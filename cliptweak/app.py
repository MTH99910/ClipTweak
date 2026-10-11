"""Native Qt UI; probing, rendering, thumbnails and encoding run off the UI thread."""
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import platform
import PySide6
from collections import deque
from pathlib import Path
from dataclasses import asdict
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QRunnable, QThreadPool, QSize, QSettings, Slot, qVersion,
)
from PySide6.QtGui import QPainter, QIcon, QShortcut, QKeySequence, QImage, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QCheckBox, QProgressBar, QDialog, QPlainTextEdit, QStyle, QStyleOptionSlider,
)
from .core import Adjustments, EXTENSIONS, natural_key, probe, processing_graph, BatchExporter, StagedVideo
from .player import Player
from . import __version__

class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

class Job(QRunnable):

    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(self.signals.progress.emit))
        except Exception as e:
            self.signals.failed.emit(str(e))

class AdjustmentSlider(QSlider):
    """One wheel detent changes one integer unit, regardless of page step."""
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.wheel_remainder = 0
        self.setSingleStep(1)

    def wheelEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        self.wheel_remainder += event.angleDelta().y()
        steps = int(self.wheel_remainder / 120)
        self.wheel_remainder -= steps * 120
        if steps:
            self.setValue(self.value() + steps)
        event.accept()


class SeekSlider(QSlider):
    """Jump directly on groove click, with normal handle dragging retained."""
    seekRequested = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider,
                option, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider,
                    option, QStyle.SubControl.SC_SliderGroove, self)
                span = max(1, groove.width() - handle.width())
                position = round(event.position().x() - groove.x() - handle.width()/2)
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(),
                    self.maximum(), max(0, min(span, position)), span, option.upsideDown))
                super().mousePressEvent(event)
                self.seekRequested.emit()
                event.accept()
                return
        super().mousePressEvent(event)


class Preview(QWidget):

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.image = None
        self.test_pattern = False
        self.paint_count = 0
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        self.paint_count += 1
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.test_pattern:
            colors = ['white', 'yellow', 'cyan', 'green', 'magenta', 'red', 'blue']
            for index, color in enumerate(colors):
                left = self.width() * index // len(colors)
                right = self.width() * (index + 1) // len(colors)
                p.fillRect(left, 0, right - left, self.height(), QColor(color))
            p.fillRect(0, self.height()//2 - 25, self.width(), 50, Qt.GlobalColor.black)
            p.setPen(Qt.GlobalColor.white)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       'Display test — open Diagnostics to restore video')
        elif self.image:
            p.drawImage(self.rect(), self.image)
        else:
            p.setPen(Qt.GlobalColor.lightGray)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Open or drop a video')

    def resizeEvent(self, event):
        self.window.player.size = (max(64, self.width()), max(64, self.height()))
        self.window.position_browser()

    def mouseMoveEvent(self, event):
        if event.position().x() > self.width() - 40:
            self.window.browser.show()
            self.window.browser.raise_()

    def mouseDoubleClickEvent(self, event):
        self.window.fullscreen()

class Window(QMainWindow):
    item_saved = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('ClipTweak')
        self.resize(1200, 800)
        self.setAcceptDrops(True)
        self.player = Player()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.thumbnails = QThreadPool(self)
        self.thumbnails.setMaxThreadCount(2)
        self.jobs = set()
        self.thumbnail_pending = deque()
        self.thumbnail_active = 0
        self.settings = {}
        self.infos = {}
        self.applied_settings = {}
        self.source = None
        self.info = None
        self.files = []
        self.loading_id = 0
        self.exporter = None
        self.processed_saves = set()
        self.item_saved.connect(self.save_item_done, Qt.ConnectionType.QueuedConnection)
        self.saved = None
        self.comparing = False
        self.frames_received = 0
        self.frame_receiver_thread = ''
        self.playback_notice = ''
        self.playback_failure = ''
        self.preferences = QSettings('ClipTweak', 'ClipTweak')
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        layout.addLayout(top)
        self.button(top, 'Open video…', self.choose)
        self.title = QLabel('ClipTweak')
        top.addWidget(self.title, 1)
        self.button(top, 'Fullscreen', self.fullscreen)
        self.button(top, 'Diagnostics', self.show_diagnostics)
        self.preview = Preview(self)
        layout.addWidget(self.preview, 1)
        self.browser = QWidget(self.preview)
        self.browser.setObjectName('browser')
        bl = QVBoxLayout(self.browser)
        self.pin = QCheckBox('Pin folder panel')
        bl.addWidget(self.pin)
        self.list = QListWidget()
        self.list.setIconSize(QSize(144, 81))
        self.list.setSpacing(6)
        bl.addWidget(self.list)
        self.list.itemClicked.connect(lambda item: self.open(Path(item.data(Qt.ItemDataRole.UserRole))))
        self.browser.hide()
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.hide_browser)
        self.hide_timer.start(500)
        transport = QHBoxLayout()
        layout.addLayout(transport)
        self.previous = self.icon_button(transport, QStyle.StandardPixmap.SP_MediaSkipBackward, 'Previous video (Alt+Left)', lambda: self.navigate(-1))
        self.play = self.icon_button(transport, QStyle.StandardPixmap.SP_MediaPlay, 'Play / Pause (Space)', lambda: self.player.command('cycle', 'pause'))
        self.next = self.icon_button(transport, QStyle.StandardPixmap.SP_MediaSkipForward, 'Next video (Alt+Right)', lambda: self.navigate(1))
        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 10000)
        transport.addWidget(self.seek, 1)
        self.seek.sliderReleased.connect(self.seek_to)
        self.seek.seekRequested.connect(self.seek_to)
        self.time = QLabel('00:00 / 00:00')
        transport.addWidget(self.time)
        transport.addWidget(QLabel('Volume'))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setMaximumWidth(120)
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.valueChanged.connect(lambda v: self.player.command('set', 'volume', v))
        transport.addWidget(volume)
        self.adjustment_panel = QWidget()
        controls = QHBoxLayout(self.adjustment_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.adjustment_panel)
        self.sliders = {}
        self.values = {}
        specs = [
            ('brightness', 'Brightness', -50, 50, 0, 100),
            ('contrast', 'Contrast', 50, 200, 100, 100),
            ('gamma', 'Gamma', 40, 250, 100, 100),
            ('saturation', 'Saturation', 0, 200, 100, 100),
            ('tint', 'Tint\nGreen ↔ Magenta', -100, 100, 0, 100),
            ('temperature', 'Temperature\nCool ↔ Warm', -100, 100, 0, 100),
        ]
        for key, label, low, high, neutral, scale in specs:
            column = QVBoxLayout()
            controls.addLayout(column)
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            value = QLabel()
            row.addWidget(value)
            column.addLayout(row)
            slider = AdjustmentSlider()
            slider.setRange(low, high)
            slider.setValue(neutral)
            column.addWidget(slider)
            self.sliders[key] = (slider, scale, neutral)
            self.values[key] = value
            slider.valueChanged.connect(self.changed)
            self.button(column, 'Reset', lambda checked=False, s=slider, n=neutral: s.setValue(n))
        footer = QHBoxLayout()
        layout.addLayout(footer)
        self.reset_all = self.button(footer, 'Reset all', self.reset)
        self.compare = QPushButton('Hold to compare original')
        self.compare.pressed.connect(lambda: self.compare_original(True))
        self.compare.released.connect(lambda: self.compare_original(False))
        footer.addWidget(self.compare)
        self.replace = QCheckBox('Replace MP4 (no backup)')
        self.replace.setChecked(self.preferences.value('replace', False, type=bool))
        self.replace.toggled.connect(lambda v: self.preferences.setValue('replace', v))
        self.replace.setToolTip('Replace MP4 originals after successful validation. No backup is retained. Other containers save a separate MP4.')
        footer.addWidget(self.replace)
        footer.addStretch()
        self.apply = self.button(footer, 'Apply', self.save)
        self.apply.setObjectName('apply')
        self.apply.setEnabled(False)
        saving = QHBoxLayout()
        layout.addLayout(saving)
        self.message = QLabel('Ready')
        self.message.setWordWrap(True)
        saving.addWidget(self.message, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.hide()
        saving.addWidget(self.progress)
        self.cancel = self.button(saving, 'Cancel save', self.cancel_save)
        self.cancel.hide()
        self.open_saved = self.button(saving, 'Open saved result', self.open_result)
        self.open_saved.hide()
        self.player.frame.connect(self.draw, Qt.ConnectionType.QueuedConnection)
        self.player.status.connect(self.status, Qt.ConnectionType.QueuedConnection)
        self.player.error.connect(self.playback_error, Qt.ConnectionType.QueuedConnection)
        self.player.notice.connect(self.audio_notice, Qt.ConnectionType.QueuedConnection)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(70)
        self.filter_timer.timeout.connect(self.update_filter)
        for key, fn in [('Space', lambda: self.player.command('cycle', 'pause')), ('Ctrl+O', self.choose), ('Alt+Left', lambda: self.navigate(-1)), ('Alt+Right', lambda: self.navigate(1)), ('F', self.fullscreen), ('Escape', self.leave_fullscreen), ('Ctrl+R', self.reset), ('Ctrl+Return', self.save), ('Left', lambda: self.player.command('seek', -5, 'relative')), ('Right', lambda: self.player.command('seek', 5, 'relative')), ('C', lambda: self.compare_original(not self.comparing))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(fn)
        self.changed()
        self.player.start()
        self.player.command('set', 'volume', 80)

    def button(self, layout, text, callback):
        b = QPushButton(text)
        b.clicked.connect(callback)
        layout.addWidget(b)
        return b

    def icon_button(self, layout, icon, tooltip, callback):
        button = self.button(layout, '', callback)
        button.setIcon(self.style().standardIcon(icon))
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip.split(' (')[0])
        return button

    def submit(self, function, done, pool=None, failed=None):
        """Retain jobs and deliver results through queued Qt signals."""
        job = Job(function)
        self.jobs.add(job)

        def finish(result):
            self.jobs.discard(job)
            done(result)

        def fail(error):
            self.jobs.discard(job)
            (failed or self.report)(error)
        job.signals.done.connect(finish)
        job.signals.failed.connect(fail)
        (pool or self.pool).start(job)
        return job

    def report(self, error):
        self.message.setText(error if len(error) < 320 else error[-320:])
        self.message.setToolTip(error)

    @Slot(str)
    def playback_error(self, error):
        self.playback_failure = error
        self.report(error)

    @Slot(str)
    def audio_notice(self, message):
        self.playback_notice = message
        self.report(message)

    @Slot(QImage)
    def draw(self, image):
        self.frames_received += 1
        self.frame_receiver_thread = threading.current_thread().name
        self.preview.image = image
        self.preview.update()

    def diagnostics_text(self):
        sample_colors = 'no frame received'
        if self.preview.image is not None:
            image = self.preview.image.scaled(32, 18)
            colors = {image.pixelColor(x, y).rgb()
                      for x in range(image.width()) for y in range(image.height())}
            sample_colors = str(len(colors))
        lines = [
            f'ClipTweak {__version__}',
            f'Application file: {Path(__file__).resolve()}',
            f'Python: {sys.executable}',
            f'Qt platform: {QApplication.platformName()}',
            f'Qt / PySide6: {qVersion()} / {PySide6.__version__}',
            f'Linux: {platform.release()}',
            f'Selected file: {self.source}',
            f'Frames delivered to Qt: {self.frames_received}',
            f'Preview paint events: {self.preview.paint_count}',
            f'Frame receiver thread: {self.frame_receiver_thread}',
            f'Sampled frame colors: {sample_colors}',
            f'Preview size: {self.preview.width()} x {self.preview.height()}',
            f'Preview visible: {self.preview.isVisible()}',
            f'Test pattern: {self.preview.test_pattern}',
            f'Player thread running: {self.player.isRunning()}',
            f'Audio notice: {self.playback_notice}',
            f'Playback error: {self.playback_failure}',
        ]
        lines.extend(f'mpv {key}: {value}' for key, value in self.player.diagnostics.copy().items())
        return '\n'.join(lines)

    def show_diagnostics(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('ClipTweak diagnostics')
        dialog.resize(750, 500)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit(self.diagnostics_text())
        report.setReadOnly(True)
        layout.addWidget(report)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.button(buttons, 'Refresh', lambda: report.setPlainText(self.diagnostics_text()))
        self.button(buttons, 'Copy report', lambda: QApplication.clipboard().setText(report.toPlainText()))
        def test_display():
            self.preview.test_pattern = not self.preview.test_pattern
            self.preview.update()
            dialog.accept()
        self.button(buttons, 'Restore video' if self.preview.test_pattern else 'Test preview display', test_display)
        self.button(buttons, 'Close', dialog.accept)
        dialog.exec()

    def position_browser(self):
        if hasattr(self, 'browser'):
            self.browser.setGeometry(max(0, self.preview.width() - 290), 0, 290, self.preview.height())

    def hide_browser(self):
        if not self.pin.isChecked() and (not self.browser.underMouse()) and (not self.list.underMouse()):
            from PySide6.QtGui import QCursor
            point = self.preview.mapFromGlobal(QCursor.pos())
            if point.x() < self.preview.width() - 40 or not self.preview.rect().contains(point):
                self.browser.hide()

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open video', str(self.source.parent) if self.source else '', 'Video files (' + ' '.join(('*' + e for e in sorted(EXTENSIONS))) + ');;All files (*)')
        if path:
            self.open(Path(path))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any((u.isLocalFile() for u in event.mimeData().urls())):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for u in event.mimeData().urls():
            if u.isLocalFile():
                self.open(Path(u.toLocalFile()))
                break

    def open(self, path, notice=None):
        """Probe asynchronously; ignore results superseded by a later selection."""
        path = path.resolve()
        self.loading_id += 1
        token = self.loading_id
        self.message.setText('Inspecting video…')

        def inspect(_):
            if path.suffix.lower() not in EXTENSIONS:
                raise RuntimeError('Unsupported file type: ' + path.suffix)
            info = probe(path)
            files = sorted((p for p in path.parent.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS), key=natural_key)
            if path not in files:
                files.append(path)
            return (info, files)

        def ready(result):
            if token != self.loading_id:
                return
            self.info, self.files = result
            self.source = path
            self.infos[path] = self.info
            self.settings.setdefault(path, Adjustments())
            self.comparing = False
            self.compare.setText('Hold to compare original')
            self.restore()
            if self.player.isRunning():
                self.playback_failure = ''
            self.player.command('set', 'vf', self.preview_filter())
            self.player.command('loadfile', str(path), 'replace')
            self.player.command('set', 'pause', 'no')
            self.title.setText(path.name)
            self.update_staged_ui()
            self.message.setText(notice or ('Saved result loaded with neutral settings: ' + path.name if path == self.saved else 'Interlaced: frame-rate-preserving BWDIF in preview and save' if self.info['interlaced'] else 'Ready — SDR video'))
            if self.playback_failure or self.playback_notice:
                self.report(self.playback_failure or self.playback_notice)
            self.populate()
        self.submit(inspect, ready, failed=lambda e: self.report(e) if token == self.loading_id else None)

    def populate(self):
        self.list.clear()
        self.thumbnail_pending.clear()
        for path in self.files:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
            if path == self.source:
                self.list.setCurrentItem(item)
            self.thumbnail_pending.append((self.loading_id, path))
        self.update_staged_ui()
        self.pump_thumbnails()

    def pump_thumbnails(self):
        """Keep two thumbnail jobs in flight and discard old pending folders."""
        while self.thumbnail_pending and self.thumbnail_active < 2:
            token, path = self.thumbnail_pending.popleft()
            self.thumbnail_active += 1

            def make(_, p=path):
                stat = p.stat()
                key = hashlib.sha256(f'{p}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
                cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'cliptweak'
                cache.mkdir(parents=True, exist_ok=True)
                target = cache / (key + '.jpg')
                if not target.exists():
                    import tempfile
                    fd, name = tempfile.mkstemp(prefix=key, suffix='.jpg', dir=cache)
                    os.close(fd)
                    out = Path(name)
                    try:
                        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(p), '-frames:v', '1', '-vf', 'thumbnail,scale=144:81:force_original_aspect_ratio=decrease', '-threads', '1', str(out)], check=True, capture_output=True, timeout=25)
                        out.replace(target)
                    finally:
                        out.unlink(missing_ok=True)
                return target

            def finish(result, t=token, p=path, error=None):
                self.thumbnail_active -= 1
                if t == self.loading_id:
                    for i in range(self.list.count()):
                        item = self.list.item(i)
                        if item.data(Qt.ItemDataRole.UserRole) == str(p):
                            if error:
                                item.setToolTip('Thumbnail unavailable: ' + error)
                            else:
                                item.setIcon(QIcon(str(result)))
                            break
                self.pump_thumbnails()
            self.submit(make, finish, self.thumbnails, failed=lambda error, f=finish: f(None, error=error))

    def navigate(self, offset):
        if self.source in self.files:
            index = self.files.index(self.source) + offset
            if 0 <= index < len(self.files):
                self.open(self.files[index])

    def current(self):
        return Adjustments(**{k: s.value() / scale for k, (s, scale, _) in self.sliders.items()})

    def changed(self):
        for k, (s, scale, _) in self.sliders.items():
            self.values[k].setText(f'{s.value() / scale:.2f}')
        if self.source:
            self.settings[self.source] = self.current()
        self.update_staged_ui()
        self.filter_timer.start()

    def restore(self):
        for k, v in asdict(self.settings[self.source]).items():
            s, scale, _ = self.sliders[k]
            s.blockSignals(True)
            s.setValue(round(v * scale))
            s.blockSignals(False)
        self.changed()

    def reset(self):
        for s, _, neutral in self.sliders.values():
            s.setValue(neutral)

    def grade_filter(self):
        info = dict(self.info, interlaced=False)
        graph = 'null' if self.comparing else processing_graph(self.current(), info)
        return '@grade:lavfi=[' + graph + ']'

    def preview_filter(self):
        filters = []
        if self.info['interlaced']:
            parity = self.info.get('detected_parity', 'auto')
            filters.append(f'@deint:lavfi=[bwdif=mode=send_frame:parity={parity}:deint=all]')
        filters.append(self.grade_filter())
        return ','.join(filters)

    def update_filter(self):
        if self.info:
            # Replace only the labelled color filter. Preserve BWDIF's temporal
            # history while dragging sliders, and redraw paused frames via mpv.
            self.player.command('vf', 'add', self.grade_filter())

    def compare_original(self, enabled):
        self.comparing = enabled
        self.compare.setText('Original shown' if enabled else 'Hold to compare original')
        self.update_filter()

    @Slot(float, float, bool)
    def status(self, pos, duration, paused):
        self.duration = duration
        self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay if paused else QStyle.StandardPixmap.SP_MediaPause))
        if not self.seek.isSliderDown():
            self.seek.setValue(int(pos / duration * 10000) if duration else 0)

        def stamp(x):
            return f'{int(x) // 3600:02}:{int(x) % 3600 // 60:02}:{int(x) % 60:02}'
        self.time.setText(stamp(pos) + ' / ' + stamp(duration))

    def seek_to(self):
        self.player.command('seek', self.seek.value() / 10000 * getattr(self, 'duration', 0), 'absolute+exact')

    def fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def leave_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()

    def staged_paths(self):
        neutral = Adjustments()
        return [path for path, settings in self.settings.items()
                if path in self.infos and settings != neutral
                and settings != self.applied_settings.get(path)]

    def update_staged_ui(self):
        paths = set(self.staged_paths())
        self.apply.setText(f'Apply ({len(paths)})' if paths else 'Apply')
        self.apply.setEnabled(bool(paths) and self.exporter is None)
        self.apply.setToolTip('Save all staged videos in this session')
        # In-place batch sources cannot be edited until their new pixels load.
        locked = bool(self.exporter and self.exporter.replace and any(
            entry.source == self.source and entry.source.suffix.lower() == '.mp4'
            for entry in self.exporter.entries))
        self.adjustment_panel.setEnabled(not locked)
        self.reset_all.setEnabled(not locked)
        for index in range(self.list.count()):
            item = self.list.item(index)
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            item.setText(path.name + (' • staged' if path in paths else ''))

    def save(self):
        """Snapshot every staged file before starting the sequential batch."""
        if self.exporter:
            return
        paths = self.staged_paths()
        if not paths:
            return
        self.exporter = BatchExporter([StagedVideo(path, self.settings[path],
            self.infos[path]) for path in paths], self.replace.isChecked())
        exporter = self.exporter
        self.processed_saves.clear()
        self.update_staged_ui()
        self.replace.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel.show()
        self.open_saved.hide()
        self.message.setText(f'Saving {len(paths)} staged video(s)…')
        job = self.submit(lambda progress: exporter.run(progress, self.item_saved.emit), self.save_done, failed=self.save_failed)
        job.signals.progress.connect(self.save_progress)

    def save_progress(self, value):
        self.progress.setValue(value)
        if self.exporter and self.exporter.current_source:
            self.message.setText(f'Saving {self.exporter.current_source.name} — {value}% of batch')

    def finish_save_ui(self):
        self.exporter = None
        self.replace.setEnabled(True)
        self.progress.hide()
        self.cancel.hide()
        self.update_staged_ui()

    @Slot(object, object)
    def save_item_done(self, entry, path):
        self.processed_saves.add((entry.source, path))
        self.saved = path
        if path == entry.source:
            self.settings[path] = Adjustments()
            self.applied_settings.pop(path, None)
            self.infos.pop(path, None)
        else:
            self.applied_settings[entry.source] = entry.settings
            self.settings[path] = Adjustments()
        self.update_staged_ui()

    def save_done(self, result):
        reload_current = False
        for entry, path in result.saved:
            if (entry.source, path) not in self.processed_saves:
                self.save_item_done(entry, path)
            reload_current |= path == entry.source == self.source
        self.finish_save_ui()
        self.open_saved.setVisible(bool(result.saved))
        detail = f'Saved {len(result.saved)} video(s)'
        if result.failed:
            detail += f'; {len(result.failed)} failed (still staged)'
        if result.cancelled:
            detail += '; cancelled — remaining files are still staged'
        self.message.setText(detail)
        self.message.setToolTip('\n'.join(f'{path}: {error}' for path, error in result.failed))
        if reload_current:
            self.open(self.source, notice=detail)
        elif self.source:
            additions = [path for _, path in result.saved if path.parent == self.source.parent]
            self.files = sorted(set(self.files + additions), key=natural_key)
            self.populate()

    def save_failed(self, error):
        self.finish_save_ui()
        self.report(error)

    def cancel_save(self):
        if self.exporter:
            self.exporter.cancelled.set()
            self.message.setText('Cancelling save…')

    def open_result(self):
        if self.saved:
            self.settings[self.saved] = Adjustments()
            self.open(self.saved)

    def closeEvent(self, event):
        if self.exporter:
            self.cancel_save()
            self.message.setText('Cancelling save; close again after it finishes')
            event.ignore()
            return
        self.thumbnail_pending.clear()
        self.thumbnails.clear()
        self.player.stop()
        self.pool.waitForDone()
        self.thumbnails.waitForDone()
        event.accept()

def configure_application(app):
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget { background:#202227; color:#e5e7eb; } QPushButton { background:#343841; border:1px solid #505660; padding:7px; border-radius:4px; } QPushButton:hover { background:#454c58; } QPushButton:disabled { color:#777; } QSlider::groove:horizontal { height:5px; background:#4b515a; } QSlider::handle:horizontal { width:14px; margin:-5px 0; background:#83b7ee; border-radius:7px; } QPushButton#apply { background:#2868a9; font-weight:bold; padding:10px 32px; } QWidget#browser { background:#2b2e35; border:1px solid #555; } QListWidget::item:selected { background:#2868a9; }')

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('ClipTweak')
    missing = [x for x in ['ffmpeg', 'ffprobe'] if not shutil.which(x)]
    if missing:
        QMessageBox.critical(None, 'Missing dependencies', 'Install: ' + ', '.join(missing))
        return 1
    configure_application(app)
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.open(Path(sys.argv[1]))
    return app.exec()
