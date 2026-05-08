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

# ── Themes ────────────────────────────────────────────────────────────────────

_THEME_AURELIA = {
    'name':            'aurelia',
    'bg':              (0.04, 0.04, 0.05, 1.00),  # cell background
    'bg_wash':         (0.00, 0.00, 0.00, 0.20),  # initial canvas wash
    'accent':          (0.92, 0.33, 0.62, 1.00),  # cursor / accent
    'cell_border':     (0.00, 0.00, 0.00, 1.00),  # flower cell borders
    'text':            (1.00, 1.00, 1.00, 1.00),  # primary text
    'text_dim':        (1.00, 1.00, 1.00, 0.55),  # source label / dim text
    'text_shade':      (0.00, 0.00, 0.00, 0.70),  # text bg shade strip
    'tile_sel_bg':     (0.18, 0.08, 0.35, 0.95),  # search tile selected bg
    'tile_bg':         (0.07, 0.05, 0.11, 0.95),  # search tile unselected bg
    'tile_sel_border': (1.00, 1.00, 1.00, 0.60),  # tile border selected
    'tile_border':     (1.00, 1.00, 1.00, 0.20),  # tile border unselected
    'tile_sel_text':   (1.00, 1.00, 1.00, 1.00),  # tile title text (selected)
    'tile_text':       (1.00, 1.00, 1.00, 1.00),  # tile title text (unselected)
    'seeder_high':     (0.10, 0.84, 0.61, 1.00),  # >500 seeders
    'seeder_mid':      (0.66, 0.33, 0.97, 1.00),  # >100 seeders
    'seeder_low':      (0.91, 0.16, 0.53, 1.00),  # <100 seeders
}

_THEME_MONO = {
    'name':            'mono',
    'desaturate':      True,                       # strip color from artwork
    'bg':              (0.08, 0.08, 0.08, 1.00),  # near-black cell bg
    'bg_wash':         (0.00, 0.00, 0.00, 0.40),  # dark canvas wash
    'accent':          (0.85, 0.85, 0.85, 1.00),  # light gray cursor
    'cell_border':     (0.22, 0.22, 0.22, 1.00),  # dark gray borders
    'text':            (1.00, 1.00, 1.00, 1.00),  # white text
    'text_dim':        (0.65, 0.65, 0.65, 0.85),  # dim gray text
    'text_shade':      (0.00, 0.00, 0.00, 0.70),  # dark shade strip
    'tile_sel_bg':     (0.22, 0.22, 0.22, 0.95),  # search tile selected bg
    'tile_bg':         (0.06, 0.06, 0.06, 0.95),  # search tile unselected bg
    'tile_sel_border': (0.80, 0.80, 0.80, 0.60),  # tile border selected (light)
    'tile_border':     (0.45, 0.45, 0.45, 0.30),  # tile border unselected (gray)
    'tile_sel_text':   (1.00, 1.00, 1.00, 1.00),  # tile title text selected
    'tile_text':       (0.85, 0.85, 0.85, 1.00),  # tile title text unselected
    'seeder_high':     (0.90, 0.90, 0.90, 1.00),
    'seeder_mid':      (0.65, 0.65, 0.65, 1.00),
    'seeder_low':      (0.45, 0.45, 0.45, 1.00),
}

_THEMES = {'aurelia': _THEME_AURELIA, 'mono': _THEME_MONO}
_saved  = (CACHE_DIR / 'theme').read_text().strip() if (CACHE_DIR / 'theme').exists() else 'aurelia'
THEME   = dict(_THEMES.get(_saved, _THEME_AURELIA))


def set_theme(name):
    THEME.update(_THEMES[name])
    (CACHE_DIR / 'theme').write_text(name)


def reload_theme():
    """Re-read the persisted theme file and apply — used for SIGUSR1 live reload."""
    saved = (CACHE_DIR / 'theme').read_text().strip() if (CACHE_DIR / 'theme').exists() else 'aurelia'
    THEME.update(_THEMES.get(saved, _THEME_AURELIA))


def toggle_theme():
    set_theme('mono' if THEME['name'] == 'aurelia' else 'aurelia')


def seeder_color(count):
    if count > 500:  return THEME['seeder_high']
    if count > 100:  return THEME['seeder_mid']
    return THEME['seeder_low']
