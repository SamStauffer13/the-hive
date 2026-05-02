"""Pure hex geometry — no GTK imports. All functions take explicit parameters."""
import math

ROWS  = 7
BEVEL = 3    # px shrink — thin dark gap at tessellation seams

# Cache trig per radius — 6 cos/sin calls → dict lookup per hex per frame
_HEX_OFFSETS: dict = {}
# Cache truncation results — binary-search text_extents → dict lookup
_TRUNC_CACHE: dict = {}

# Axial directions for hex spiral traversal (flat-top orientation)
_SPIRAL_DIRS = [(0, -1), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0)]


def radius(vh):
    return max(40, vh / (math.sqrt(3) * (ROWS + 0.5)))


def ncols(vw, r):
    return max(1, int(vw / (1.5 * r)) + 1)


def positions(n, vw, vh):
    """Return (list of (cx, cy), r) in a hex spiral centered at world origin.
    Cell 0 at (0,0), cells 1-6 form ring 1, cells 7-18 ring 2, etc.
    Pan maps the selected cell to screen center — no viewport offset here."""
    r = radius(vh)

    axial = [(0, 0)]
    ring  = 1
    while len(axial) < n:
        q, s = -ring, ring          # start of ring k: direction 4 * k from origin
        for d in range(6):
            dq, ds = _SPIRAL_DIRS[d]
            for _ in range(ring):
                if len(axial) >= n:
                    break
                axial.append((q, s))
                q += dq
                s += ds
        ring += 1

    def to_world(q, s):
        return 1.5 * r * q, math.sqrt(3) * r * (s + q / 2)

    return [to_world(q, s) for q, s in axial[:n]], r


def hex_path(cr, cx, cy, r):
    if r not in _HEX_OFFSETS:
        _HEX_OFFSETS[r] = [(r * math.cos(math.pi / 3 * i), r * math.sin(math.pi / 3 * i)) for i in range(6)]
    pts = _HEX_OFFSETS[r]
    cr.move_to(cx + pts[0][0], cy + pts[0][1])
    for dx, dy in pts[1:]:
        cr.line_to(cx + dx, cy + dy)
    cr.close_path()


def wide_hex_path(cr, cx, cy, w, h):
    """Flat-sided hex bar used for the search input."""
    tip = h / 2
    cr.move_to(cx - w / 2 + tip, cy - h / 2)
    cr.line_to(cx + w / 2 - tip, cy - h / 2)
    cr.line_to(cx + w / 2,       cy)
    cr.line_to(cx + w / 2 - tip, cy + h / 2)
    cr.line_to(cx - w / 2 + tip, cy + h / 2)
    cr.line_to(cx - w / 2,       cy)
    cr.close_path()


def sr_dynamic_geo(n, vw, vh):
    """Pick column count and R so all search results fill the viewport."""
    top_off = 100
    avail_h = vh - top_off
    best_cols, best_R = 3, 0
    for cols in range(3, 10):
        rows = math.ceil(n / cols)
        r_h  = avail_h / (math.sqrt(3) * (rows + 0.5))
        r_w  = vw      / (1.5 * cols + 0.5)
        r    = min(r_h, r_w)
        if r > best_R:
            best_R, best_cols = r, cols
    return best_R, best_cols, top_off


def sr_item_positions(n, vw, vh, scroll_y):
    """Return (list of (cx, cy), R) for n search result tiles."""
    R, ncols_, top_off = sr_dynamic_geo(n, vw, vh)
    h_step = 1.5 * R
    v_step = math.sqrt(3) * R
    first_row      = min(n, ncols_)
    grid_w         = (first_row - 1) * h_step + 2 * R
    x_off          = (vw - grid_w) / 2
    nrows          = math.ceil(max(1, n) / ncols_)
    last_row_count = n - (nrows - 1) * ncols_
    max_cy_rel = (nrows - 1) * v_step + (v_step if last_row_count >= 2 else v_step / 2)
    grid_h    = max_cy_rel - v_step / 2 + 2 * R
    y_off     = top_off + max(0, (vh - top_off - grid_h) / 2) - (v_step / 2 - R)
    pos = []
    for i in range(n):
        col = i % ncols_
        row = i // ncols_
        cx  = x_off + col * h_step + R
        cy  = scroll_y + y_off + row * v_step + (col % 2) * (v_step / 2) + v_step / 2
        pos.append((cx, cy))
    return pos, R


def clamp_nav(direction, current, max_val, cols):
    """Clamp a navigation move to valid bounds."""
    if   direction == 'left':  return max(0, current - 1)
    elif direction == 'right': return min(max_val - 1, current + 1)
    elif direction == 'up':    return max(0, current - cols)
    elif direction == 'down':  return min(max_val - 1, current + cols)
    return current


def truncate_text(cr, text, max_w):
    key = (text, round(max_w), round(cr.get_font_size() * 10))
    cached = _TRUNC_CACHE.get(key)
    if cached is not None:
        return cached
    if cr.text_extents(text).width <= max_w:
        result = text
    else:
        lo, hi = 0, len(text)
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if cr.text_extents(text[:mid] + '…').width <= max_w:
                lo = mid
            else:
                hi = mid
        result = text[:lo] + '…'
    if len(_TRUNC_CACHE) < 2000:
        _TRUNC_CACHE[key] = result
    return result
