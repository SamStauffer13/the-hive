import json
import re
import subprocess
from constants import WORKSHOP_DIR, CACHE_DIR, UNSUBSCRIBE_QUEUE_FILE, T_WALLPAPER, S_INSTALLED
from cache import load_queue


def get_wallpapers():
    supported = {'scene', 'video'}
    wallpapers = []
    if not WORKSHOP_DIR.exists():
        return wallpapers
    for wp_dir in sorted(WORKSHOP_DIR.iterdir()):
        project = wp_dir / "project.json"
        if not project.exists():
            continue
        try:
            data    = json.loads(project.read_text())
            wp_type = (data.get('type') or '').lower()
            if wp_type not in supported:
                continue
            preview_path = wp_dir / data.get('preview', 'preview.jpg')
            wallpapers.append({
                'type':    T_WALLPAPER,
                'name':    data.get('title', wp_dir.name),
                'artwork': str(preview_path) if preview_path.exists() else None,
                'data':    wp_dir.name,
                'wp_type': wp_type,
                'state':   S_INSTALLED,
            })
        except Exception as e:
            print(f"Error reading wallpaper {wp_dir.name}: {e}")
    return sorted(wallpapers, key=lambda w: w['name'].lower())


def get_active_wallpaper_id():
    result = subprocess.run(
        ['ps', '-C', 'linux-wallpaperengine', '-o', 'args='],
        capture_output=True, text=True
    )
    match = re.search(r'--bg\s+(\S+)', result.stdout)
    return match.group(1) if match else None


def flush_unsubscribe_queue():
    if not UNSUBSCRIBE_QUEUE_FILE.exists():
        return
    if subprocess.run(['pgrep', '-x', 'steam'], capture_output=True).returncode != 0:
        return
    queue = load_queue(UNSUBSCRIBE_QUEUE_FILE)
    if not queue:
        return
    print(f"Steam running, flushing {len(queue)} queued unsubscribe(s)")
    for workshop_id in queue:
        subprocess.Popen(['steam', '-silent', f'steam://unsubscribe/{workshop_id}'])
    UNSUBSCRIBE_QUEUE_FILE.unlink()
