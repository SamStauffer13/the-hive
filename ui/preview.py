import math
import subprocess
import threading
import urllib.request
from pathlib import Path
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib
from constants import CACHE_DIR, T_YOUTUBE, PREVIEW_N
from config import jellyfin_cfg

# yt-dlp sb0 storyboard geometry: 320x180 frames in a 3×3 grid per tile
_SB_FW, _SB_FH, _SB_COLS, _SB_ROWS = 320, 180, 3, 3
_SB_FPT = _SB_COLS * _SB_ROWS   # frames per tile = 9

# Playback timing at ~20fps pulse: hold 1 tick (50ms) + fade 2 ticks (100ms) = ~6.7fps
_HOLD_TICKS = 1
_FADE_TICKS = 2


def _preview_source(item):
    data = item.get('data') or ''
    if not data or item['type'] == T_YOUTUBE:
        return None
    if data.startswith('/'):
        return data
    local = item.get('path') or ''
    if local and Path(local).exists():
        return local
    url, api_key = jellyfin_cfg()
    if api_key:
        return f"{url}/Videos/{data}/stream?static=true&api_key={api_key}"
    return None


def _preview_paths(item):
    key = abs(hash(item.get('data') or ''))
    return [CACHE_DIR / f'preview_{key}_{i:02d}.jpg' for i in range(PREVIEW_N)]


def _extract_yt_storyboard(item):
    vid_id = item['data']
    key    = abs(hash(vid_id))

    try:
        result = subprocess.run(
            ['yt-dlp', '--get-url', '-f', 'sb0', '--no-warnings',
             f'https://www.youtube.com/watch?v={vid_id}'],
            capture_output=True, text=True, timeout=20
        )
    except Exception as e:
        print(f'yt-dlp failed: {e}')
        return []

    url_template = result.stdout.strip()
    if not url_template:
        return []

    all_outs     = [CACHE_DIR / f'preview_{key}_{i:02d}.jpg' for i in range(PREVIEW_N)]
    tiles_needed = math.ceil(PREVIEW_N / _SB_FPT)

    for t in range(tiles_needed):
        tile_path   = CACHE_DIR / f'yt_sb_{key}_t{t}.webp'
        frame_start = t * _SB_FPT
        tile_outs   = all_outs[frame_start : frame_start + _SB_FPT]

        if not tile_path.exists():
            try:
                req = urllib.request.Request(
                    url_template.replace('$M', str(t)),
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    tile_path.write_bytes(r.read())
            except Exception as e:
                print(f'Storyboard tile {t} download failed: {e}')
                break

        n       = len(tile_outs)
        missing = [i for i, p in enumerate(tile_outs) if not p.exists() or p.stat().st_size < 500]
        if missing:
            split = ''.join(f'[s{i}]' for i in range(n))
            crops = [f'[s{i}]crop={_SB_FW}:{_SB_FH}:{(i%_SB_COLS)*_SB_FW}:{(i//_SB_COLS)*_SB_FH}[o{i}]'
                     for i in range(n)]
            filt  = f'[0:v]split={n}{split};' + ';'.join(crops)
            maps  = sum([['-map', f'[o{i}]', '-q:v', '4', str(tile_outs[i]), '-y']
                         for i in missing], [])
            subprocess.run(
                ['ffmpeg', '-i', str(tile_path), '-filter_complex', filt] + maps,
                capture_output=True, timeout=30
            )

    return [p for p in all_outs if p.exists() and p.stat().st_size > 500]


def extract_frames(item):
    if item['type'] == T_YOUTUBE:
        return _extract_yt_storyboard(item)
    src = _preview_source(item)
    if not src:
        return []
    key = abs(hash(item.get('data') or ''))
    out = str(CACHE_DIR / f'preview_{key}_%02d.jpg')
    try:
        subprocess.run(
            ['ffmpeg', '-ss', '00:00:30', '-i', src,
             '-vf', 'fps=5,scale=320:-2',
             '-frames:v', str(PREVIEW_N),
             '-q:v', '5', '-start_number', '0', out, '-y'],
            capture_output=True, timeout=30
        )
    except Exception as e:
        print(f'Preview extract failed: {e}')
        return []
    return [p for p in _preview_paths(item) if p.exists() and p.stat().st_size > 500]


class PreviewManager:
    def __init__(self, on_update):
        self._on_update = on_update
        self.slot       = -1
        self._pbs       = []
        self._idx       = 0
        self._tick      = 0
        self._fading    = False

    def start(self, item, slot):
        self.slot = slot
        cached = [p for p in _preview_paths(item) if p.exists() and p.stat().st_size > 500]
        if len(cached) >= PREVIEW_N:
            self._load_files(cached, slot)
        else:
            threading.Thread(target=self._extract_and_load, args=(item, slot), daemon=True).start()

    def stop(self):
        self._pbs    = []
        self._idx    = 0
        self._tick   = 0
        self._fading = False
        self.slot    = -1

    def pulse(self):
        """Called at ~20fps. Returns True to keep GLib timer alive."""
        if not self._pbs:
            return True
        self._tick += 1
        if not self._fading:
            if self._tick >= _HOLD_TICKS:
                self._tick   = 0
                self._fading = True
                self._on_update()
        else:
            self._on_update()
            if self._tick >= _FADE_TICKS:
                self._idx    = (self._idx + 1) % len(self._pbs)
                self._tick   = 0
                self._fading = False
        return True

    def current_frame(self):
        """Returns (pb_cur, pb_next, fade_alpha).
        pb_next is None and fade_alpha is 1.0 when holding."""
        if not self._pbs:
            return None, None, 1.0
        pb_cur = self._pbs[self._idx % len(self._pbs)]
        if self._fading:
            pb_next    = self._pbs[(self._idx + 1) % len(self._pbs)]
            fade_alpha = min(1.0, self._tick / _FADE_TICKS)
            return pb_cur, pb_next, fade_alpha
        return pb_cur, None, 1.0

    def _load_files(self, paths, slot):
        def load():
            pbs = []
            for p in paths:
                try:
                    pbs.append(GdkPixbuf.Pixbuf.new_from_file(str(p)))
                except Exception:
                    pass
            if pbs:
                GLib.idle_add(self._set_pbs, pbs, slot)
        threading.Thread(target=load, daemon=True).start()

    def _set_pbs(self, pbs, slot):
        if self.slot != slot:
            return False
        self._pbs  = pbs
        self._idx  = 0
        self._tick = 0
        self._on_update()
        return False

    def _extract_and_load(self, item, slot):
        paths = extract_frames(item)
        if paths and self.slot == slot:
            self._load_files(paths, slot)
