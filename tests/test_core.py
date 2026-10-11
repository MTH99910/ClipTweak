import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from video_tint.core import Adjustments, Exporter, filter_graph, natural_key, probe, unique_path

class CoreTests(unittest.TestCase):

    def test_neutral_and_mapping(self):
        graph = filter_graph(Adjustments())
        self.assertIn('brightness=0.000000:contrast=1.000000:gamma=1.000000:saturation=1.000000', graph)
        self.assertIn('rm=0.000000:gm=0.000000:bm=0.000000', graph)
        warm = filter_graph(Adjustments(temperature=1))
        self.assertIn('rm=0.120000:gm=0.000000:bm=-0.120000', warm)
        magenta = filter_graph(Adjustments(tint=1))
        self.assertIn('rm=0.050000:gm=-0.100000:bm=0.050000', magenta)
        self.assertLess(graph.index('eq='), graph.index('colorbalance='))
        self.assertTrue(filter_graph(Adjustments(), True).startswith('bwdif=mode=send_frame'))

    def test_names_and_natural_order(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / '日本 $(file) 2.MTS'
            source.touch()
            one = unique_path(source)
            one.touch()
            two = unique_path(source)
            two.touch()
            self.assertEqual(unique_path(source).name, '日本 $(file) 2_corrected_3.mp4')
            self.assertEqual(sorted([Path('a10.MTS'), Path('a2.MTS')], key=natural_key)[0].name, 'a2.MTS')

    def test_invalid_settings_and_symlink_names(self):
        with self.assertRaises(ValueError):
            Adjustments(gamma=float('nan'))
        with self.assertRaises(ValueError):
            Adjustments(temperature=2)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.mp4'
            (Path(folder)/'source_corrected.mp4').symlink_to('missing.mp4')
            self.assertEqual(unique_path(source).name,'source_corrected_2.mp4')

    def test_probe_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'invalid.mp4'
            p.write_text('not a video')
            with self.assertRaises(RuntimeError):
                probe(p)

    def test_snapshot_and_lock(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'source.mp4'
            source.write_bytes(b'original')
            settings = Adjustments()
            exporter = Exporter(source, settings, {})
            settings.gamma = 2
            self.assertEqual(exporter.settings.gamma, 1)
            lock = source.with_name('source_corrected.mp4.videotint-lock')
            lock.touch()
            with self.assertRaises(FileExistsError):
                exporter.run()
            self.assertEqual(source.read_bytes(), b'original')

    def test_encode_failure_preserves_source(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'broken.mp4'
            source.write_bytes(b'original')
            info = {'streams': [], 'video': {'index': 0}, 'interlaced': False, 'format': {}}
            with self.assertRaises(RuntimeError):
                Exporter(source, Adjustments(), info).run()
            self.assertEqual(source.read_bytes(), b'original')
            self.assertEqual(list(Path(d).iterdir()), [source])
if __name__ == '__main__':
    unittest.main()
