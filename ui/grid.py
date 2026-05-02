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
    radius, positions, axial_to_world, axial_round,
    hex_path, truncate_text,
    BEVEL,
)
from . import scale_pixbuf_for_hex
from .preview import PreviewManager
from .search_overlay import SearchOverlay
from constants import (
    C_BG_DARK, C_PINK, S_INSTALLED, S_DOWNLOADING, S_NOT_INSTALLED, VIDEO_TYPES,
    ITEM_ALPHA_UNSELECTED, ALPHA_DOWNLOADING_BASE,
)

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
        self._petal_rings = 0

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
            self.queue_draw()
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
            fw    = int(2 * super_r)
            scale = max(fw / raw.get_width(), fw / raw.get_height())
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
        key = (n, vh)
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

        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.paint()

        vw, vh = width, height
        if self.query:
            self._update_pan(vw, vh)
            self._draw_filter_mode(cr, vw, vh)
        else:
            self._draw_infinite(cr, vw, vh)

    def _draw_infinite(self, cr, vw, vh):
        """Infinite tiling hex grid — browse mode. Selected cell always at center."""
        r      = radius(vh)
        cell_r = r - BEVEL
        sqrt3  = math.sqrt(3)

        # Petal cells: set of (dq, ds) within _petal_rings hex distance
        petal_set = set()
        for dq in range(-self._petal_rings, self._petal_rings + 1):
            for ds in range(-self._petal_rings, self._petal_rings + 1):
                dr   = -dq - ds
                dist = max(abs(dq), abs(ds), abs(dr))
                if 0 < dist <= self._petal_rings:
                    petal_set.add((dq, ds))

        # Preview / flower image for selected + petals
        sel_item = self._item_at(self._sel_q, self._sel_s)
        flower_pb = flower_pb_next = None
        fade_alpha = 1.0
        if sel_item:
            pb_cur, pb_next, fade_alpha = self.preview.current_frame()
            raw     = pb_cur or self._pb_cache.get(id(sel_item))
            super_r = sqrt3 * r * self._petal_rings + cell_r
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
                    cr.set_source_rgba(*C_BG_DARK)
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
                else:
                    cr.set_source_rgba(*C_BG_DARK)
                    cr.paint()
                    draw_pb = self._get_scaled(item, cell_r)
                    if draw_pb:
                        Gdk.cairo_set_source_pixbuf(cr, draw_pb,
                            cx - draw_pb.get_width()  / 2,
                            cy - draw_pb.get_height() / 2)
                        cr.paint_with_alpha(alpha)

                cr.restore()

    def _draw_filter_mode(self, cr, vw, vh):
        """Finite spiral — filter mode. Matched cells bright, unmatched dim."""
        pos, r = self._get_positions()
        cell_r = r - BEVEL

        sel_item_idx = (
            self.visible[self.selected]
            if self.visible and 0 <= self.selected < len(self.visible)
            else -1
        )
        sel_cx = sel_cy = 0.0
        has_sel = 0 <= sel_item_idx < len(pos)
        if has_sel:
            sel_cx, sel_cy = pos[sel_item_idx]

        cr.save()
        cr.translate(self._pan_x, self._pan_y)

        for i, item in enumerate(self.all_items):
            if i >= len(pos):
                break
            cx, cy = pos[i]
            if cy + self._pan_y + r < 0 or cy + self._pan_y - r > vh:
                continue
            if cx + self._pan_x + r < 0 or cx + self._pan_x - r > vw:
                continue

            matched  = i in self._matched
            selected = (i == sel_item_idx)
            alpha    = 0.30 if selected else (0.88 if matched else 0.08)

            cr.save()
            hex_path(cr, cx, cy, cell_r)
            cr.clip()
            cr.set_source_rgba(*C_BG_DARK)
            cr.paint()
            pb = self._pb_cache.get(id(item))
            if pb:
                spb = self._get_scaled(item, cell_r)
                if spb:
                    Gdk.cairo_set_source_pixbuf(cr, spb,
                        cx - spb.get_width() / 2, cy - spb.get_height() / 2)
                    cr.paint_with_alpha(alpha)
            cr.restore()

        if has_sel:
            self._draw_center_search(cr, sel_cx, sel_cy, cell_r)

        cr.restore()

    def _draw_center_search(self, cr, cx, cy, cell_r):
        """Query text + blinking cursor inside the center cell."""
        font_size = max(16, cell_r * 0.32)
        cr.select_font_face('CYBERHYPE', 0, 0)
        cr.set_font_size(font_size)
        text = truncate_text(cr, self.query, cell_r * 1.55)
        te   = cr.text_extents(text)
        tx   = cx - te.width / 2 - te.x_bearing
        ty   = cy - te.height / 2 - te.y_bearing
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.move_to(tx, ty)
        cr.show_text(text)

        if int(time.time() * 2) % 2 == 0:
            cur_x = tx + te.x_advance + 3
            cr.set_source_rgba(*C_PINK)
            cr.set_line_width(2)
            cr.move_to(cur_x, ty - 2)
            cr.line_to(cur_x, ty + te.height + 2)
            cr.stroke()

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
        r      = radius(vh)
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
        r      = radius(vh)

        if self.query:
            pos_list, _ = self._get_positions()
            gx, gy      = x - self._pan_x, y - self._pan_y
            for i, (cx, cy) in enumerate(pos_list):
                if math.hypot(gx - cx, gy - cy) > r:
                    continue
                if i not in self._matched:
                    return
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
        cur_idx = self.visible[self.selected] if 0 <= self.selected < len(self.visible) else -1
        if cur_idx < 0 or cur_idx >= len(pos_list):
            return self.selected
        cur_cx, cur_cy = pos_list[cur_idx]

        best, best_score = -1, float('inf')
        for vi, item_idx in enumerate(self.visible):
            if item_idx >= len(pos_list) or vi == self.selected:
                continue
            cx, cy = pos_list[item_idx]
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
        if not query:
            self.visible  = list(range(len(self.all_items)))
            self._matched = set()
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
