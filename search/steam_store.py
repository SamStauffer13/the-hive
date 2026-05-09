import json
import urllib.request
import urllib.parse
from constants import T_GAME, S_NOT_INSTALLED
from data.steam import local_artwork, game_state, SKIP_NAMES


def search(query):
    out = []
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://store.steampowered.com/api/storesearch/?term={encoded}&cc=US&l=en",
            headers={'User-Agent': 'the-hive/2.0'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            items = json.loads(r.read()).get('items', [])
        for item in items[:9]:
            if item.get('type') not in ('game', 'app'):
                continue
            appid = str(item['id'])
            name  = item.get('name', '')
            if not name or name.startswith(SKIP_NAMES):
                continue
            out.append({
                'type':         T_GAME,
                'name':         name,
                'year':         None,
                'artwork':      local_artwork(appid),
                'artwork_url':  f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
                'data':         appid,
                'state':        game_state(appid),
                'media_result': True,
                'source':       'Steam',
            })
    except Exception as e:
        print(f"Steam store search failed: {e}")
    return out
