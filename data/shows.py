from config import shows_dir
from constants import T_SHOW
from . import scan_video_dir


def get_local_shows():
    return scan_video_dir(shows_dir(), T_SHOW, recursive=True)
