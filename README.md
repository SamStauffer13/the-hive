# The Hive

Two features, one interface.

## Omni Launcher

Fullscreen honeycomb grid of local content with cover art and fuzzy search.

**Sources:** Steam library · local movies/shows · Wallpaper Engine · YouTube

## Omni Search

Type a query → search TMDB, Steam Store, and book databases → select a result → `Enter` sends it to qBittorrent.

## Install

```bash
sudo pacman -S python-gobject gtk4
HIVE_CONFIG=/path/to/config.json python the-hive.py
```

## Config

`~/.config/the-hive/config.json` — all fields optional.

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

## Structure

```
the-hive/
  the-hive.py         # entry point
  constants.py        # types, colors, draw params
  config.py           # reads config.json
  cache.py            # image download + queue helpers
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
    hex_geometry.py   # pure math — positions, paths, caches
    grid.py           # HiveGrid drawing area
    search_overlay.py # search result tiles + state
    preview.py        # video preview frames + animation
```

## Optional dependencies

| Tool | Used for |
|------|----------|
| `mpv` / `vlc` | playing local movies and shows |
| `ffmpeg` | extracting video preview frames |
| `steam` | launching games, unsubscribing wallpapers |
| `linux-wallpaperengine` | animated wallpapers from Workshop |
