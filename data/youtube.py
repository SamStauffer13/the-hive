import json
import pickle
import re
import shutil
import sqlite3
import time
import urllib.request
from pathlib import Path
from constants import CACHE_DIR, T_YOUTUBE, S_INSTALLED

YT_CACHE_TTL = 30 * 60  # seconds


def _read_zen_cookies():
    zen_dir      = Path.home() / '.var/app/app.zen_browser.zen/.zen'
    cookie_files = sorted(zen_dir.glob('*/cookies.sqlite'))
    if not cookie_files:
        return ''
    tmp = CACHE_DIR / 'zen_cookies_tmp.sqlite'
    shutil.copy2(cookie_files[0], tmp)
    pairs = []
    try:
        conn = sqlite3.connect(tmp)
        for name, value in conn.execute(
            "SELECT name, value FROM moz_cookies WHERE host IN ('.youtube.com', 'youtube.com')"
        ):
            pairs.append(f'{name}={value}')
        conn.close()
    finally:
        tmp.unlink(missing_ok=True)
    return '; '.join(pairs)


def _fetch_yt_initial_data(cookie_str):
    req = urllib.request.Request(
        'https://www.youtube.com/',
        headers={
            'Cookie':          cookie_str,
            'User-Agent':      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8', errors='replace')
    m = re.search(r'var ytInitialData\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
    if not m:
        raise ValueError('ytInitialData not found in YouTube homepage')
    return json.loads(m.group(1))


def _extract_yt_videos(data):
    videos, seen = [], set()

    def walk(obj):
        if isinstance(obj, dict):
            lvm = obj.get('lockupViewModel')
            if lvm and lvm.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
                vid_id = lvm.get('contentId')
                if vid_id and vid_id not in seen:
                    seen.add(vid_id)
                    try:
                        title = lvm['metadata']['lockupMetadataViewModel']['title']['content']
                    except (KeyError, TypeError):
                        title = ''
                    if title:
                        videos.append({'video_id': vid_id, 'title': title})
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return videos[:50]


def get_youtube_videos():
    cache_file = CACHE_DIR / 'youtube_cache.pkl'
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < YT_CACHE_TTL:
                return pickle.load(cache_file.open('rb'))
        except Exception:
            pass

    cookie_str = _read_zen_cookies()
    if not cookie_str:
        print('YouTube: no Zen cookies found')
        return []
    try:
        raw = _extract_yt_videos(_fetch_yt_initial_data(cookie_str))
    except Exception as e:
        print(f'YouTube fetch failed: {e}')
        return []

    new_ids = {v['video_id'] for v in raw}
    for stale in CACHE_DIR.glob('yt_*.jpg'):
        if stale.stem[3:] not in new_ids:
            stale.unlink(missing_ok=True)

    items = []
    for v in raw:
        vid_id     = v['video_id']
        thumb_path = CACHE_DIR / f'yt_{vid_id}.jpg'
        items.append({
            'type':    T_YOUTUBE,
            'name':    v['title'],
            'artwork': str(thumb_path) if thumb_path.exists() else None,
            'data':    vid_id,
            'state':   S_INSTALLED,
        })
    try:
        pickle.dump(items, cache_file.open('wb'))
    except Exception:
        pass
    return items
