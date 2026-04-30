import json
import re
import time
import http.cookiejar
import urllib.request
import urllib.parse
from constants import T_BOOK

QBIT_URL     = 'http://127.0.0.1:8080'
_jar         = http.cookiejar.CookieJar()
_opener      = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))

_ENGINE_LABELS = {'piratebay': 'TPB', 'yts': 'YTS', 'kickasstorrents': 'KAT'}
_COMPLETE_KWS  = ('complete', 'complete.series', 'the.complete', 's01-s', 'season.1-')


def _request(path, data=None):
    req = urllib.request.Request(f"{QBIT_URL}{path}", data=data)
    req.add_header('Referer', QBIT_URL)
    with _opener.open(req, timeout=8) as r:
        return r.read()


def login(user='admin', pw='adminadmin'):
    data = urllib.parse.urlencode({'username': user, 'password': pw}).encode()
    return _request('/api/v2/auth/login', data) == b'Ok.'


def search(query, category='all'):
    login()
    q = urllib.parse.urlencode({'pattern': query, 'plugins': 'all', 'category': category}).encode()
    search_id = json.loads(_request('/api/v2/search/start', q))['id']
    for _ in range(20):
        time.sleep(1)
        resp = json.loads(_request(f'/api/v2/search/results?id={search_id}&limit=20'))
        if resp.get('status') == 'Stopped' or resp.get('results'):
            results = resp.get('results', [])
            _request('/api/v2/search/delete', f'id={search_id}'.encode())
            return results
    return []


def add_torrent(url):
    data = urllib.parse.urlencode({'urls': url}).encode()
    _request('/api/v2/torrents/add', data)


def fmt_size(r):
    size = r.get('fileSize', -1)
    if size and size > 0:
        return f"{size/1_000_000_000:.1f}GB" if size >= 1_000_000_000 else f"{size/1_000_000:.0f}MB"
    return ''


def engine_label(name):
    return _ENGINE_LABELS.get(name, name[:3].upper() if name else '')


def build_torrent_queries(item):
    name = item['name']
    year = item.get('year')
    if item.get('season_result'):
        show = item['show_name']
        sn   = item['season_number']
        if sn == 0:
            return [f"{show} complete series", show]
        return [f"{show} S{sn:02d}", f"{show} Season {sn}"]
    if item.get('type') == T_BOOK:
        author = item.get('author', '')
        return [f"{name} {author}".strip() if author else name, name]
    return [f"{name} {year}" if year else name]


def show_sort_key(filename):
    f = filename.lower().replace(' ', '.')
    if any(k in f for k in _COMPLETE_KWS):
        return 0
    m = re.search(r's(\d{1,2})e', f) or re.search(r'season[.\s](\d{1,2})', f) or re.search(r's(\d{2})', f)
    return int(m.group(1)) + 1 if m else 99
