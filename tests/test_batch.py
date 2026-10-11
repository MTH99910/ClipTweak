"""Real saves from immutable multi-file snapshots and partial failure/cancel."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from cliptweak.core import Adjustments, BatchExporter, StagedVideo, probe


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cliptweak-batch-')
        self.folder = Path(self.temp.name)
        self.paths = []
        for name in ('one 日本.mp4', 'two $.mp4'):
            path = self.folder / name
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=96x64:rate=25', '-t', '0.4', '-c:v', 'libx264',
                str(path)], check=True, capture_output=True)
            self.paths.append(path)

    def tearDown(self):
        self.temp.cleanup()

    def batch(self, replace=False):
        return BatchExporter([StagedVideo(path, Adjustments(temperature=.4), probe(path))
                              for path in self.paths], replace)

    def test_all_files_saved_with_snapshot(self):
        originals = [path.read_bytes() for path in self.paths]
        entries = [StagedVideo(path, Adjustments(tint=.3), probe(path)) for path in self.paths]
        batch = BatchExporter(entries)
        entries[1].settings.tint = -.8
        entries[1].info['video']['index'] = 99
        self.assertEqual(batch.entries[1].settings.tint, .3)
        self.assertNotEqual(batch.entries[1].info['video']['index'], 99)
        progress = []
        result = batch.run(progress.append)
        self.assertFalse(result.failed)
        self.assertFalse(result.cancelled)
        self.assertEqual(len(result.saved), 2)
        self.assertEqual(progress[-1], 100)
        self.assertTrue(all(a <= b for a, b in zip(progress, progress[1:])))
        for path, original, (_, output) in zip(self.paths, originals, result.saved):
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(output.exists())
            self.assertEqual(probe(output)['video']['width'], 96)

    def test_failed_file_does_not_stop_other_files(self):
        batch = self.batch()
        self.paths[0].write_bytes(b'broken source')
        result = batch.run()
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0][0], self.paths[0])
        self.assertEqual(len(result.saved), 1)
        self.assertEqual(result.saved[0][0].source, self.paths[1])
        self.assertFalse(list(self.folder.glob('*.videotint-lock')))

    def test_cancel_stops_active_and_pending_replacements(self):
        batch = self.batch(replace=True)
        originals = [path.read_bytes() for path in self.paths]
        result = batch.run(lambda _: batch.cancelled.set())
        self.assertTrue(result.cancelled)
        self.assertFalse(result.saved)
        self.assertEqual([path.read_bytes() for path in self.paths], originals)
        self.assertFalse(list(self.folder.glob('*backup*')))
        self.assertFalse(list(self.folder.glob('.videotint-*')))

    def test_replace_both_without_extra_files(self):
        batch = self.batch(replace=True)
        result = batch.run()
        self.assertEqual(len(result.saved), 2)
        self.assertFalse(result.failed)
        self.assertEqual(set(self.folder.iterdir()), set(self.paths))
        self.assertTrue(all(path == entry.source for entry, path in result.saved))
