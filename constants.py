from pathlib import Path

# Paths
STEAM_APPS             = Path.home() / ".local/share/Steam/steamapps"
STEAM_CACHE            = Path.home() / ".steam/steam/appcache/librarycache"
CACHE_DIR              = Path.home() / ".cache/the-hive"
UNSUBSCRIBE_QUEUE_FILE = CACHE_DIR / "unsubscribe_queue.json"
STEAM_NAMES_CACHE      = CACHE_DIR / "steam_names.json"
WORKSHOP_DIR           = Path.home() / ".steam/steam/steamapps/workshop/content/431960"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Item types
T_GAME      = 'game'
T_MOVIE     = 'movie'
T_SHOW      = 'show'
T_WALLPAPER = 'wallpaper'
T_YOUTUBE   = 'youtube'
T_BOOK      = 'book'

# Item states
S_INSTALLED     = 'installed'
S_DOWNLOADING   = 'downloading'
S_NOT_INSTALLED = 'not_installed'

# Aurelia draw colors (Cairo RGBA)
C_BG_DARK = (0.04, 0.04, 0.05, 1.0)
C_PINK    = (0.92, 0.33, 0.62, 1.0)

# Draw parameters
ITEM_SCALE_SELECTED    = 1.15
ITEM_ALPHA_UNSELECTED  = 0.50
ALPHA_DOWNLOADING_BASE = 0.35
PETAL_RINGS            = 2      # rings of petals shown around selected cell (1=6, 2=18, 3=36)
SEARCH_DEBOUNCE_MS     = 400

# Video file extensions
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm'}

# Preview animation
PREVIEW_N = 27  # frames extracted per clip (3 YouTube storyboard tiles × 9 frames)

VIDEO_TYPES = {T_MOVIE, T_SHOW, T_YOUTUBE}


def seeder_color(count):
    if count > 500:  return (0.10, 0.84, 0.61, 1.0)
    if count > 100:  return (0.66, 0.33, 0.97, 1.0)
    return (0.91, 0.16, 0.53, 1.0)
