import random
import re
import shutil
import signal
import subprocess
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from constants import (
    CACHE_DIR, WORKSHOP_DIR, UNSUBSCRIBE_QUEUE_FILE,
    T_GAME, T_MOVIE, T_SHOW, T_WALLPAPER, T_YOUTUBE, T_BOOK,
    S_INSTALLED, S_DOWNLOADING, S_NOT_INSTALLED,
    SEARCH_DEBOUNCE_MS, toggle_theme, reload_theme,
)
from config import jellyfin_cfg, qbit_cfg
from cache import download_image, load_queue, save_queue
from ui.grid import HiveGrid
from data.steam import (
    get_steam_games, fetch_cdn_artwork, fetch_store_name,
    name_cache_load, name_cache_save, game_state, SKIP_NAMES,
)
from data.movies import get_local_movies
from data.shows import get_local_shows
from data.wallpapers import get_wallpapers, get_active_wallpaper_id, flush_unsubscribe_queue
from data.youtube import get_youtube_videos
from search import tmdb, openlibrary, steam_store
from search.qbit import (
    search as qbit_search, add_torrent, build_torrent_queries,
    fmt_size, show_sort_key, engine_label, login as qbit_login,
)


def _try_launch(commands, not_found_msg):
    for cmd in commands:
        try:
            subprocess.Popen(cmd)
            return
        except FileNotFoundError:
            continue
    print(not_found_msg)


def _spawn(func, *args):
    threading.Thread(target=func, args=args, daemon=True).start()


class TheHive(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="The Hive")
        self.set_default_size(1280, 800)
        self.set_decorated(False)
        self.set_cursor(Gdk.Cursor.new_from_name("none", None))
        _spawn(flush_unsubscribe_queue)

        css = Gtk.CssProvider()
        css.load_from_data(b"window, box, scrolledwindow, viewport { background-color: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._all_items       = []
        self._search_timer_id = None

        self.grid = HiveGrid([], self._dispatch_launch, self._unsubscribe_wallpaper)

        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_vexpand(True)
        grid_scroll.set_hexpand(True)
        grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        grid_scroll.set_child(self.grid)

        self.set_child(grid_scroll)
        self.grid.grab_focus()

        key = Gtk.EventControllerKey()
        key.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._on_sigusr1)

        _spawn(self._load_all_async)
        GLib.timeout_add_seconds(5, self._poll_game_states)

    def _on_sigusr1(self):
        reload_theme()
        self.grid.queue_draw()
        return True

    # ── Data loading ───────────────────────────────────────────────────

    def _load_all_async(self):
        LOADERS = [
            ('yt',     get_youtube_videos),
            ('walls',  get_wallpapers),
            ('movies', get_local_movies),
            ('shows',  get_local_shows),
            ('steam',  get_steam_games),
        ]
        results = {}
        ordered = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(fn): key for key, fn in LOADERS}
            for future in as_completed(futures):
                key = futures[future]
                results[key] = future.result()
                ordered = [item for k, _ in LOADERS if k in results for item in results[k]]
                GLib.idle_add(self._on_data_loaded, ordered, priority=GLib.PRIORITY_HIGH_IDLE)

        yt        = results.get('yt', [])
        steam     = results.get('steam', [])
        all_items = ordered

        missing_thumbs = [v for v in yt if not v['artwork']]
        if missing_thumbs:
            _spawn(self._fetch_yt_thumbnails, missing_thumbs)

        missing_art = [g for g in steam if not g['artwork']]
        if missing_art:
            _spawn(self._fetch_cdn_art, missing_art)

        unnamed = [g for g in steam if g['name'] == g['data']]
        if unnamed:
            _spawn(self._fetch_missing_names, unnamed)

        yt_items = [i for i in all_items if i['type'] == T_YOUTUBE]
        if yt_items:
            _spawn(self._fetch_yt_animated_previews, yt_items)

    def _on_data_loaded(self, all_items):
        self._all_items = all_items
        if not self.grid.query:
            sel = self.grid.selected if self.grid.all_items else 0
            self.grid.set_items(all_items, selected=sel, keep_cache=bool(self.grid.all_items))
        return False

    def _poll_game_states(self):
        changed = False
        for item in self._all_items:
            if item['type'] == T_GAME:
                new_state = game_state(item['data'])
                if new_state != item.get('state'):
                    item['state'] = new_state
                    changed = True
        if changed:
            self.grid.queue_draw()
        return True

    # ── Background art fetchers ────────────────────────────────────────

    def _fetch_cdn_art(self, games):
        def fetch_one(game):
            path = fetch_cdn_artwork(game['data'])
            if path:
                game['artwork'] = path
                try:
                    return id(game), GdkPixbuf.Pixbuf.new_from_file(path)
                except Exception:
                    pass
            return None, None

        with ThreadPoolExecutor(max_workers=10) as ex:
            for future in as_completed(ex.submit(fetch_one, g) for g in games):
                item_id, pb = future.result()
                if pb:
                    GLib.idle_add(self.grid._store_pixbuf, item_id, pb)

    def _fetch_missing_names(self, games):
        cache   = name_cache_load()
        updated = False
        for game in games:
            appid  = game['data']
            result = fetch_store_name(appid)
            if result:
                _, name, typ = result
                cache[appid] = {'name': name, 'type': typ}
                updated = True
                if typ == 'game' and not name.startswith(SKIP_NAMES):
                    game['name'] = name
                    GLib.idle_add(self.grid.queue_draw)
        if updated:
            name_cache_save(cache)

    def _fetch_yt_thumbnails(self, videos):
        def fetch_one(item):
            vid_id = item['data']
            path   = CACHE_DIR / f'yt_{vid_id}.jpg'
            if not path.exists():
                if not download_image(
                    f'https://i.ytimg.com/vi/{vid_id}/maxresdefault.jpg', path
                ):
                    return None, None
            item['artwork'] = str(path)
            try:
                return id(item), GdkPixbuf.Pixbuf.new_from_file(str(path))
            except Exception:
                return None, None

        with ThreadPoolExecutor(max_workers=8) as ex:
            for future in as_completed(ex.submit(fetch_one, v) for v in videos):
                item_id, pb = future.result()
                if pb:
                    GLib.idle_add(self.grid._store_pixbuf, item_id, pb)

    def _fetch_yt_animated_previews(self, items):
        def fetch_one(item):
            vid_id = item['data']
            path   = CACHE_DIR / f'yt_anim_{vid_id}.webp'
            if not path.exists():
                download_image(
                    f'https://i.ytimg.com/vi/{vid_id}/mqdefault_6s.webp', path
                )

        with ThreadPoolExecutor(max_workers=4) as ex:
            for _ in as_completed(ex.submit(fetch_one, i) for i in items):
                pass

    # ── Launch ─────────────────────────────────────────────────────────

    def _dispatch_launch(self, item):
        state = item.get('state', S_INSTALLED)
        if state == S_DOWNLOADING:
            return
        if state == S_NOT_INSTALLED:
            self._start_download(item)
            item['state'] = S_DOWNLOADING
            self.grid.search.clear()
            self.grid.queue_draw()
            return
        dispatch = {
            T_GAME:     lambda: self._launch_game(item['data']),
            T_MOVIE:    lambda: self._launch_movie(item['data']),
            T_SHOW:     lambda: self._launch_movie(item['data']),
            T_YOUTUBE:  lambda: self._launch_youtube(item['data']),
            T_WALLPAPER: lambda: self._launch_wallpaper_item(item),
        }
        action = dispatch.get(item['type'])
        if action:
            action()

    def _start_download(self, item):
        if item['type'] == T_GAME:
            subprocess.Popen(['steam', f'steam://install/{item["data"]}'])
        elif item['type'] in (T_MOVIE, T_SHOW, T_BOOK):
            try:
                add_torrent(item['data'])
            except Exception as e:
                print(f"qBit add failed: {e}")

    def _launch_game(self, appid):
        subprocess.Popen(['steam', f'steam://rungameid/{appid}'])
        self.close()

    def _launch_movie(self, data):
        if data.startswith('/'):
            _try_launch([[p, data] for p in ['mpv', 'vlc']], "No video player found")
            self.close()
            return
        url, api_key = jellyfin_cfg()
        if not api_key:
            print("No Jellyfin API key configured")
            self.close()
            return
        _try_launch([[p, f"{url}/Items/{data}/Download?api_key={api_key}"] for p in ['mpv', 'vlc']], "No video player found")
        self.close()

    def _launch_youtube(self, video_id):
        subprocess.Popen(['xdg-open', f'https://www.youtube.com/watch?v={video_id}'])
        self.close()

    def _launch_wallpaper(self, workshop_id):
        subprocess.run(['pkill', '-f', 'linux-wallpaperengine'])
        result = subprocess.run(['hyprctl', 'monitors'], capture_output=True, text=True)
        monitors = re.findall(r'^Monitor (\S+)', result.stdout, re.MULTILINE)
        if not monitors:
            import os
            monitors = [os.environ.get('MONITOR', 'eDP-1')]
        for monitor in monitors:
            subprocess.Popen([
                'linux-wallpaperengine', '--screen-root', monitor,
                '--bg', workshop_id, '--fps', '30',
                '--scaling', 'stretch', '--silent', '--disable-mouse', '--no-fullscreen-pause'
            ], start_new_session=True)
        (CACHE_DIR / 'last_wallpaper').write_text(workshop_id)

    def _launch_wallpaper_item(self, item):
        self._launch_wallpaper(item['data'])
        self.close()

    def _unsubscribe_wallpaper(self, slot, workshop_id):
        active_id = get_active_wallpaper_id()
        queue = load_queue(UNSUBSCRIBE_QUEUE_FILE)
        if workshop_id not in queue:
            queue.append(workshop_id)
        save_queue(UNSUBSCRIBE_QUEUE_FILE, queue)
        wp_dir = WORKSHOP_DIR / workshop_id
        if wp_dir.exists():
            _spawn(shutil.rmtree, wp_dir)
        if active_id == workshop_id:
            candidates = [i for i in self.grid.all_items if i['type'] == T_WALLPAPER and i['data'] not in queue]
            if candidates:
                self._launch_wallpaper(random.choice(candidates)['data'])
        self.grid.remove_visible(slot)

    # ── Omni Search ────────────────────────────────────────────────────

    def _schedule_media_search(self, query):
        if self._search_timer_id is not None:
            GLib.source_remove(self._search_timer_id)
        self._search_timer_id = GLib.timeout_add(SEARCH_DEBOUNCE_MS, self._fire_media_search, query)

    def _fire_media_search(self, query):
        self._search_timer_id = None
        if query and query == self.grid.query:
            self._run_media_search_async(query)
        return False

    def _run_media_search_async(self, query):
        self.grid.search.set_loading(query)
        _spawn(self._run_media_search, query)

    def _run_media_search(self, query):
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_tmdb  = ex.submit(tmdb.search, query)
            f_ol    = ex.submit(openlibrary.search, query)
            f_steam = ex.submit(steam_store.search, query)
            tmdb_results  = f_tmdb.result()
            ol_results    = f_ol.result()
            steam_results = f_steam.result()

        # Expand TV shows → season tiles; include movies alongside
        shows  = [r for r in tmdb_results if r.get('type') == T_SHOW]
        movies = [r for r in tmdb_results if r.get('type') != T_SHOW]
        expanded = []
        if shows:
            with ThreadPoolExecutor(max_workers=len(shows)) as ex:
                futures = {ex.submit(tmdb.fetch_seasons, s): s for s in shows}
                for future in as_completed(futures):
                    seasons = future.result()
                    expanded.extend(seasons if seasons else [futures[future]])

        results = expanded + movies + ol_results + steam_results

        if not results:
            GLib.idle_add(self.grid.search.set_loading_done)
            return

        GLib.idle_add(self.grid.search.set_loading_done)
        GLib.idle_add(self.grid.show_results, results)
        for item in results:
            if item.get('artwork'):
                _spawn(self._preload_sr_artwork, item)
            elif item.get('artwork_url'):
                _spawn(self._fetch_sr_artwork, item)

    def _fetch_seasons_async(self, show_item):
        self.grid.search.set_loading(show_item['name'])
        _spawn(self._run_fetch_seasons, show_item)

    def _run_fetch_seasons(self, show_item):
        seasons = tmdb.fetch_seasons(show_item)
        if seasons:
            GLib.idle_add(self.grid.search.set_loading_done)
            GLib.idle_add(self.grid.show_results, seasons)
            for s in seasons:
                if s.get('artwork_url') and not s.get('artwork'):
                    _spawn(self._fetch_sr_artwork, s)
        else:
            GLib.idle_add(self._trigger_torrent_search, show_item)

    def _trigger_torrent_search(self, query_or_item):
        if isinstance(query_or_item, dict):
            queries     = build_torrent_queries(query_or_item)
            item_type   = query_or_item.get('type', T_MOVIE)
            artwork_url = query_or_item.get('artwork_url')
            artwork     = query_or_item.get('artwork')
        else:
            queries     = [query_or_item]
            item_type   = T_MOVIE
            artwork_url = None
            artwork     = None
        self.grid.search.set_loading(queries[0])
        _spawn(self._run_torrent_search, queries, item_type, artwork_url, artwork)

    def _execute_torrent_queries(self, queries, category):
        if len(queries) == 1:
            return qbit_search(queries[0], category)
        seen, raw = set(), []
        with ThreadPoolExecutor(max_workers=len(queries)) as ex:
            for results in ex.map(lambda q: qbit_search(q, category), queries):
                for r in results:
                    key = r.get('fileUrl') or r.get('fileName', '')
                    if key not in seen:
                        seen.add(key)
                        raw.append(r)
        return raw

    def _format_torrent_results(self, raw, item_type, artwork, artwork_url):
        if item_type == T_SHOW:
            raw.sort(key=lambda r: (show_sort_key(r.get('fileName', '')), -r.get('nbSeeders', 0)))
        else:
            raw.sort(key=lambda r: r.get('nbSeeders', 0), reverse=True)
        return [
            {
                'type':        item_type,
                'name':        r['fileName'],
                'artwork':     artwork,
                'artwork_url': artwork_url,
                'data':        r['fileUrl'],
                'state':       S_NOT_INSTALLED,
                'size':        fmt_size(r),
                'seeders':     r.get('nbSeeders', 0),
                'source':      engine_label(r.get('engineName', '')),
            }
            for r in raw[:10] if r.get('fileName') and r.get('fileUrl')
        ]

    def _run_torrent_search(self, queries, item_type=T_MOVIE, artwork_url=None, artwork=None):
        category = 'books' if item_type == T_BOOK else 'all'
        try:
            raw = self._execute_torrent_queries(queries, category)
        except Exception as e:
            print(f"qBit search failed: {e}")
            GLib.idle_add(self.grid.search.set_loading_done)
            return
        results = self._format_torrent_results(raw, item_type, artwork, artwork_url)
        GLib.idle_add(self.grid.search.set_loading_done)
        GLib.idle_add(self.grid.show_results, results)
        if artwork_url and not artwork:
            _spawn(self._fetch_shared_artwork, results, artwork_url)

    # ── Search artwork ─────────────────────────────────────────────────

    def _preload_sr_artwork(self, item):
        path = item.get('artwork')
        if not path:
            return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(self.grid._store_pixbuf, id(item), pb)
        except Exception:
            pass

    def _fetch_sr_artwork(self, item):
        art_url    = item['artwork_url']
        cache_path = CACHE_DIR / f"sr_{abs(hash(art_url))}.jpg"
        if not cache_path.exists():
            urls = [art_url]
            if item.get('type') == T_GAME and 'library_600x900' in art_url:
                appid = item.get('data', '')
                urls.append(f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg")
            if not download_image(urls, cache_path):
                return
        item['artwork'] = str(cache_path)
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(cache_path))
            GLib.idle_add(self.grid._store_pixbuf, id(item), pb)
        except Exception:
            pass

    def _fetch_shared_artwork(self, items, art_url):
        cache_path = CACHE_DIR / f"sr_{abs(hash(art_url))}.jpg"
        if not cache_path.exists():
            if not download_image(art_url, cache_path):
                return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(str(cache_path))
        except Exception:
            return
        for item in items:
            item['artwork'] = str(cache_path)
            GLib.idle_add(self.grid._store_pixbuf, id(item), pb)

    # ── Key input ──────────────────────────────────────────────────────

    def _on_key_pressed(self, controller, keyval, keycode, state):
        in_search = self.grid.search.is_active()

        _DISPATCH = {
            Gdk.KEY_Escape:   lambda: self._handle_escape(in_search),
            Gdk.KEY_Return:   lambda: self._handle_return(in_search),
            Gdk.KEY_KP_Enter: lambda: self._handle_return(in_search),
            Gdk.KEY_Up:    lambda: self.grid.search.navigate('up',    *self.grid._viewport_size()) if in_search else self.grid.navigate('up'),
            Gdk.KEY_Down:  lambda: self.grid.search.navigate('down',  *self.grid._viewport_size()) if in_search else self.grid.navigate('down'),
            Gdk.KEY_Left:  lambda: self.grid.search.navigate('left',  *self.grid._viewport_size()) if in_search else self.grid.navigate('left'),
            Gdk.KEY_Right: lambda: self.grid.search.navigate('right', *self.grid._viewport_size()) if in_search else self.grid.navigate('right'),
        }

        if (state & Gdk.ModifierType.CONTROL_MASK) and not self.grid.query:
            if keyval == Gdk.KEY_Up:
                self.grid.adjust_petal_rings(1)
                return True
            if keyval == Gdk.KEY_Down:
                self.grid.adjust_petal_rings(-1)
                return True
            if keyval == Gdk.KEY_t:
                toggle_theme()
                self.grid.queue_draw()
                return True

        if keyval == Gdk.KEY_BackSpace and (state & Gdk.ModifierType.SUPER_MASK):
            item, slot = self.grid.get_selected()
            if item and item['type'] == T_WALLPAPER:
                self._unsubscribe_wallpaper(slot, item['data'])
            return True

        if keyval == Gdk.KEY_BackSpace and self.grid.query:
            new_q = self.grid.query[:-1]
            self.grid.filter(new_q)
            if not new_q:
                self.grid.search.clear()
            return True

        if keyval in _DISPATCH:
            _DISPATCH[keyval]()
            return True

        if 32 <= keyval <= 126 and not (state & Gdk.ModifierType.CONTROL_MASK):
            if self.grid._online_mode:
                return True  # swallow — don't mutate query while results showing
            if in_search:
                self.grid.search.clear()
            self.grid.filter(self.grid.query + chr(keyval))
            return True

        return False

    def _handle_escape(self, in_search):
        if self.grid._online_mode:
            self.grid.clear_results()
            self.grid.search.clear()
        elif in_search:
            self.grid.search.clear()
        elif self.grid.query:
            self.grid.filter("")
        else:
            self.close()

    def _handle_return(self, in_search):
        if self.grid._online_mode:
            item, _ = self.grid.get_selected()
            if item:
                sel = item
                if sel.get('type') == T_SHOW and not sel.get('season_result'):
                    self._fetch_seasons_async(sel)
                elif sel.get('media_result') and sel['type'] == T_GAME:
                    self._dispatch_launch(sel)
                else:
                    self._dispatch_launch(sel)
            return

        if not in_search:
            if self.grid.query:
                self._run_media_search_async(self.grid.query)
            elif self.grid.visible:
                self.grid.activate()
            return

        sel = self.grid.search.get_selected_item()
        if not sel:
            self.grid.search.activate(self._dispatch_launch)
            return
        if sel.get('type') == T_SHOW and not sel.get('season_result'):
            self._fetch_seasons_async(sel)
        elif sel.get('media_result') and sel['type'] == T_GAME:
            self._dispatch_launch(sel)
