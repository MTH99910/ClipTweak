"""Actual Qt/libmpv staging/navigation and multi-file FFmpeg Apply.
QT_QPA_PLATFORM=offscreen CLIPTWEAK_AUDIO=null python scripts/batch_smoke_test.py
Add --replace to validate atomic MP4 replacement without backups.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from cliptweak.app import Window, configure_application
from cliptweak.core import Adjustments, probe

temp = tempfile.TemporaryDirectory(prefix='cliptweak-batch-gui-')
folder = Path(temp.name)
os.environ['XDG_CONFIG_HOME'] = str(folder / 'config')
paths = [folder / name for name in ('clip1 日本.mp4', 'clip2 $.mp4', 'clip10.mp4')]
for path in paths:
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=25', '-t', '1', '-c:v', 'libx264',
        str(path)], capture_output=True, check=True)
originals = {path: path.read_bytes() for path in paths}
app = QApplication([])
configure_application(app)
w = Window()
w.replace.setChecked('--replace' in sys.argv)
w.show()
errors = []
w.player.error.connect(errors.append)
state = dict(stage=0, start=time.monotonic(), frames=0)
w.player.frame.connect(lambda _: state.update(frames=state['frames']+1))
w.open(paths[0])

def check():
    try:
        assert not errors, errors
        assert time.monotonic()-state['start'] < 35, state
        if state['stage'] == 0 and w.source == paths[0] and state['frames'] > 3:
            w.sliders['brightness'][0].setValue(15)
            assert w.apply.text() == 'Apply (1)'
            state.update(stage=1, before=state['frames'])
            w.navigate(1)
        elif state['stage'] == 1 and w.source == paths[1] and state['frames'] > state['before']+3:
            w.sliders['temperature'][0].setValue(40)
            assert w.apply.text() == 'Apply (2)'
            assert any('staged' in w.list.item(i).text() for i in range(w.list.count()))
            w.apply.click()
            assert len(w.exporter.entries) == 2
            if not w.replace.isChecked():
                w.sliders['temperature'][0].setValue(20)
            state.update(stage=2, before=state['frames'])
        elif state['stage'] == 2 and w.exporter is None:
            assert w.saved, w.message.text()
            if w.replace.isChecked():
                assert len(list(folder.glob('*.mp4'))) == 3
                assert not list(folder.glob('*backup*'))
                assert all(w.settings[path] == Adjustments() for path in paths[:2])
                outputs = paths[:2]
            else:
                outputs = [path.with_name(path.stem+'_corrected.mp4') for path in paths[:2]]
                assert all(path.read_bytes() == originals[path] for path in paths)
                assert w.staged_paths() == [paths[1]], w.staged_paths()
                assert w.apply.text() == 'Apply (1)'
            assert all(output.exists() and probe(output)['video']['width'] == 320 for output in outputs)
            assert paths[2].read_bytes() == originals[paths[2]]
            state.update(stage=3, before=state['frames'])
            w.open_result()
        elif state['stage'] == 3 and w.source == w.saved and state['frames'] > state['before']+3:
            assert w.current() == Adjustments(), 'Saved result corrected twice'
            print(f"PASS: stage two videos via navigation, Apply both, preserve neutral file, snapshot isolation, saved-result playback; replace={w.replace.isChecked()}; Qt={app.platformName()}")
            w.close()
            app.quit()
            state['stage'] = 4
    except Exception as error:
        print('FAIL:', error)
        state['failed'] = True
        w.cancel_save()
        w.player.stop()
        app.quit()

timer = QTimer()
timer.timeout.connect(check)
timer.start(100)
app.exec()
w.pool.waitForDone()
w.thumbnails.waitForDone()
raise SystemExit(1 if state.get('failed') or state['stage'] != 4 else 0)
