import json
import urllib.request
from pathlib import Path


def load_queue(path: Path) -> list:
    """Read a JSON list from path; return [] on missing/error."""
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception:
        return []


def save_queue(path: Path, queue: list):
    """Write a JSON list to path."""
    path.write_text(json.dumps(queue))


def download_image(urls, path: Path, min_size: int = 1000) -> bool:
    """Try each URL in order, save to path. Returns True on first success."""
    if isinstance(urls, str):
        urls = [urls]
    for url in urls:
        try:
            urllib.request.urlretrieve(url, path)
            if path.stat().st_size > min_size:
                return True
            path.unlink(missing_ok=True)
        except Exception:
            continue
    return False
