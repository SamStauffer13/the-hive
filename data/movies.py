import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from constants import CACHE_DIR, T_MOVIE, S_INSTALLED
from config import jellyfin_cfg, movies_dir
from . import scan_video_dir


def _jellyfin_poster(url, api_key, item_id):
    cached = CACHE_DIR / f"jf_{item_id}.jpg"
    if cached.exists():
        return str(cached)
    try:
        req = urllib.request.Request(
            f"{url}/Items/{item_id}/Images/Primary?fillWidth=400&api_key={api_key}"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            cached.write_bytes(r.read())
        return str(cached)
    except Exception:
        return None


def get_local_movies():
    url, api_key = jellyfin_cfg()
    if api_key:
        try:
            req = urllib.request.Request(
                f"{url}/Items?recursive=true&IncludeItemTypes=Movie&Fields=Path&api_key={api_key}"
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                items = json.loads(r.read()).get('Items', [])
            with ThreadPoolExecutor(max_workers=10) as ex:
                futures = [(ex.submit(_jellyfin_poster, url, api_key, item['Id']), item) for item in items]
            return sorted(
                [{'type': T_MOVIE, 'name': item['Name'], 'artwork': f.result(),
                  'data': item['Id'], 'path': item.get('Path'), 'state': S_INSTALLED}
                 for f, item in futures],
                key=lambda m: m['name'].lower()
            )
        except Exception as e:
            print(f"Jellyfin unavailable, falling back to local scan: {e}")

    return sorted(scan_video_dir(movies_dir(), T_MOVIE), key=lambda m: m['name'].lower())
