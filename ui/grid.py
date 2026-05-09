import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from .hex_geometry import (
    radius, radius_for_n, positions, axial_to_world, axial_round,
    hex_path, truncate_text,
    BEVEL,
)
from . import scale_pixbuf_for_hex
from .preview import PreviewManager
from .search_overlay import SearchOverlay
from constants import (
    THEME, S_INSTALLED, S_DOWNLOADING, S_NOT_INSTALLED, VIDEO_TYPES,
    ITEM_ALPHA_UNSELECTED, ALPHA_DOWNLOADING_BASE,
)


def _desat(cr):
    """Desaturate the current clip region to grayscale (mono theme only)."""
    if not THEME.get('desaturate'):
        return
    cr.set_operator(cairo.OPERATOR_HSL_SATURATION)
    cr.set_source_rgb(0.5, 0.5, 0.5)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)


# Axial navigation deltas (pointy-top hex)
_AX_DIRS = {
    'right': ( 1,  0),
    'left':  (-1,  0),
    'up':    ( 0, -1),
    'down':  ( 0,  1),
}


class HiveGrid(Gtk.DrawingArea):
    def __init__(self, items, on_activate, on_delete):
        super().__init__()
        self.all_items   = items
        self.visible     = list(range(len(items)))   # filter mode: matched indices
        self.selected    = 0                          # filter mode: index into visible
        self._sel_q      = 0                          # browse mode: axial q
        self._sel_s      = 0                          # browse mode: axial s
        self.on_activate = on_activate
        self.on_delete   = on_delete
        self.query       = ""

        self._pb_cache     = {}
        self._scaled_cache = {}
        # Filter mode layout cache
        self._pos_cache     = None
        self._pos_cache_key = None
        self._pan_x         = 0.0
        self._pan_y         = 0.0
        self._hovered       = None   # (q, s) or None

        self._matched     = set()
        self._petal_rings = 1

        self._gravity_pos = {}   # item_idx → (world_x, world_y) in filter mode
        self._item_alpha  = {}   # item_idx → alpha (1.0=visible, 0.08=faded)

        # Zoom transition — 0.0 = star map (all items visible), 1.0 = normal browse
        self._zoom           = 0.0
        self._zoom_animating = False

        self.search  = SearchOverlay(self.queue_draw)
        self.preview = PreviewManager(self.queue_draw)

        self.set_focusable(True)
        self.set_draw_func(self._draw, None)
        self._update_size()

        GLib.timeout_add(50, self._pulse)

        click = Gtk.GestureClick()
        click.connect('pressed', self._on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._on_motion)
        motion.connect('leave',  self._on_leave)
        self.add_controller(motion)

        threading.Thread(target=self._load_pixbufs_async, args=(items,), daemon=True).start()

    def _pulse(self):
        self.preview.pulse()
        if self.query:
            self._animate_search()
            self.queue_draw()
        return True

    def _animate_search(self):
        """Per-tick fade for unmatched items. Positions snap immediately on filter."""
        speed = 0.35
        for i in range(len(self.all_items)):
            target = 1.0 if i in self._matched else 0.08
            cur    = self._item_alpha.get(i, 1.0)
            self._item_alpha[i] = cur + (target - cur) * speed

    # ── Zoom transition ──────────────────────────────────────────────────

    def _flower_r_sub(self):
        """Smallest r_sub such that a 7-sub-cluster flower covers all items."""
        n = len(self.all_items)
        r_sub = 1
        while 7 * (1 + 3 * r_sub * (r_sub + 1)) < n:
            r_sub += 1
        return r_sub

    def _effective_r(self, vh):
        """Cell radius lerped from star-map size → normal size (ease-out cubic)."""
        n = len(self.all_items)
        if not n:
            return radius(vh)
        vw, _ = self._viewport_size()
        # Flower extent = 3*r_sub rings — use that as the star-map bounding size
        r_sub = self._flower_r_sub()
        outer = 3 * r_sub
        r0 = radius_for_n(1 + 3 * outer * (outer + 1), vw, vh)
        r1 = radius(vh)
        t  = 1.0 - (1.0 - self._zoom) ** 3
        return r0 + (r1 - r0) * t

    def _start_zoom(self):
        if self._zoom < 1.0 and not self._zoom_animating:
            self._zoom_animating = True
            GLib.timeout_add(16, self._tick_zoom)

    def _tick_zoom(self):
        self._zoom = min(1.0, self._zoom + 0.05)
        self.queue_draw()
        if self._zoom >= 1.0:
            self._zoom_animating = False
            return False
        return True

    # ── Item mapping (infinite tiling) ──────────────────────────────────

    def _item_at(self, q, s):
        """Return item tiled at axial cell (q, s). Uses Knuth multiplicative hash."""
        n = len(self.all_items)
        if not n:
            return None
        return self.all_items[(q * 2654435761 + s * 2246822519) % n]

    def _item_idx_at(self, q, s):
        """Return all_items index for axial cell (q, s)."""
        n = len(self.all_items)
        if not n:
            return -1
        return (q * 2654435761 + s * 2246822519) % n

    # ── Pixbuf loading ───────────────────────────────────────────────────

    def _load_pixbufs_async(self, items):
        def load_one(item):
            if item.get('artwork'):
                try:
                    return id(item), GdkPixbuf.Pixbuf.new_from_file(item['artwork'])
                except Exception:
                    pass
            return id(item), None

        with ThreadPoolExecutor(max_workers=8) as ex:
            for future in as_completed(ex.submit(load_one, item) for item in items):
                item_id, pb = future.result()
                if pb:
                    GLib.idle_add(self._store_pixbuf, item_id, pb)

    def _store_pixbuf(self, item_id, pb):
        self._pb_cache[item_id] = pb
        self.queue_draw()
        return False

    def _get_flower_pb(self, raw, super_r):
        key = (id(raw), round(super_r))
        pb  = self._scaled_cache.get(key)
        if pb is None:
            fw    = math.ceil(2 * super_r) + 2   # +2 margin avoids subpixel edge gaps
            scale = min(fw / raw.get_width(), fw / raw.get_height())
            pb    = raw.scale_simple(
                max(1, int(raw.get_width()  * scale)),
                max(1, int(raw.get_height() * scale)),
                GdkPixbuf.InterpType.BILINEAR,
            )
            self._scaled_cache[key] = pb
        return pb

    def _get_scaled(self, item, draw_r):
        key = (id(item), draw_r)
        if key not in self._scaled_cache:
            pb = self._pb_cache.get(id(item))
            if pb is None:
                return None
            self._scaled_cache[key] = scale_pixbuf_for_hex(pb, draw_r)
        return self._scaled_cache[key]

    # ── Layout helpers ────────────────────────────────────────────────────

    def _viewport_size(self):
        viewport = self.get_parent()
        if viewport is not None:
            scroll = viewport.get_parent()
            if isinstance(scroll, Gtk.ScrolledWindow):
                return scroll.get_width() or 1280, scroll.get_height() or 800
        return 1280, 800

    def _get_positions(self):
        """Filter mode only — finite spiral positions."""
        vw, vh = self._viewport_size()
        n   = len(self.all_items)
        key = (n, vw, vh)
        if self._pos_cache is not None and self._pos_cache_key == key:
            return self._pos_cache, radius(vh)
        pos, r              = positions(n, vw, vh)
        self._pos_cache     = pos
        self._pos_cache_key = key
        return pos, r

    def _invalidate_layout(self):
        self._pos_cache     = None
        self._pos_cache_key = None

    def _update_size(self):
        vw, vh = self._viewport_size()
        self.set_content_width(vw)
        self.set_content_height(vh)

    def _update_pan(self, vw=None, vh=None):
        """Filter mode pan: keep selected item at screen center."""
        if vw is None or vh is None:
            vw, vh = self._viewport_size()
        pos, _ = self._get_positions()
        if not pos:
            self._pan_x = self._pan_y = 0.0
            return
        idx = self.visible[self.selected] if (self.query and 0 <= self.selected < len(self.visible)) else self.selected
        if 0 <= idx < len(pos):
            cx, cy      = pos[idx]
            self._pan_x = vw / 2 - cx
            self._pan_y = vh / 2 - cy
        else:
            self._pan_x = self._pan_y = 0.0

    # ── Drawing ───────────────────────────────────────────────────────────

    def _draw(self, area, cr, width, height, data):
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()

        cr.set_source_rgba(*THEME['bg_wash'])
        cr.paint()

        vw, vh = width, height
        if self.query:
            self._update_pan(vw, vh)
            self._draw_filter_mode(cr, vw, vh)
        else:
            self._draw_infinite(cr, vw, vh)

    def _draw_infinite(self, cr, vw, vh):
        """Infinite tiling hex grid — browse mode. Selected cell always at center."""
        r      = self._effective_r(vh)
        cell_r = r - BEVEL
        sqrt3  = math.sqrt(3)

        # Star-map mode: clip to a 6-petal flower (center cluster + 6 petal clusters)
        n             = len(self.all_items)
        _flower_clip  = None
        if self._zoom < 0.98 and n > 0:
            r_sub    = self._flower_r_sub()
            _petal_c = 2 * r_sub
            _PDIRS   = [(_petal_c,0),(0,_petal_c),(-_petal_c,_petal_c),
                        (-_petal_c,0),(0,-_petal_c),(_petal_c,-_petal_c)]
            _flower_clip = (r_sub, _PDIRS, r_sub)

        active_petals = self._petal_rings
        petal_set = set()
        for dq in range(-active_petals, active_petals + 1):
            for ds in range(-active_petals, active_petals + 1):
                dr   = -dq - ds
                dist = max(abs(dq), abs(ds), abs(dr))
                if 0 < dist <= active_petals:
                    petal_set.add((dq, ds))

        # Preview / flower image for selected + petals
        sel_item = self._item_at(self._sel_q, self._sel_s)
        flower_pb = flower_pb_next = None
        fade_alpha = 1.0
        if sel_item:
            pb_cur, pb_next, fade_alpha = self.preview.current_frame()
            raw     = pb_cur or self._pb_cache.get(id(sel_item))
            super_r = sqrt3 * r * active_petals + cell_r
            if raw:
                flower_pb = self._get_flower_pb(raw, super_r)
            if pb_next:
                flower_pb_next = self._get_flower_pb(pb_next, super_r)

        # Visible cell range (screen coords: selected cell = vw/2, vh/2)
        s_range = math.ceil((vh / 2 + r) / (1.5 * r)) + 1
        x_half  = (vw / 2 + r) / (sqrt3 * r) + 1

        for ds in range(-s_range, s_range + 1):
            s      = self._sel_s + ds
            q_off  = -ds / 2
            q_lo   = math.floor(-x_half + q_off) - 1
            q_hi   = math.ceil( x_half + q_off)  + 1
            for dq in range(q_lo, q_hi + 1):
                if _flower_clip is not None:
                    core_r, petal_dirs, petal_r = _flower_clip
                    if max(abs(dq), abs(ds), abs(dq + ds)) > core_r:
                        in_petal = False
                        for pdq, pds in petal_dirs:
                            odq, ods = dq - pdq, ds - pds
                            if max(abs(odq), abs(ods), abs(odq + ods)) <= petal_r:
                                in_petal = True
                                break
                        if not in_petal:
                            continue
                q  = self._sel_q + dq
                cx = vw / 2 + sqrt3 * r * (dq + ds / 2)
                cy = vh / 2 + 1.5 * r * ds

                if cx + r < 0 or cx - r > vw or cy + r < 0 or cy - r > vh:
                    continue

                item = self._item_at(q, s)
                if item is None:
                    continue

                selected = (dq == 0 and ds == 0)
                is_petal = (dq, ds) in petal_set
                alpha    = 1.0 if (selected or is_petal) else ITEM_ALPHA_UNSELECTED

                state = item.get('state', S_INSTALLED)
                if state == S_DOWNLOADING:
                    alpha *= ALPHA_DOWNLOADING_BASE + ALPHA_DOWNLOADING_BASE * (math.sin(time.time() * 1.5) + 1) / 2
                elif state == S_NOT_INSTALLED:
                    alpha *= ALPHA_DOWNLOADING_BASE

                cr.save()
                hex_path(cr, cx, cy, cell_r)
                cr.clip()

                if (selected or is_petal) and flower_pb:
                    cr.set_source_rgba(*THEME['bg'])
                    cr.paint()
                    # Flower centered at screen center (selected cell position)
                    ox = vw / 2 - flower_pb.get_width()  / 2
                    oy = vh / 2 - flower_pb.get_height() / 2
                    Gdk.cairo_set_source_pixbuf(cr, flower_pb, ox, oy)
                    if flower_pb_next:
                        cr.paint_with_alpha(alpha * (1.0 - fade_alpha))
                        Gdk.cairo_set_source_pixbuf(cr, flower_pb_next, ox, oy)
                        cr.paint_with_alpha(alpha * fade_alpha)
                    else:
                        cr.paint_with_alpha(alpha)
                    # flower stays full color — no desat
                else:
                    cr.set_source_rgba(*THEME['bg'])
                    cr.paint()
                    draw_pb = self._get_scaled(item, cell_r)
                    if draw_pb:
                        Gdk.cairo_set_source_pixbuf(cr, draw_pb,
                            cx - draw_pb.get_width()  / 2,
                            cy - draw_pb.get_height() / 2)
                        cr.paint_with_alpha(alpha)
                        _desat(cr)

                cr.restore()

                # Black border around flower cells — visible at outer edge
                if selected or is_petal:
                    hex_path(cr, cx, cy, cell_r)
                    cr.set_source_rgba(*THEME['cell_border'])
                    cr.set_line_width(4)
                    cr.stroke()

    def _draw_filter_mode(self, cr, vw, vh):
        """Filter mode — fixed 1-ring flower at center, matched cells around it."""
        pos, r = self._get_positions()
        cell_r = r - BEVEL
        sqrt3  = math.sqrt(3)

        rank_of = {item_idx: rank for rank, item_idx in enumerate(self.visible)}

        # Flower image: selected match's artwork spans the 7-cell cluster
        sel_item_idx = (
            self.visible[self.selected]
            if self.visible and 0 <= self.selected < len(self.visible)
            else -1
        )
        super_r   = sqrt3 * r + cell_r   # 1-ring flower radius
        flower_pb = None
        if 0 <= sel_item_idx < len(self.all_items):
            raw = self._pb_cache.get(id(self.all_items[sel_item_idx]))
            if raw:
                flower_pb = self._get_flower_pb(raw, super_r)

        cr.save()
        cr.translate(vw / 2, vh / 2)

        # Pass 0: full 7-cell flower — always drawn regardless of match count
        if flower_pb:
            for fx, fy in pos[:7]:
                cr.save()
                hex_path(cr, fx, fy, cell_r)
                cr.clip()
                cr.set_source_rgba(*THEME['bg'])
                cr.paint()
                Gdk.cairo_set_source_pixbuf(cr, flower_pb,
                    -flower_pb.get_width()  / 2,
                    -flower_pb.get_height() / 2)
                cr.paint_with_alpha(1.0)
                cr.restore()

        # Pass 1: unmatched background
        for i, item in enumerate(self.all_items):
            if i >= len(pos) or i in self._matched:
                continue
            cx, cy   = pos[i]
            scx, scy = cx + vw / 2, cy + vh / 2
            if scy + r < 0 or scy - r > vh or scx + r < 0 or scx - r > vw:
                continue
            fade_a = self._item_alpha.get(i, 1.0)
            if fade_a < 0.01:
                continue
            cr.save()
            hex_path(cr, cx, cy, cell_r)
            cr.clip()
            cr.set_source_rgba(*THEME['bg'])
            cr.paint()
            pb = self._pb_cache.get(id(item))
            if pb:
                spb = self._get_scaled(item, cell_r)
                if spb:
                    Gdk.cairo_set_source_pixbuf(cr, spb,
                        cx - spb.get_width()  / 2,
                        cy - spb.get_height() / 2)
                    cr.paint_with_alpha(fade_a)
                    _desat(cr)
            cr.restore()

        # Pass 2: matched cells on top
        for i, item in enumerate(self.all_items):
            if i >= len(pos) or i not in self._matched:
                continue
            rank      = rank_of.get(i, -1)
            in_flower = 0 <= rank < 7
            cx, cy    = self._gravity_pos.get(i, pos[i])
            alpha     = self._item_alpha.get(i, 1.0) * (1.0 if in_flower else 0.55)
            scx, scy  = cx + vw / 2, cy + vh / 2
            if scy + r < 0 or scy - r > vh or scx + r < 0 or scx - r > vw:
                continue
            cr.save()
            hex_path(cr, cx, cy, cell_r)
            cr.clip()
            cr.set_source_rgba(*THEME['bg'])
            cr.paint()
            if in_flower and flower_pb:
                # Flower image centered at world origin — same as browse mode
                Gdk.cairo_set_source_pixbuf(cr, flower_pb,
                    -flower_pb.get_width()  / 2,
                    -flower_pb.get_height() / 2)
                cr.paint_with_alpha(alpha)
                # flower stays full color — no desat
            else:
                pb = self._pb_cache.get(id(item))
                if pb:
                    spb = self._get_scaled(item, cell_r)
                    if spb:
                        Gdk.cairo_set_source_pixbuf(cr, spb,
                            cx - spb.get_width()  / 2,
                            cy - spb.get_height() / 2)
                        cr.paint_with_alpha(alpha)
                        _desat(cr)
            cr.restore()

        self._draw_floating_search(cr, len(self.visible))

        cr.restore()

    def _draw_floating_search(self, cr, match_count):
        """Query text + cursor + match count centered on the flower."""
        cr.select_font_face('CYBERHYPE', 0, 0)
        cr.set_font_size(32)
        text = self.query
        te   = cr.text_extents(text)
        # Center query text on the flower (world origin = flower center)
        ty = -(te.y_bearing + te.height / 2)
        tx = -te.width / 2 - te.x_bearing
        cr.set_source_rgba(*THEME['text'])
        cr.move_to(tx, ty)
        cr.show_text(text)

        if int(time.time() * 2) % 2 == 0:
            cur_x = tx + te.x_advance + 3
            cr.set_source_rgba(*THEME['accent'])
            cr.set_line_width(2)
            cr.move_to(cur_x, ty + te.y_bearing - 2)
            cr.line_to(cur_x, ty + te.y_bearing + te.height + 2)
            cr.stroke()

        # Match count just below the query text
        if match_count == 0:
            count_str = "no match"
        elif match_count == 1:
            count_str = "1 match"
        else:
            count_str = f"{match_count} matches"
        cr.select_font_face('Nova Mono', 0, 0)
        cr.set_font_size(13)
        cr.set_source_rgba(*THEME['text_dim'])
        te_c = cr.text_extents(count_str)
        cr.move_to(-te_c.width / 2 - te_c.x_bearing, ty + te.height + 10)
        cr.show_text(count_str)

    # ── Interaction ────────────────────────────────────────────────────────

    def _screen_to_axial(self, x, y, vw, vh, r):
        """Screen (x, y) → axial (q, s) in browse mode."""
        s_frac = (y - vh / 2) / (1.5 * r)
        q_frac = (x - vw / 2) / (math.sqrt(3) * r) - s_frac / 2
        dq, ds = axial_round(q_frac, s_frac)
        return self._sel_q + dq, self._sel_s + ds

    def _on_motion(self, ctrl, x, y):
        if self.query or self.search.is_active():
            return
        vw, vh = self._viewport_size()
        r      = self._effective_r(vh)
        hov    = self._screen_to_axial(x, y, vw, vh, r)
        if hov != self._hovered:
            self._hovered = hov
            self.queue_draw()

    def _on_leave(self, ctrl):
        if self._hovered is not None:
            self._hovered = None
            self.queue_draw()

    def _on_click(self, gesture, n_press, x, y):
        if self.search.is_active():
            vw, vh = self._viewport_size()
            slot   = self.search.slot_at(x, y, vw, vh, 0)
            if slot >= 0:
                self.search.selected = slot
                self.queue_draw()
                if n_press >= 2:
                    self.search.activate(self.on_activate)
            return

        vw, vh = self._viewport_size()
        r      = self._effective_r(vh)

        if self.query:
            pos_list, _ = self._get_positions()
            # Click coords relative to world origin (screen center)
            wx, wy = x - vw / 2, y - vh / 2
            for i in self._matched:
                cx, cy = self._gravity_pos.get(i, pos_list[i] if i < len(pos_list) else (0, 0))
                if math.hypot(wx - cx, wy - cy) > r:
                    continue
                try:
                    vis_idx = self.visible.index(i)
                except ValueError:
                    return
                if vis_idx != self.selected:
                    self.selected = vis_idx
                    self._update_pan()
                    self.queue_draw()
                if n_press >= 2:
                    self.activate()
                return
        else:
            self._start_zoom()
            q, s = self._screen_to_axial(x, y, vw, vh, r)
            if q != self._sel_q or s != self._sel_s:
                self._sel_q, self._sel_s = q, s
                self.preview.stop()
                item = self._item_at(q, s)
                if item and item['type'] in VIDEO_TYPES:
                    self.preview.start(item, (q, s))
                self.queue_draw()
            if n_press >= 2:
                self.activate()

    # ── Navigation ─────────────────────────────────────────────────────────

    def _navigate_spatial(self, direction):
        """Filter mode: find nearest matched cell in the given direction."""
        pos_list, _ = self._get_positions()

        def cell_pos(idx):
            if idx in self._gravity_pos:
                return self._gravity_pos[idx]
            if idx < len(pos_list):
                return pos_list[idx]
            return (0.0, 0.0)

        cur_idx = self.visible[self.selected] if 0 <= self.selected < len(self.visible) else -1
        if cur_idx < 0:
            return self.selected
        cur_cx, cur_cy = cell_pos(cur_idx)

        best, best_score = -1, float('inf')
        for vi, item_idx in enumerate(self.visible):
            if vi == self.selected:
                continue
            cx, cy = cell_pos(item_idx)
            dx, dy = cx - cur_cx, cy - cur_cy
            if direction == 'left'  and dx >= 0: continue
            if direction == 'right' and dx <= 0: continue
            if direction == 'up'    and dy >= 0: continue
            if direction == 'down'  and dy <= 0: continue
            score = math.hypot(dx, dy)
            if score < best_score:
                best_score, best = score, vi

        return best if best >= 0 else self.selected

    def adjust_petal_rings(self, delta):
        self._petal_rings = max(0, min(3, self._petal_rings + delta))
        self.queue_draw()

    def navigate(self, direction):
        if self.query:
            n = len(self.visible)
            if not n:
                return
            new = self._navigate_spatial(direction)
            if new != self.selected:
                self.selected = new
                self._update_pan()
                self.queue_draw()
        else:
            self._start_zoom()
            dq, ds         = _AX_DIRS[direction]
            self._sel_q   += dq
            self._sel_s   += ds
            self.preview.stop()
            item = self._item_at(self._sel_q, self._sel_s)
            if item and item['type'] in VIDEO_TYPES:
                self.preview.start(item, (self._sel_q, self._sel_s))
            self.queue_draw()

    # ── Public API ─────────────────────────────────────────────────────────

    def activate(self):
        if self.query:
            if 0 <= self.selected < len(self.visible):
                self.on_activate(self.all_items[self.visible[self.selected]])
        else:
            item = self._item_at(self._sel_q, self._sel_s)
            if item:
                self.on_activate(item)

    def get_selected(self):
        """Returns (item, item_idx) — item_idx is index into all_items."""
        if self.query:
            if 0 <= self.selected < len(self.visible):
                idx = self.visible[self.selected]
                return self.all_items[idx], idx
            return None, -1
        idx = self._item_idx_at(self._sel_q, self._sel_s)
        if idx >= 0:
            return self.all_items[idx], idx
        return None, -1

    def set_items(self, items, selected=0, keep_cache=False):
        self.all_items = items
        self.visible   = list(range(len(items)))
        self.selected  = min(selected, max(0, len(items) - 1))
        if not keep_cache:
            self._sel_q = 0
            self._sel_s = 0
        self._matched = set()
        self.query    = ""
        if not keep_cache:
            self._pb_cache.clear()
            self._scaled_cache.clear()
        self._invalidate_layout()
        if not keep_cache:
            self._update_size()
        self._update_pan()
        self.queue_draw()
        if keep_cache:
            new_items = [i for i in items if id(i) not in self._pb_cache and i.get('artwork')]
            if new_items:
                threading.Thread(target=self._load_pixbufs_async, args=(new_items,), daemon=True).start()
        elif any(i.get('artwork') for i in items):
            threading.Thread(target=self._load_pixbufs_async, args=(items,), daemon=True).start()

    def filter(self, query):
        was_filtering = bool(self.query)
        self.query    = query
        vw, vh        = self._viewport_size()

        if not query:
            self.visible      = list(range(len(self.all_items)))
            self._matched     = set()
            self._gravity_pos = {}
            self._item_alpha.clear()
        else:
            q       = query.lower()
            matched = []
            for i, item in enumerate(self.all_items):
                name, pos_idx, ok = item['name'].lower(), 0, True
                for ch in q:
                    p = name.find(ch, pos_idx)
                    if p < 0:
                        ok = False
                        break
                    pos_idx = p + 1
                if ok:
                    matched.append(i)
            self._matched = set(matched)
            self.visible  = matched

            # Pack matched items: flower in ring-0+1 (pos 0-6), rest skip ring-2 → ring-3+
            # Ring-2 left empty as a gap so non-flower cells don't touch the flower edge.
            if matched:
                _RING2_END = 19   # positions 0-6: ring-0+1 (flower), 7-18: ring-2 (skipped)
                total_needed = max(7, _RING2_END + max(0, len(matched) - 7))
                tgt_pos, _   = positions(total_needed, vw, vh)
                self._gravity_pos = {}
                for rank, item_idx in enumerate(matched):
                    pos_idx = rank if rank < 7 else _RING2_END + (rank - 7)
                    self._gravity_pos[item_idx] = tgt_pos[pos_idx]
            else:
                self._gravity_pos = {}

        self.selected = 0
        if was_filtering != bool(query):
            self._invalidate_layout()
            self._update_size()
        self._update_pan()
        self.queue_draw()

    def remove_item(self, item_idx):
        """Remove item at item_idx from all_items."""
        if not (0 <= item_idx < len(self.all_items)):
            return
        item = self.all_items.pop(item_idx)
        self._pb_cache.pop(id(item), None)
        self._scaled_cache = {k: v for k, v in self._scaled_cache.items() if k[0] != id(item)}
        self.visible  = [i if i < item_idx else i - 1 for i in self.visible if i != item_idx]
        self._matched = {i if i < item_idx else i - 1 for i in self._matched if i != item_idx}
        self.selected = min(self.selected, max(0, len(self.visible) - 1))
        self._invalidate_layout()
        self._update_size()
        self._update_pan()
        self.queue_draw()

    # Legacy alias
    def remove_visible(self, item_idx):
        self.remove_item(item_idx)
