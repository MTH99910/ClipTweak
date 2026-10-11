"""Native Qt UI; probing, rendering, thumbnails and encoding run off the UI thread."""
import hashlib
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from dataclasses import asdict
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QRunnable, QThreadPool, QSize, QSettings,
)
from PySide6.QtGui import QPainter, QIcon, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QCheckBox, QProgressBar,
)
from .core import Adjustments, EXTENSIONS, natural_key, probe, processing_graph, Exporter
from .player import Player

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

class Preview(QWidget):

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.image = None
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.image:
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

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Video Tint')
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
        self.source = None
        self.info = None
        self.files = []
        self.loading_id = 0
        self.exporter = None
        self.saved = None
        self.comparing = False
        self.playback_notice = ''
        self.playback_failure = ''
        self.preferences = QSettings('VideoTint', 'VideoTint')
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        layout.addLayout(top)
        self.button(top, 'Open video…', self.choose)
        self.button(top, '◀ Previous', lambda: self.navigate(-1))
        self.button(top, 'Next ▶', lambda: self.navigate(1))
        self.title = QLabel('Video Tint')
        top.addWidget(self.title, 1)
        self.button(top, 'Fullscreen', self.fullscreen)
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
        self.play = self.button(transport, 'Play / Pause', lambda: self.player.command('cycle', 'pause'))
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 10000)
        transport.addWidget(self.seek, 1)
        self.seek.sliderReleased.connect(self.seek_to)
        self.time = QLabel('00:00 / 00:00')
        transport.addWidget(self.time)
        transport.addWidget(QLabel('Volume'))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setMaximumWidth(120)
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.valueChanged.connect(lambda v: self.player.command('set', 'volume', v))
        transport.addWidget(volume)
        controls = QHBoxLayout()
        layout.addLayout(controls)
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
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(neutral)
            column.addWidget(slider)
            self.sliders[key] = (slider, scale, neutral)
            self.values[key] = value
            slider.valueChanged.connect(self.changed)
            self.button(column, 'Reset', lambda checked=False, s=slider, n=neutral: s.setValue(n))
        footer = QHBoxLayout()
        layout.addLayout(footer)
        self.button(footer, 'Reset all', self.reset)
        self.compare = QPushButton('Hold to compare original')
        self.compare.pressed.connect(lambda: self.compare_original(True))
        self.compare.released.connect(lambda: self.compare_original(False))
        footer.addWidget(self.compare)
        self.replace = QCheckBox('Replace MP4 (keep backup)')
        self.replace.setChecked(self.preferences.value('replace', False, type=bool))
        self.replace.toggled.connect(lambda v: self.preferences.setValue('replace', v))
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
        self.player.frame.connect(self.draw)
        self.player.status.connect(self.status)
        self.player.error.connect(self.playback_error)
        self.player.notice.connect(self.audio_notice)
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

    def playback_error(self, error):
        self.playback_failure = error
        self.report(error)

    def audio_notice(self, message):
        self.playback_notice = message
        self.report(message)

    def draw(self, image):
        self.preview.image = image
        self.preview.update()

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

    def open(self, path):
        """Probe asynchronously; ignore results superseded by a later selection."""
        path = path.absolute()
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
            self.apply.setEnabled(self.exporter is None)
            self.message.setText('Saved result loaded with neutral settings: ' + path.name if path == self.saved else 'Interlaced: frame-rate-preserving BWDIF in preview and save' if self.info['interlaced'] else 'Ready — SDR video')
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
        self.pump_thumbnails()

    def pump_thumbnails(self):
        """Keep two thumbnail jobs in flight and discard old pending folders."""
        while self.thumbnail_pending and self.thumbnail_active < 2:
            token, path = self.thumbnail_pending.popleft()
            self.thumbnail_active += 1

            def make(_, p=path):
                stat = p.stat()
                key = hashlib.sha256(f'{p}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
                cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'video-tint'
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

    def preview_filter(self):
        if self.comparing:
            if not self.info['interlaced']:
                return ''
            parity = self.info.get('detected_parity', 'auto')
            return f'@grade:lavfi=[bwdif=mode=send_frame:parity={parity}:deint=all]'
        return '@grade:lavfi=[' + processing_graph(self.current(), self.info) + ']'

    def update_filter(self):
        if self.info:
            self.player.command('set', 'vf', self.preview_filter())

    def compare_original(self, enabled):
        self.comparing = enabled
        self.compare.setText('Original shown' if enabled else 'Hold to compare original')
        self.update_filter()

    def status(self, pos, duration, paused):
        self.duration = duration
        self.play.setText('Play' if paused else 'Pause')
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

    def save(self):
        """Snapshot the current file and adjustments before starting an encode."""
        if not self.source or self.exporter:
            return
        self.exporter = Exporter(self.source, self.current(), self.info, self.replace.isChecked())
        exporter = self.exporter
        self.apply.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel.show()
        self.open_saved.hide()
        self.message.setText('Saving settings snapshot…')
        job = self.submit(lambda progress: exporter.run(progress), self.save_done, failed=self.save_failed)
        job.signals.progress.connect(self.progress.setValue)

    def save_done(self, path):
        self.exporter = None
        self.saved = path
        self.settings[path] = Adjustments()
        self.apply.setEnabled(True)
        self.progress.hide()
        self.cancel.hide()
        self.open_saved.show()
        self.message.setText('Saved: ' + str(path))
        if path == self.source:
            self.open(path)

    def save_failed(self, error):
        self.exporter = None
        self.apply.setEnabled(self.source is not None)
        self.progress.hide()
        self.cancel.hide()
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

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Video Tint')
    missing = [x for x in ['ffmpeg', 'ffprobe'] if not shutil.which(x)]
    if missing:
        QMessageBox.critical(None, 'Missing dependencies', 'Install: ' + ', '.join(missing))
        return 1
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget { background:#202227; color:#e5e7eb; } QPushButton { background:#343841; border:1px solid #505660; padding:7px; border-radius:4px; } QPushButton:hover { background:#454c58; } QPushButton:disabled { color:#777; } QSlider::groove:horizontal { height:5px; background:#4b515a; } QSlider::handle:horizontal { width:14px; margin:-5px 0; background:#83b7ee; border-radius:7px; } QPushButton#apply { background:#2868a9; font-weight:bold; padding:10px 32px; } QWidget#browser { background:#2b2e35; border:1px solid #555; } QListWidget::item:selected { background:#2868a9; }')
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.open(Path(sys.argv[1]))
    return app.exec()
