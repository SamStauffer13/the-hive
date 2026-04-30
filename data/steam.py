import re
import json
import urllib.request
from constants import (
    STEAM_APPS, STEAM_CACHE, CACHE_DIR, STEAM_NAMES_CACHE,
    T_GAME, S_INSTALLED, S_DOWNLOADING, S_NOT_INSTALLED,
)
from cache import download_image

_ACF_APPID  = re.compile(r'"appid"\s+"(\d+)"')
_ACF_NAME   = re.compile(r'"name"\s+"([^"]+)"')
_ACF_FLAGS  = re.compile(r'"StateFlags"\s+"(\d+)"')

SKIP_NAMES = ('Proton', 'Steam', 'Steamworks')


def game_state(appid):
    acf = STEAM_APPS / f"appmanifest_{appid}.acf"
    if not acf.exists():
        return S_NOT_INSTALLED
    try:
        m = _ACF_FLAGS.search(acf.read_text(encoding='utf-8'))
        if m and int(m.group(1)) & 4:
            return S_INSTALLED
    except Exception:
        pass
    return S_DOWNLOADING


def local_artwork(appid):
    steam_art = STEAM_CACHE / appid / "library_600x900.jpg"
    if steam_art.exists():
        return str(steam_art)
    cached = CACHE_DIR / f"{appid}.jpg"
    return str(cached) if cached.exists() else None


def fetch_cdn_artwork(appid):
    cached = CACHE_DIR / f"{appid}.jpg"
    urls = [
        f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/library_600x900.jpg",
        f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
        f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/header.jpg",
    ]
    return str(cached) if download_image(urls, cached) else None


def name_cache_load():
    try:
        return json.loads(STEAM_NAMES_CACHE.read_text()) if STEAM_NAMES_CACHE.exists() else {}
    except Exception:
        return {}


def name_cache_save(cache):
    try:
        STEAM_NAMES_CACHE.write_text(json.dumps(cache))
    except Exception as e:
        print(f"Failed to save name cache: {e}")


def fetch_store_name(appid):
    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read()).get(str(appid), {})
        if data.get('success') and data.get('data'):
            d = data['data']
            return appid, d.get('name', ''), d.get('type', 'game')
    except Exception:
        pass
    return None


def _acf_names():
    names = {}
    if not STEAM_APPS.exists():
        return names
    for manifest in STEAM_APPS.glob("appmanifest_*.acf"):
        try:
            content = manifest.read_text(encoding='utf-8')
            m_id    = _ACF_APPID.search(content)
            m_name  = _ACF_NAME.search(content)
            if m_id and m_name:
                names[m_id.group(1)] = m_name.group(1)
        except Exception:
            pass
    return names


def get_steam_games():
    app_ids = set()
    if STEAM_CACHE.exists():
        for d in STEAM_CACHE.iterdir():
            try:
                app_ids.add(str(int(d.name)))
            except ValueError:
                pass

    acf   = _acf_names()
    cache = name_cache_load()
    app_ids |= set(acf.keys())

    games = []
    for appid in app_ids:
        name = acf.get(appid) or cache.get(appid, {}).get('name', '')
        typ  = cache.get(appid, {}).get('type', '')
        if name.startswith(SKIP_NAMES):
            continue
        if typ and typ != 'game':
            continue
        if not typ:
            portrait = STEAM_CACHE / appid / "library_600x900.jpg"
            if not portrait.exists() and appid not in acf:
                continue
        games.append({
            'type':    T_GAME,
            'name':    name or appid,
            'artwork': local_artwork(appid),
            'data':    appid,
            'state':   game_state(appid),
        })

    games.sort(key=lambda g: g['name'].lower())
    return games
