"""Regressions for audio startup blocking all video rendering."""
import os
import subprocess
import unittest
from unittest.mock import patch
from cliptweak.audio import select_audio_backend

class AudioTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.executable = patch('cliptweak.audio.shutil.which', return_value='/usr/bin/mpv')
        self.executable.start()
    def tearDown(self):
        self.executable.stop()
        self.env.stop()
    def test_working_pipewire_keeps_audio(self):
        with patch('cliptweak.audio.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(select_audio_backend(), ('pipewire', ''))
            self.assertEqual(run.call_count, 1)
            self.assertIn('--ao=pipewire', run.call_args.args[0])
    def test_stalled_pulse_falls_back_to_alsa(self):
        results = [subprocess.CompletedProcess([], 2), subprocess.TimeoutExpired('mpv', 2),
                   subprocess.CompletedProcess([], 0)]
        with patch('cliptweak.audio.subprocess.run', side_effect=results):
            self.assertEqual(select_audio_backend(), ('alsa', ''))
    def test_all_backends_fail_keeps_video_and_reports_silence(self):
        with patch('cliptweak.audio.subprocess.run', side_effect=subprocess.TimeoutExpired('mpv', 2)):
            backend, message = select_audio_backend()
        self.assertEqual(backend, 'null')
        self.assertIn('playing silently', message)
        self.assertIn('pulse timed out', message)
    def test_explicit_silent_mode_never_probes(self):
        os.environ['CLIPTWEAK_AUDIO'] = 'null'
        with patch('cliptweak.audio.subprocess.run') as run:
            self.assertEqual(select_audio_backend()[0], 'null')
            run.assert_not_called()
    def test_invalid_backend_clear_error(self):
        os.environ['CLIPTWEAK_AUDIO'] = 'typo'
        with self.assertRaisesRegex(ValueError, 'CLIPTWEAK_AUDIO'):
            select_audio_backend()

if __name__ == '__main__': unittest.main()
