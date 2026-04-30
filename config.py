import json
import os
from pathlib import Path

_DEFAULT_CONFIG = Path.home() / '.config' / 'the-hive' / 'config.json'
CONFIG_FILE     = Path(os.environ.get('HIVE_CONFIG', _DEFAULT_CONFIG))

_config = None


def load_config():
    global _config
    if _config is None:
        try:
            _config = json.loads(CONFIG_FILE.read_text())
        except FileNotFoundError:
            print(f"Config not found. Copy config.example.json to {CONFIG_FILE}")
            _config = {}
        except Exception:
            _config = {}
    return _config


def movies_dir():
    return Path(load_config().get('paths', {}).get('movies', '~/Videos/movies')).expanduser()


def shows_dir():
    return Path(load_config().get('paths', {}).get('shows', '~/Videos/shows')).expanduser()


def tmdb_token():
    return load_config().get('tmdb', {}).get('read_token', '')


def jellyfin_cfg():
    cfg = load_config().get('jellyfin', {})
    return cfg.get('url', 'http://localhost:8096'), cfg.get('api_key', '')


def qbit_cfg():
    cfg = load_config().get('qbittorrent', {})
    return cfg.get('user', 'admin'), cfg.get('pass', 'adminadmin')
