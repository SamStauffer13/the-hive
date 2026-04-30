import json
import urllib.request
import urllib.parse
from constants import T_MOVIE, T_SHOW, S_NOT_INSTALLED
from config import tmdb_token


def search(query):
    token = tmdb_token()
    if not token:
        return []
    out = []
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://api.themoviedb.org/3/search/multi?query={encoded}&include_adult=false&language=en-US&page=1",
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read()).get('results', [])
        for item in data[:9]:
            media_type = item.get('media_type')
            if media_type not in ('movie', 'tv'):
                continue
            name = item.get('title') or item.get('name', '')
            if not name:
                continue
            date   = item.get('release_date') or item.get('first_air_date', '')
            year   = int(date[:4]) if date else None
            poster = item.get('poster_path')
            out.append({
                'type':         T_MOVIE if media_type == 'movie' else T_SHOW,
                'name':         name,
                'year':         year,
                'artwork':      None,
                'artwork_url':  f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
                'data':         None,
                'state':        S_NOT_INSTALLED,
                'media_result': True,
                'tmdb_id':      item.get('id'),
            })
    except Exception as e:
        print(f"TMDB search failed: {e}")
    return out


def fetch_seasons(show_item):
    token   = tmdb_token()
    tmdb_id = show_item.get('tmdb_id')
    name    = show_item['name']
    if not (token and tmdb_id):
        return []
    seasons = []
    try:
        req = urllib.request.Request(
            f"https://api.themoviedb.org/3/tv/{tmdb_id}",
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        for s in data.get('seasons', []):
            sn = s.get('season_number', 0)
            if sn == 0:
                continue
            poster = s.get('poster_path')
            date   = s.get('air_date', '')
            seasons.append({
                'type':          T_SHOW,
                'name':          f"{name} — Season {sn}",
                'year':          int(date[:4]) if date else show_item.get('year'),
                'artwork':       None,
                'artwork_url':   f"https://image.tmdb.org/t/p/w500{poster}" if poster else show_item.get('artwork_url'),
                'data':          None,
                'state':         S_NOT_INSTALLED,
                'media_result':  True,
                'season_result': True,
                'season_number': sn,
                'show_name':     name,
            })
    except Exception as e:
        print(f"TMDB season fetch failed: {e}")
    return seasons
