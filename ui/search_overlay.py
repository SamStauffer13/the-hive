import math
import time
import gi
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib
from .hex_geometry import (
    hex_path, sr_dynamic_geo, sr_item_positions, clamp_nav, truncate_text, BEVEL
)
from . import scale_pixbuf_for_hex
from constants import seeder_color


class SearchOverlay:
    """Manages search result state and draws the fullscreen search overlay."""

    def __init__(self, on_redraw):
        self._on_redraw    = on_redraw
        self.items         = []
        self.selected      = 0
        self.loading       = False
        self.query         = ""
        self._pb_cache     = {}
        self._scaled_cache = {}

    # ── State ──────────────────────────────────────────────────────────

    def set_loading(self, query):
        self.query    = query
        self.items    = []
        self.selected = 0
        self.loading  = True
        self._on_redraw()

    def set_results(self, items, query):
        if self.query != query:
            return
        self.items         = items
        self.selected      = 0
        self.loading       = False
        self._pb_cache     = {}
        self._scaled_cache = {}
        self._on_redraw()

    def clear(self):
        self.items         = []
        self.selected      = 0
        self.loading       = False
        self.query         = ""
        self._scaled_cache = {}
        self._on_redraw()

    def is_active(self):
        return bool(self.items or self.loading)

    def get_selected_item(self):
        if 0 <= self.selected < len(self.items):
            return self.items[self.selected]
        return None

    def store_pixbuf(self, item_id, pb):
        self._pb_cache[item_id] = pb
        self._on_redraw()
        return False

    # ── Navigation ─────────────────────────────────────────────────────

    def navigate(self, direction, vw, vh):
        n = len(self.items)
        if not n:
            return
        _, ncols, _ = sr_dynamic_geo(n, vw, vh)
        self.selected = clamp_nav(direction, self.selected, n, ncols)
        self._on_redraw()

    def activate(self, on_activate):
        item = self.get_selected_item()
        if item and on_activate:
            on_activate(item)

    # ── Hit testing ────────────────────────────────────────────────────

    def slot_at(self, x, y, vw, vh, scroll_y):
        if not self.items:
            return -1
        positions, R = sr_item_positions(len(self.items), vw, vh, scroll_y)
        for i, (cx, cy) in enumerate(positions):
            if math.hypot(x - cx, y - cy) <= R:
                return i
        return -1

    # ── Drawing ────────────────────────────────────────────────────────

    def draw(self, cr, vw, vh, scroll_y):
        if self.loading:
            self._draw_loading(cr, vw, vh)
            return
        if not self.items:
            self._draw_empty(cr, vw, vh)
            return
        self._draw_tiles(cr, vw, vh, scroll_y)

    def _draw_loading(self, cr, vw, vh):
        pulse = 0.6 + 0.4 * (math.sin(time.time() * 2.5) + 1) / 2
        cr.select_font_face('CYBERHYPE', 0, 0)
        cr.set_font_size(28)
        cr.set_source_rgba(1.0, 1.0, 1.0, pulse)
        te = cr.text_extents('SEARCHING')
        cr.move_to((vw - te.width) / 2 - te.x_bearing, vh / 2 - te.height / 2 - te.y_bearing)
        cr.show_text('SEARCHING')
        dots = '.' * (int(time.time() * 2) % 4)
        cr.set_font_size(20)
        te2 = cr.text_extents(dots or ' ')
        cr.move_to((vw - te2.width) / 2 - te2.x_bearing, vh / 2 + 30)
        cr.show_text(dots)

    def _draw_empty(self, cr, vw, vh):
        cr.select_font_face('Nova Mono', 0, 0)
        cr.set_font_size(18)
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        te = cr.text_extents('No results found.')
        cr.move_to((vw - te.width) / 2 - te.x_bearing, vh / 2)
        cr.show_text('No results found.')

    def _draw_tiles(self, cr, vw, vh, scroll_y):
        positions, R = sr_item_positions(len(self.items), vw, vh, scroll_y)
        draw_r = R - BEVEL

        for i, item in enumerate(self.items):
            cx, cy = positions[i]
            if cy + R < scroll_y or cy - R > scroll_y + vh:
                continue

            sel = (i == self.selected)

            cr.set_source_rgba(*((0.18, 0.08, 0.35, 0.95) if sel else (0.07, 0.05, 0.11, 0.95)))
            hex_path(cr, cx, cy, draw_r)
            cr.fill()

            pb = self._pb_cache.get(id(item))
            if pb:
                cr.save()
                hex_path(cr, cx, cy, draw_r)
                cr.clip()
                cache_key = (id(item), int(draw_r))
                spb = self._scaled_cache.get(cache_key)
                if spb is None:
                    spb = scale_pixbuf_for_hex(pb, draw_r)
                    self._scaled_cache[cache_key] = spb
                Gdk.cairo_set_source_pixbuf(cr, spb, cx - spb.get_width() / 2, cy - spb.get_height() / 2)
                cr.paint_with_alpha(0.85 if sel else 0.65)
                cr.restore()

            cr.save()
            hex_path(cr, cx, cy, draw_r)
            cr.clip()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.70)
            cr.rectangle(cx - R, cy + R * 0.10, 2 * R, R)
            cr.fill()
            cr.restore()

            cr.set_line_width(2.0 if sel else 1.0)
            cr.set_source_rgba(*((1.0, 1.0, 1.0, 0.60) if sel else (1.0, 1.0, 1.0, 0.20)))
            hex_path(cr, cx, cy, draw_r)
            cr.stroke()

            cr.select_font_face('Nova Mono', 0, 0)

            if item.get('source'):
                cr.set_font_size(11)
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.55)
                te = cr.text_extents(item['source'])
                cr.move_to(cx - te.width / 2 - te.x_bearing, cy - R * 0.48)
                cr.show_text(item['source'])

            year  = item.get('year')
            name  = item['name']
            label = f"{name} ({year})" if year else name
            cr.set_font_size(14)
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            label = truncate_text(cr, label, draw_r * 1.55)
            te    = cr.text_extents(label)
            cr.move_to(cx - te.width / 2 - te.x_bearing, cy + R * 0.42)
            cr.show_text(label)

            seeders  = item.get('seeders', 0)
            seed_col = seeder_color(seeders)
            parts = []
            if item.get('size'):    parts.append(item['size'])
            if seeders:             parts.append(f'{seeders}↑')
            meta = '  ·  '.join(parts)
            if meta:
                cr.set_font_size(11)
                cr.set_source_rgba(*seed_col)
                te = cr.text_extents(meta)
                cr.move_to(cx - te.width / 2 - te.x_bearing, cy + R * 0.60)
                cr.show_text(meta)
