"""Actual Qt/libmpv playback, paused correction and GUI export smoke test.
Run: QT_QPA_PLATFORM=offscreen python scripts/smoke_test.py
"""
import os, sys, tempfile, subprocess, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from video_tint.app import Window, configure_application
from video_tint.core import probe, processing_graph
temp = tempfile.TemporaryDirectory(prefix='video-tint-smoke-')
interlaced = '--interlaced' in sys.argv
extension = '.MTS' if interlaced else '.mp4'
source = Path(temp.name) / ('Sony sample 日本 $ 2' + extension)
command = [
    'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=320x180:rate=' + ('50' if interlaced else '25'),
    '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '4',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'ac3' if interlaced else 'aac',
]
if interlaced:
    command += ['-vf', 'tinterlace=interleave_top', '-flags', '+ilme+ildct',
                '-x264-params', 'tff=1']
subprocess.run(command + ['-y', str(source)], check=True)
import shutil
next_source = source.with_name('Sony sample 日本 $ 10' + extension)
shutil.copyfile(source, next_source)
app = QApplication([])
configure_application(app)
window = Window()
window.resize(900, 600)
window.show()
errors = []
window.player.error.connect(errors.append)
state = {'stage': 0, 'start': time.monotonic(), 'frames': 0}
window.player.status.connect(lambda pos, duration, paused: state.update(position_seconds=pos))
window.player.frame.connect(lambda image: state.update(frames=state['frames'] + 1))
window.open(source)

def check():
    try:
        if errors:
            raise AssertionError(errors)
        if time.monotonic() - state['start'] > 45:
            raise AssertionError(f'Timeout: {state}, {window.message.text()}')
        stage = state['stage']
        if stage == 0 and window.info and (getattr(window, 'duration', 0) > 0) and (state['frames'] > 3):
            assert window.files == [source, next_source], 'Folder ordering is not natural'
            assert window.info['interlaced'] == interlaced, 'Incorrect field detection'
            window.player.command('set', 'pause', 'yes')
            state.update(stage=1, wait=time.monotonic())
        elif stage == 1 and time.monotonic() - state['wait'] > 0.5:
            assert window.frame_receiver_thread == 'MainThread', 'Frames delivered outside the GUI thread'
            # Check the actual QWidget surface, with the production stylesheet.
            image = window.preview.image
            assert all(value == 255 for value in bytes(image.bits())[3::4]), 'Undefined frame alpha'
            painted = window.preview.grab().toImage()
            colors = {painted.pixelColor(x, y).rgb()
                      for x in range(0, painted.width(), max(1, painted.width()//20))
                      for y in range(0, painted.height(), max(1, painted.height()//20))}
            assert len(colors) > 10, 'Qt preview widget painted a blank surface'
            state['before'] = window.preview.image.copy()
            state['frames_before'] = state['frames']
            state['position'] = window.seek.value()
            window.sliders['brightness'][0].setValue(25)
            window.sliders['temperature'][0].setValue(35)
            state.update(stage=2, wait=time.monotonic())
        elif stage == 2 and time.monotonic() - state['wait'] > 1:
            assert state['frames'] > state['frames_before'], 'Paused preview did not redraw'
            assert window.preview.image != state['before'], 'Adjustment did not change preview'
            assert abs(window.seek.value() - state['position']) < 100, 'Position changed during correction'
            state['frame_number'] = round(state['position_seconds'] * 25)
            state['screenshot'] = Path(temp.name) / 'preview.png'
            window.player.command('screenshot-to-file', str(state['screenshot']), 'video')
            state['snapshot']=window.current()
            window.save()
            window.sliders['brightness'][0].setValue(-15)
            state['stage'] = 3
        elif stage == 3 and window.saved and (not window.exporter):
            info = probe(window.saved)
            assert info['video']['width'] == 320
            assert not info['interlaced'], 'Saved output should be progressive'
            assert any((s['codec_type'] == 'audio' for s in info['streams']))
            assert state['screenshot'].exists(), 'Preview screenshot was not written'
            decoded = Path(temp.name) / 'saved.png'
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(window.saved), '-vf', f"select=eq(n\\,{state['frame_number']})", '-frames:v', '1', str(decoded)], check=True)
            comparison = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(state['screenshot']), '-i', str(decoded), '-lavfi', 'ssim', '-f', 'null', '-'], capture_output=True, text=True, check=True)
            import re
            state['ssim'] = float(re.search('All:([0-9.]+)', comparison.stderr).group(1))
            reference = Path(temp.name) / 'reference.png'
            graph=processing_graph(state['snapshot'],probe(source)) + f",select=eq(n\\,{state['frame_number']})"
            subprocess.run(['ffmpeg','-v','error','-i',str(source),'-vf',graph,'-frames:v','1',str(reference)],check=True)
            reference_compare=subprocess.run(['ffmpeg','-hide_banner','-i',str(state['screenshot']),'-i',str(reference),'-lavfi','ssim','-f','null','-'],capture_output=True,text=True,check=True)
            reference_ssim=float(re.search('All:([0-9.]+)',reference_compare.stderr).group(1))
            assert reference_ssim > .98, f'Preview differs from shared filter reference: {reference_ssim}'
            assert state['ssim'] > .98, f"Preview/output mismatch: SSIM={state['ssim']}"
            assert not window.list.currentItem().icon().isNull(), 'Asynchronous thumbnail missing'
            state['saved'] = window.saved
            state['frames_before'] = state['frames']
            window.open_result()
            state['stage'] = 4
        elif stage == 4 and window.source == state['saved'] and (state['frames'] > state['frames_before'] + 2):
            assert window.current().brightness == 0, 'Saved result got correction twice'
            assert window.current().temperature == 0
            window.open(source)
            state['stage'] = 5
        elif stage == 5 and window.source == source and (window.current().brightness == -0.15):
            assert window.current().temperature == 0.35, 'Per-video settings not restored'
            window.navigate(1)
            state['stage'] = 6
        elif stage == 6 and window.source == state['saved'] and (window.current().brightness == 0):
            window.navigate(-1)
            state['stage'] = 7
        elif stage == 7 and window.source == source and (window.current().brightness == -0.15):
            print(f"PASS: playback ({state['frames']} frames), paused live correction without seek, FFmpeg save, audio, saved-result playback, neutral reset; SSIM={state['ssim']:.5f}; thumbnails, natural navigation, per-video state, immutable export; interlaced={interlaced}; Qt={app.platformName()}")
            window.close()
            app.quit()
            state['stage'] = 8
    except Exception as e:
        print('FAIL:', e)
        state['failed'] = True
        window.cancel_save()
        window.player.stop()
        app.quit()
timer = QTimer()
timer.timeout.connect(check)
timer.start(100)
app.exec()
window.pool.waitForDone()
window.thumbnails.waitForDone()
temp.cleanup()
raise SystemExit(1 if state.get('failed') or state['stage'] != 8 else 0)
