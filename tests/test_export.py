"""Real FFmpeg integration tests using generated footage, never user files."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from cliptweak.core import Adjustments, Exporter, probe
from unittest.mock import patch

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ExportTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cliptweak-export-')
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make(self, name='source.mp4', extra=None, audio='aac', vf=None):
        path = self.folder / name
        cmd = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=50', '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', audio, '-metadata', 'title=Test title']
        if vf:
            cmd += ['-vf', vf]
        cmd += (extra or []) + [str(path)]
        subprocess.run(cmd, check=True, capture_output=True)
        return path

    def export(self, source, replace=False):
        return Exporter(source, Adjustments(temperature=0.25, tint=-0.2), probe(source), replace).run()

    def test_interlaced_mts_and_remux(self):
        source = self.make('Sony 日本 $; 10.MTS', audio='ac3', vf='tinterlace=interleave_top', extra=['-flags', '+ilme+ildct', '-x264-params', 'tff=1', '-color_primaries', 'bt709', '-colorspace', 'bt709', '-color_trc', 'bt709'])
        info = probe(source)
        self.assertTrue(info['interlaced'])
        result = self.export(source)
        out = probe(result)
        self.assertFalse(out['interlaced'])
        self.assertEqual(out['video']['width'], 320)
        self.assertEqual(out['video']['r_frame_rate'], '25/1')
        self.assertEqual(out['video']['color_space'], 'bt709')
        self.assertEqual([s['codec_name'] for s in out['streams'] if s['codec_type'] == 'audio'], ['ac3'])
        remux = self.folder / 'remux.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-c', 'copy', str(remux)], check=True, capture_output=True)
        self.assertTrue(probe(remux)['interlaced'])
        self.assertFalse(probe(self.export(remux))['interlaced'])

    def test_missing_field_metadata_uses_idet(self):
        source = self.make('untagged.MTS', vf='tinterlace=interleave_top', extra=['-flags', '+ilme+ildct', '-x264-params', 'tff=1'])
        real_run = subprocess.run

        def erase_field_metadata(args, **kwargs):
            result = real_run(args, **kwargs)
            if args[0] == 'ffprobe':
                data = json.loads(result.stdout)
                data['streams'][0]['field_order'] = 'unknown'
                result.stdout = json.dumps(data)
            return result
        with patch('cliptweak.core.subprocess.run', side_effect=erase_field_metadata):
            info = probe(source)
        self.assertTrue(info['interlaced'])
        self.assertEqual(info['detected_parity'], 'tff')
        self.assertEqual(info['field_detection'], '120-frame idet sample')

    def test_variable_frame_timestamps(self):
        source = self.make(vf="select='not(eq(mod(n,3),0))'", extra=['-fps_mode', 'vfr'])
        saved = self.export(source)

        def timestamps(path):
            p = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)], capture_output=True, text=True, check=True)
            return [float(f['best_effort_timestamp_time']) for f in json.loads(p.stdout)['frames']]
        before, after = (timestamps(source), timestamps(saved))
        self.assertEqual(len(before), len(after))
        for a, b in zip(before, after):
            self.assertAlmostEqual(a - before[0], b - after[0], places=4)

    def test_audio_conversion_and_aspect(self):
        source = self.make('pcm.mov', audio='pcm_s16le', vf='setsar=4/3')
        info = probe(self.export(source))
        self.assertEqual(info['video']['sample_aspect_ratio'], '4:3')
        self.assertEqual([s['codec_name'] for s in info['streams'] if s['codec_type'] == 'audio'], ['aac'])
        self.assertEqual(info['format']['tags']['title'], 'Test title')

    def test_mixed_audio_copies_supported_track(self):
        source=self.folder/'mixed.mkv'
        subprocess.run([
            'ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=25',
            '-f','lavfi','-i','sine=frequency=440','-t','1',
            '-map','0:v','-map','1:a','-map','1:a','-c:v','libx264',
            '-c:a:0','aac','-c:a:1','pcm_s16le',str(source),
        ],check=True,capture_output=True)
        saved=self.export(source)
        audio=[s['codec_name'] for s in probe(saved)['streams'] if s['codec_type']=='audio']
        self.assertEqual(audio,['aac','aac'])
        def packet_hash(path):
            return subprocess.run([
                'ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c:a','copy',
                '-f','hash','-hash','md5','-',
            ],check=True,capture_output=True).stdout
        self.assertEqual(packet_hash(source),packet_hash(saved))

    def test_full_range_preserved(self):
        source = self.make(vf='scale=out_range=full', extra=['-color_range', 'pc'])
        self.assertEqual(probe(source)['video']['color_range'], 'pc')
        self.assertEqual(probe(self.export(source))['video']['color_range'], 'pc')

    def test_rotation(self):
        source = self.make()
        rotated = self.folder / 'rotated.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-display_rotation', '90', '-i', str(source), '-c', 'copy', str(rotated)], check=True, capture_output=True)
        info = probe(self.export(rotated))
        self.assertEqual((info['video']['width'], info['video']['height']), (180, 320))
        self.assertFalse(any((s.get('rotation') for s in info['video'].get('side_data_list', []))))

    def test_replace_without_backup_and_unique_names(self):
        source = self.make()
        original = source.read_bytes()
        first = self.export(source)
        second = self.export(source)
        self.assertNotEqual(first, second)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.export(source, True), source)
        self.assertFalse(list(self.folder.glob('*backup*')))
        self.assertNotEqual(source.read_bytes(), original)

    def test_cancel_preserves_source(self):
        source = self.make()
        original = source.read_bytes()
        exporter = Exporter(source, Adjustments(), probe(source), replace=True)

        def cancel(_):
            exporter.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            exporter.run(cancel)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(list(self.folder.glob('*backup*')))
        self.assertFalse(list(self.folder.glob('.videotint-*')))
        self.assertFalse(list(self.folder.glob('*.videotint-lock')))
if __name__ == '__main__':
    unittest.main()
