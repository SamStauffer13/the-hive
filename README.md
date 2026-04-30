# The Hive

Two features, one interface.

---

## Omni Launcher

Fullscreen honeycomb grid of local content with cover art and fuzzy search.

**Sources:** Steam library · local movies/shows · Wallpaper Engine · YouTube

## Omni Search

Type a query → search TMDB, Steam Store, and book databases → select a result → `Enter` sends it to qBittorrent.

---

## Install

**System dependencies** (GTK4 + PyGObject — not pip-installable):

```bash
# Arch / SteamOS
sudo pacman -S python-gobject gtk4

# Ubuntu / Debian
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0

# Fedora
sudo dnf install python3-gobject gtk4
```

**Config:**

```bash
mkdir -p ~/.config/the-hive
cp config.example.json ~/.config/the-hive/config.json
# edit ~/.config/the-hive/config.json
```

**Run:**

```bash
python the-hive.py
```

To point at a different config file:

```bash
HIVE_CONFIG=/path/to/config.json python the-hive.py
```

---

## Config

`~/.config/the-hive/config.json` — all fields are optional. Omitted sections simply produce no content in the launcher.

```json
{
  "paths": {
    "movies": "~/Videos/movies",
    "shows":  "~/Videos/shows"
  },
  "tmdb":        { "read_token": "" },
  "jellyfin":    { "url": "http://localhost:8096", "api_key": "" },
  "qbittorrent": { "user": "admin", "pass": "adminadmin" }
}
```

| Key | Purpose |
|-----|---------|
| `paths.movies` | Local movie directory — scanned for video files |
| `paths.shows` | Local shows directory — scanned for season folders |
| `tmdb.read_token` | [TMDB API](https://www.themoviedb.org/settings/api) read token — enables Omni Search |
| `jellyfin` | Jellyfin server — used for streaming movies via URL |
| `qbittorrent` | qBittorrent WebUI credentials — receives torrents from Omni Search |

---

## Structure

```
the-hive/
  the-hive.py         # entry point
  constants.py        # types, colors, draw params
  config.py           # reads ~/.config/the-hive/config.json
  cache.py            # download_image, load_queue, save_queue
  launcher.py         # TheHive window — orchestrates everything
  data/
    steam.py          # Steam library + CDN artwork
    movies.py         # Jellyfin + local fallback
    shows.py          # local shows
    wallpapers.py     # Wallpaper Engine workshop
    youtube.py        # YouTube recommended feed
  search/
    tmdb.py           # TMDB movie/show search + seasons
    openlibrary.py    # book search
    steam_store.py    # Steam store search
    qbit.py           # qBittorrent client
  ui/
    hex_geometry.py   # pure math, no GTK — positions, paths, caches
    grid.py           # HiveGrid drawing area — main honeycomb
    search_overlay.py # search result tiles + state
    preview.py        # video preview frames + animation
```

---

## Optional dependencies

| Tool | Used for |
|------|----------|
| `mpv` / `vlc` | playing local movies and shows |
| `ffmpeg` | extracting video preview frames |
| `steam` | launching games, unsubscribing wallpapers |
| `linux-wallpaperengine` | animated wallpapers from Workshop |

All are optional — missing tools are silently skipped.

---

## Principles

- **Two domains** — Launcher (local) and Search (online) never share state
- **Atomic modules** — each file does one thing, readable in isolation
- **No inline clients** — all API calls live in `search/`, never in the controller
- **Pure geometry** — `hex_geometry.py` has zero GTK imports
