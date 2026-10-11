"""Real libmpv regression: regional numeric locale returns a null player."""
import ctypes.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(ctypes.util.find_library('mpv'), 'libmpv required')
class PlayerLocaleTests(unittest.TestCase):
    def test_qt_desktop_locale_is_normalized_before_mpv_creation(self):
        # Isolate the process-global locale and QApplication from the test runner.
        program = '''
import ctypes as C
import ctypes.util
import locale
from PySide6.QtWidgets import QApplication
from video_tint.player import Player
app = QApplication([])
try:
    locale.setlocale(locale.LC_NUMERIC, 'en_GB.UTF-8')
except locale.Error:
    raise SystemExit(77)
lib = C.CDLL(ctypes.util.find_library('mpv'))
lib.mpv_create.argtypes = []
lib.mpv_create.restype = C.c_void_p
lib.mpv_terminate_destroy.argtypes = [C.c_void_p]
lib.mpv_terminate_destroy.restype = None
assert not lib.mpv_create(), 'Did not reproduce null player with regional locale'
other_categories = [locale.LC_CTYPE, locale.LC_TIME, locale.LC_COLLATE,
                    locale.LC_MONETARY, locale.LC_MESSAGES]
before = [locale.setlocale(category) for category in other_categories]
player = Player()
assert player.diagnostics['numeric_locale_before'] == 'en_GB.UTF-8'
assert locale.setlocale(locale.LC_NUMERIC) == 'C'
assert before == [locale.setlocale(category) for category in other_categories]
handle = lib.mpv_create()
assert handle, 'libmpv creation still fails after numeric locale reset'
lib.mpv_terminate_destroy(handle)
print('PASS: reproduced null player; locale fix creates real libmpv player')
'''
        result = subprocess.run(
            [sys.executable, '-c', program],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 77:
            self.skipTest('en_GB.UTF-8 locale is not installed')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)


if __name__ == '__main__':
    unittest.main()
