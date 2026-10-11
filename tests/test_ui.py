"""Qt events for groove seeking, fine wheel control and batch staging."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint, QPointF, Qt, QSettings
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from cliptweak.app import Window, configure_application
from cliptweak.core import Adjustments, StagedVideo, BatchResult


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_application(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cliptweak-ui-')
        self.patches = [patch('cliptweak.app.Player.start'),
            patch('cliptweak.app.QSettings', return_value=QSettings(
                str(Path(self.temp.name)/'prefs.ini'), QSettings.Format.IniFormat))]
        for item in self.patches:
            item.start()
        self.window = Window()
        self.window.player.command = lambda *args: self.commands.append(args)
        self.commands = []
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_timeline_groove_click_requests_exact_position(self):
        self.window.duration = 100
        slider = self.window.seek
        slider.setValue(0)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                         pos=QPoint(slider.width()*3//4, slider.height()//2))
        self.assertTrue(self.commands)
        command = self.commands[-1]
        self.assertEqual(command[0], 'seek')
        self.assertEqual(command[2], 'absolute+exact')
        self.assertAlmostEqual(command[1], 75, delta=2)

    def test_wheel_changes_only_one_integer_step(self):
        slider = self.window.sliders['temperature'][0]
        initial = slider.value()
        for delta, expected in ((120, initial+1), (-120, initial)):
            event = QWheelEvent(QPointF(slider.rect().center()),
                QPointF(slider.mapToGlobal(slider.rect().center())), QPoint(),
                QPoint(0, delta), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(slider, event)
            self.assertEqual(slider.value(), expected)

    def test_apply_snapshots_all_staged_files_and_preserves_new_edits(self):
        one, two = Path('/tmp/one.mp4'), Path('/tmp/two.mp4')
        w = self.window
        w.infos = {one: {}, two: {}}
        w.settings = {one: Adjustments(tint=.2), two: Adjustments(temperature=.4)}
        w.source = one
        w.update_staged_ui()
        self.assertEqual(w.apply.text(), 'Apply (2)')
        with patch.object(w, 'submit') as submit:
            w.save()
        batch = w.exporter
        self.assertEqual({entry.source for entry in batch.entries}, {one, two})
        w.settings[two] = Adjustments(temperature=.7)
        self.assertEqual(batch.entries[1].settings.temperature, .4)
        # A newly edited file stays staged; failed/unprocessed snapshots do too.
        w.source = None
        w.save_done(BatchResult([(batch.entries[0], Path('/tmp/one_corrected.mp4')),
                                (batch.entries[1], Path('/tmp/two_corrected.mp4'))], [], False))
        self.assertEqual(w.staged_paths(), [two])

    def test_replacement_locks_edits_and_resets_applied_source(self):
        path = Path('/tmp/in-place.mp4')
        w = self.window
        w.infos = {path: {}}
        w.settings = {path: Adjustments(tint=.4)}
        w.source = path
        w.replace.setChecked(True)
        with patch.object(w, 'submit'):
            w.save()
        self.assertFalse(w.adjustment_panel.isEnabled())
        entry = w.exporter.entries[0]
        with patch.object(w, 'open') as reopen:
            w.save_done(BatchResult([(entry, path)], [], False))
        self.assertEqual(w.settings[path], Adjustments())
        self.assertTrue(w.adjustment_panel.isEnabled())
        self.assertFalse(w.staged_paths())
        reopen.assert_called_once()

    def test_transport_icons_have_accessible_names(self):
        for button in (self.window.previous, self.window.play, self.window.next):
            self.assertEqual(button.text(), '')
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.accessibleName())
        self.assertLess(self.window.previous.x(), self.window.play.x())
        self.assertLess(self.window.play.x(), self.window.next.x())
