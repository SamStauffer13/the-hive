import math
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf


def scale_pixbuf_for_hex(pb, r):
    """Scale pixbuf to cover a hex of radius r."""
    hw    = 2 * r
    hh    = math.sqrt(3) * r
    scale = max(hw / pb.get_width(), hh / pb.get_height())
    w = max(1, int(pb.get_width()  * scale))
    h = max(1, int(pb.get_height() * scale))
    return pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
