import re
from pathlib import Path
from constants import VIDEO_EXTS, S_INSTALLED


def scan_video_dir(directory: Path, item_type: str, recursive: bool = False) -> list:
    """Return items for all video files under directory."""
    if not directory.exists():
        return []
    iterator = directory.rglob('*') if recursive else directory.iterdir()
    items = []
    for f in sorted(iterator):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            name = re.sub(r'\s*[\[\(].*?[\]\)]', '', f.stem).strip() or f.stem
            items.append({
                'type': item_type, 'name': name,
                'artwork': None, 'data': str(f), 'state': S_INSTALLED,
            })
    return items
