import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from .hex_geometry import (
    radius, positions,
    hex_path, wide_hex_path, truncate_text,
    BEVEL,
)
from . import scale_pixbuf_for_hex
from .preview import PreviewManager
from .search_overlay import SearchOverlay
from constants import (
    C_BG_DARK, S_INSTALLED, S_DOWNLOADING, S_NOT_INSTALLED, VIDEO_TYPES,
    ITEM_ALPHA_UNSELECTED, ALPHA_DOWNLOADING_BASE, PETAL_RINGS,
)



class HiveGrid(Gtk.DrawingArea):
    def __init__(self, items, on_activate, on_delete):
        super().__init__()
        self.all_items   = items
        self.visible     = list(range(len(items)))
        self.selected    = 0
        self.on_activate = on_activate
        self.on_delete   = on_delete
        self.query       = ""

        self._pb_cache      = {}
        self._scaled_cache  = {}
        self._pos_cache     = None
        self._pos_cache_key = None
        self._petal_cache     = {}   # slot → ring_number (1-based)
        self._petal_cache_key = None
        self._pan_x         = 0.0
        self._pan_y         = 0.0
        self._hovered       = -1

        self._matched = set()

        self.search = SearchOverlay(self.queue_draw)
        self.preview = PreviewManager(self.queue_draw)

        self.set_focusable(True)
        self.set_draw_func(self._draw, None)
        self._update_size()

        GLib.timeout_add(50, self.preview.pulse)

        click = Gtk.GestureClick()
        click.connect('pressed', self._on_click)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._on_motion)
        motion.connect('leave',  self._on_leave)
        self.add_controller(motion)

        threading.Thread(target=self._load_pixbufs_async, args=(items,), daemon=True).start()

    # ── Pixbuf loading ─────────────────────────────────────────────────

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

    # ── Layout ─────────────────────────────────────────────────────────

    def _viewport_size(self):
        viewport = self.get_parent()
        if viewport is not None:
            scroll = viewport.get_parent()
            if isinstance(scroll, Gtk.ScrolledWindow):
                return scroll.get_width() or 1280, scroll.get_height() or 800
        return 1280, 800

    def _get_positions(self):
        vw, vh = self._viewport_size()
        n = len(self.all_items) if self.query else len(self.visible)
        key = (n, vh)                   # vw unused by spiral positions()
        if self._pos_cache is not None and self._pos_cache_key == key:
            return self._pos_cache, radius(vh)
        pos, r = positions(n, vw, vh)
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

    # ── Pan ────────────────────────────────────────────────────────────

    def _update_pan(self, vw=None, vh=None):
        """Always place the selected cell at screen center — no clamping.
        Pass vw/vh explicitly from _draw to guarantee correct dimensions."""
        if vw is None or vh is None:
            vw, vh = self._viewport_size()
        pos, _ = self._get_positions()
        if not pos:
            self._pan_x = self._pan_y = 0.0
            return
        idx = self.visible[self.selected] if (self.query and 0 <= self.selected < len(self.visible)) else self.selected
        if 0 <= idx < len(pos):
            cx, cy = pos[idx]
            self._pan_x = vw / 2 - cx
            self._pan_y = vh / 2 - cy
        else:
            self._pan_x = self._pan_y = 0.0

    def _draw_selected_overlay(self, cr, cx, cy, cell_r, item):
        cr.save()
        hex_path(cr, cx, cy, cell_r)
        cr.clip()
        cr.set_source_rgba(0, 0, 0, 0.68)
        cr.rectangle(cx - cell_r, cy + cell_r * 0.30, 2 * cell_r, cell_r * 0.80)
        cr.fill()
        cr.restore()

        cr.select_font_face('Nova Mono', 0, 0)
        cr.set_font_size(13)
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        name = truncate_text(cr, item['name'], cell_r * 1.6)
        te   = cr.text_extents(name)
        cr.move_to(cx - te.width / 2 - te.x_bearing, cy + cell_r * 0.55)
        cr.show_text(name)

        cr.set_font_size(9)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.70)
        badge = item['type'].upper()
        te2   = cr.text_extents(badge)
        cr.move_to(cx - te2.width / 2 - te2.x_bearing, cy + cell_r * 0.72)
        cr.show_text(badge)

        cr.set_line_width(2.0)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.50)
        hex_path(cr, cx, cy, cell_r)
        cr.stroke()

    # ── Drawing ────────────────────────────────────────────────────────

    def _draw(self, area, cr, width, height, data):
        cr.set_source_rgba(*C_BG_DARK)
        cr.paint()

        vw, vh = width, height
        self._update_pan(vw, vh)  # always correct — uses actual draw dimensions

        if self.search.is_active():
            self._draw_grid_dim(cr, vw, vh)
            self.search.draw(cr, vw, vh, 0)
            self._draw_search_bar(cr, vw, vh)
            return

        if self.query:
            self._draw_filter_mode(cr, vw, vh)
            self._draw_search_bar(cr, vw, vh)
            return

        pos, r     = self._get_positions()
        cell_r     = r - BEVEL
        petal_dist = r * math.sqrt(3)

        # Petal slots — cached; only recomputed on selection or layout change
        petal_key = (self.selected, self._pos_cache_key)
        if self._petal_cache_key != petal_key:
            if 0 <= self.selected < len(pos):
                sel_cx0, sel_cy0 = pos[self.selected]
                cache = {}
                for s, (cx, cy) in enumerate(pos):
                    if s == self.selected:
                        continue
                    d = math.hypot(cx - sel_cx0, cy - sel_cy0)
                    ring = math.ceil(d / (petal_dist * 1.05))
                    if ring <= PETAL_RINGS:
                        cache[s] = ring
                self._petal_cache = cache
            else:
                self._petal_cache = {}
            self._petal_cache_key = petal_key
        petal_slots = self._petal_cache  # slot → ring_number

        flower_pb = flower_pb_next = None
        fade_alpha = 1.0
        sel_cx = sel_cy = 0.0

        if 0 <= self.selected < len(pos):
            sel_cx, sel_cy = pos[self.selected]
            item = self.all_items[self.visible[self.selected]]
            pb_cur, pb_next, fade_alpha = self.preview.current_frame()
            raw     = pb_cur or self._pb_cache.get(id(item))
            super_r = petal_dist * PETAL_RINGS + cell_r
            if raw:
                flower_pb = self._get_flower_pb(raw, super_r)
            if pb_next:
                flower_pb_next = self._get_flower_pb(pb_next, super_r)

        # Pan so selected cell is always at screen center
        cr.save()
        cr.translate(self._pan_x, self._pan_y)

        # ── Main grid pass ─────────────────────────────────────────────
        for slot, item_idx in enumerate(self.visible):
            if slot >= len(pos):
                break
            cx, cy   = pos[slot]
            if cy + self._pan_y + r < 0 or cy + self._pan_y - r > vh:
                continue
            if cx + self._pan_x + r < 0 or cx + self._pan_x - r > vw:
                continue
            item     = self.all_items[item_idx]
            selected  = (slot == self.selected)
            petal_ring = petal_slots.get(slot)   # None if not a petal
            is_petal   = petal_ring is not None

            if selected:
                alpha = 1.0
            elif is_petal:
                alpha = 0.30 / petal_ring        # ring 1→0.30, ring 2→0.15, ring 3→0.10…
            else:
                alpha = ITEM_ALPHA_UNSELECTED

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
                ox = sel_cx - flower_pb.get_width()  / 2
                oy = sel_cy - flower_pb.get_height() / 2
                Gdk.cairo_set_source_pixbuf(cr, flower_pb, ox, oy)
                if flower_pb_next:
                    cr.paint_with_alpha(alpha * (1.0 - fade_alpha))
                    Gdk.cairo_set_source_pixbuf(cr, flower_pb_next, ox, oy)
                    cr.paint_with_alpha(alpha * fade_alpha)
                else:
                    cr.paint_with_alpha(alpha)
            else:
                draw_pb = self._get_scaled(item, cell_r)
                if draw_pb:
                    Gdk.cairo_set_source_pixbuf(cr, draw_pb,
                        cx - draw_pb.get_width()  / 2,
                        cy - draw_pb.get_height() / 2)
                    cr.paint_with_alpha(alpha)
                else:
                    cr.set_source_rgba(*C_BG_DARK)
                    cr.paint()
            cr.restore()

        try:
            if 0 <= self.selected < len(pos):
                item = self.all_items[self.visible[self.selected]]
                self._draw_selected_overlay(cr, sel_cx, sel_cy, cell_r, item)
        except Exception as e:
            print(f'[hive] overlay draw error: {e}')

        cr.restore()  # end pan transform

        self._draw_search_bar(cr, vw, vh)

    def _draw_filter_mode(self, cr, vw, vh):
        """Full grid stays in place. Matched cells bright, unmatched dim."""
        pos, r = self._get_positions()
        cell_r = r - BEVEL

        sel_item_idx = (
            self.visible[self.selected]
            if self.visible and 0 <= self.selected < len(self.visible)
            else -1
        )
        sel_cx = sel_cy = 0.0
        has_selected = sel_item_idx >= 0 and sel_item_idx < len(pos)
        if has_selected:
            sel_cx, sel_cy = pos[sel_item_idx]

        cr.save()
        cr.translate(self._pan_x, self._pan_y)

        # ── Draw all cells ─────────────────────────────────────────────
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
            alpha    = 1.0 if selected else (0.88 if matched else 0.08)

            cr.save()
            hex_path(cr, cx, cy, cell_r)
            cr.clip()
            pb = self._pb_cache.get(id(item))
            if pb:
                spb = self._get_scaled(item, cell_r)
                if spb:
                    Gdk.cairo_set_source_pixbuf(cr, spb, cx - spb.get_width() / 2, cy - spb.get_height() / 2)
                    cr.paint_with_alpha(alpha)
                    cr.restore()
                    continue
            cr.set_source_rgba(*C_BG_DARK)
            cr.paint()
            cr.restore()

        if has_selected:
            item = self.all_items[sel_item_idx]
            self._draw_selected_overlay(cr, sel_cx, sel_cy, cell_r, item)

        cr.restore()  # end pan transform

    def _draw_grid_dim(self, cr, vw, vh):
        """Draw all library cells dimmed as a background for the search overlay."""
        if not self.all_items:
            return
        pos, r = self._get_positions()
        cell_r = r - BEVEL

        cr.save()
        cr.translate(self._pan_x, self._pan_y)

        for slot, item in enumerate(self.all_items):
            if slot >= len(pos):
                break
            cx, cy = pos[slot]
            if cy + self._pan_y + r < 0 or cy + self._pan_y - r > vh:
                continue
            if cx + self._pan_x + r < 0 or cx + self._pan_x - r > vw:
                continue
            cr.save()
            hex_path(cr, cx, cy, cell_r)
            cr.clip()
            pb = self._pb_cache.get(id(item))
            if pb:
                spb = self._get_scaled(item, cell_r)
                if spb:
                    Gdk.cairo_set_source_pixbuf(cr, spb,
                        cx - spb.get_width()  / 2,
                        cy - spb.get_height() / 2)
                    cr.paint_with_alpha(0.22)
                    cr.restore()
                    continue
            cr.set_source_rgba(*C_BG_DARK)
            cr.paint()
            cr.restore()

        cr.restore()  # end pan transform
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()

    def _draw_search_bar(self, cr, vw, vh):
        bar_w = vw * 0.58
        bar_h = 78
        cx    = vw / 2
        cy    = bar_h / 2 + 18

        cr.save()
        wide_hex_path(cr, cx, cy, bar_w, bar_h)
        cr.clip()
        cr.set_source_rgba(*C_BG_DARK)
        cr.paint()
        cr.restore()

        cr.select_font_face('CYBERHYPE', 0, 0)
        text = self.query if self.query else 'SEARCH'
        cr.set_font_size(32 if self.query else 22)
        te = cr.text_extents(text)
        tx = cx - te.width / 2 - te.x_bearing
        ty = cy - te.height / 2 - te.y_bearing - (8 if self.query else 0)
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.move_to(tx, ty)
        cr.show_text(text)

        if self.query:
            n_match = len(self.visible)
            count_text = f"{n_match} result{'s' if n_match != 1 else ''}" if n_match else "no results"
            cr.select_font_face('Nova Mono', 0, 0)
            cr.set_font_size(12)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.38)
            te2 = cr.text_extents(count_text)
            cr.move_to(cx - te2.width / 2 - te2.x_bearing, cy + 20)
            cr.show_text(count_text)

        cr.select_font_face('CYBERHYPE', 0, 0)
        cr.set_font_size(32 if self.query else 22)
        if int(time.time() * 2) % 2 == 0 and self.query:
            cur_x = tx + te.x_advance + 5
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
            cr.set_line_width(2)
            cr.move_to(cur_x, ty - 2)
            cr.line_to(cur_x, ty + te.height + 2)
            cr.stroke()

    # ── Interaction ────────────────────────────────────────────────────

    def _slot_at(self, x, y):
        # Convert screen coords to grid space before hit-testing
        gx, gy = x - self._pan_x, y - self._pan_y
        vw, vh = self._viewport_size()
        r = radius(vh)
        for slot, (cx, cy) in enumerate(self._get_positions()[0]):
            if math.hypot(gx - cx, gy - cy) <= r:
                return slot
        return -1

    def _on_motion(self, ctrl, x, y):
        if self.search.is_active():
            return
        slot = self._slot_at(x, y)
        if slot != self._hovered:
            self._hovered = slot
            self.queue_draw()

    def _on_leave(self, ctrl):
        if self._hovered != -1:
            self._hovered = -1
            self.queue_draw()

    def _on_click(self, gesture, n_press, x, y):
        if self.search.is_active():
            vw, vh = self._viewport_size()
            slot = self.search.slot_at(x, y, vw, vh, 0)
            if slot >= 0:
                self.search.selected = slot
                self.queue_draw()
                if n_press >= 2:
                    self.search.activate(self.on_activate)
            return

        vw, vh = self._viewport_size()
        r = radius(vh)
        pos_list, _ = self._get_positions()
        # Convert screen coords to grid space
        gx, gy = x - self._pan_x, y - self._pan_y

        for i, (cx, cy) in enumerate(pos_list):
            if math.hypot(gx - cx, gy - cy) > r:
                continue
            if self.query:
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
            else:
                slot = i
                if slot != self.selected:
                    self.selected = slot
                    self.preview.stop()
                    item = self.all_items[self.visible[slot]]
                    if item['type'] in VIDEO_TYPES:
                        self.preview.start(item, slot)
                    self._update_pan()
                self.queue_draw()
                if n_press >= 2:
                    self.activate()
            return

    # ── Navigation ─────────────────────────────────────────────────────

    def _navigate_spatial(self, direction):
        """Snap to the nearest visible match in the given direction."""
        pos_list, _ = self._get_positions()
        cur_idx = self.visible[self.selected] if 0 <= self.selected < len(self.visible) else -1
        if cur_idx < 0 or cur_idx >= len(pos_list):
            return 0
        cur_cx, cur_cy = pos_list[cur_idx]

        best       = self.selected
        best_score = float('inf')
        for vi, item_idx in enumerate(self.visible):
            if item_idx >= len(pos_list) or vi == self.selected:
                continue
            cx, cy = pos_list[item_idx]
            dx, dy = cx - cur_cx, cy - cur_cy
            if direction == 'left'  and dx >= 0: continue
            if direction == 'right' and dx <= 0: continue
            if direction == 'up'    and dy >= 0: continue
            if direction == 'down'  and dy <= 0: continue
            # Primary-axis distance + cross-axis penalty
            score = (abs(dx) + abs(dy) * 3) if direction in ('left', 'right') else (abs(dy) + abs(dx) * 3)
            if score < best_score:
                best_score = score
                best       = vi
        return best

    def navigate(self, direction):
        n = len(self.visible)
        if not n:
            return
        new = self._navigate_spatial(direction)
        if new != self.selected:
            self.selected = new
            if not self.query:
                self.preview.stop()
                item = self.all_items[self.visible[new]]
                if item['type'] in VIDEO_TYPES:
                    self.preview.start(item, new)
            self._update_pan()
            self.queue_draw()

    # ── Public API ─────────────────────────────────────────────────────

    def activate(self):
        if 0 <= self.selected < len(self.visible):
            self.on_activate(self.all_items[self.visible[self.selected]])

    def get_selected(self):
        if 0 <= self.selected < len(self.visible):
            return self.all_items[self.visible[self.selected]], self.selected
        return None, -1

    def set_items(self, items, selected=0):
        self.all_items   = items
        self.visible     = list(range(len(items)))
        self.selected    = min(selected, max(0, len(items) - 1))
        self._matched = set()
        self.query    = ""
        self._pb_cache.clear()
        self._scaled_cache.clear()
        self._invalidate_layout()
        self._update_size()
        self._update_pan()
        self.queue_draw()
        if any(item.get('artwork') for item in items):
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

    def remove_visible(self, slot):
        if not (0 <= slot < len(self.visible)):
            return
        item_idx = self.visible.pop(slot)
        item     = self.all_items.pop(item_idx)
        self._pb_cache.pop(id(item), None)
        self._scaled_cache = {k: v for k, v in self._scaled_cache.items() if k[0] != id(item)}
        self.visible  = [i if i < item_idx else i - 1 for i in self.visible]
        self.selected = min(self.selected, max(0, len(self.visible) - 1))
        self._invalidate_layout()
        self._update_size()
        self._update_pan()
        self.queue_draw()
