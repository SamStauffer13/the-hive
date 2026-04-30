#!/usr/bin/python3
"""The Hive — entry point."""
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

sys.path.insert(0, str(Path(__file__).parent))

from launcher import TheHive


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='com.deck.thehive')

    def do_activate(self):
        windows = self.get_windows()
        if windows:
            windows[0].present()
        else:
            TheHive(self).present()


if __name__ == '__main__':
    sys.exit(App().run(sys.argv))
