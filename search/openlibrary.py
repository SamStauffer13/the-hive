import json
import urllib.request
import urllib.parse
from constants import T_BOOK, S_NOT_INSTALLED


def search(query):
    out = []
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://openlibrary.org/search.json?q={encoded}&limit=9&fields=title,author_name,first_publish_year,cover_i",
            headers={'User-Agent': 'the-hive/2.0'},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            docs = json.loads(r.read()).get('docs', [])
        for item in docs[:9]:
            name = item.get('title', '')
            if not name:
                continue
            authors  = item.get('author_name', [])
            cover_id = item.get('cover_i')
            out.append({
                'type':         T_BOOK,
                'name':         name,
                'year':         item.get('first_publish_year'),
                'author':       authors[0] if authors else '',
                'artwork':      None,
                'artwork_url':  f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None,
                'data':         None,
                'state':        S_NOT_INSTALLED,
                'media_result': True,
            })
    except Exception as e:
        print(f"Open Library search failed: {e}")
    return out
