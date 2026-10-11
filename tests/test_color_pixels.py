"""Exercise actual FFmpeg pixels, independently of graph string formatting."""
import subprocess
import unittest
from cliptweak.core import Adjustments, filter_graph


class ColorPixelTests(unittest.TestCase):
    def pixels(self, settings, levels=(16, 64, 126, 192, 235), color_range='tv'):
        # Neutral gray patches in limited/full-range YUV, avoiding codec loss.
        width = len(levels) * 16
        frame = bytes(v for v in levels for _ in range(16)) * 16
        frame += bytes([128]) * (width * 16 * 2)
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'yuv444p',
            '-video_size', f'{width}x16', '-i', 'pipe:0', '-frames:v', '1',
            '-vf', filter_graph(settings, color_range=color_range, matrix='bt709') +
            ',scale=in_color_matrix=bt709,format=rgb24',
            '-f', 'rawvideo', 'pipe:1'], input=frame, capture_output=True, check=True)
        return [tuple(result.stdout[i*16*3+8*3:i*16*3+8*3+3]) for i in range(len(levels))]

    def test_balance_direction_and_black_preservation(self):
        for key in ('temperature', 'tint'):
            positive = self.pixels(Adjustments(**{key: 1}))
            negative = self.pixels(Adjustments(**{key: -1}))
            self.assertLessEqual(max(positive[0]), 2)
            self.assertGreaterEqual(min(positive[-1]), 252)
            r, g, b = positive[2]
            rn, gn, bn = negative[2]
            if key == 'temperature':
                self.assertGreater(r, g); self.assertGreater(g, b)
                self.assertLess(rn, gn); self.assertLess(gn, bn)
            else:
                self.assertGreater(r, g); self.assertGreater(b, g)
                self.assertLess(rn, gn); self.assertLess(bn, gn)

    def test_small_adjustments_are_gentle_and_gradients_monotonic(self):
        levels = tuple(range(16, 236))
        baseline = self.pixels(Adjustments(), levels)
        for key, value in [('brightness', .02), ('temperature', .03), ('tint', .03),
                           ('gamma', 1.05), ('contrast', 1.05)]:
            result = self.pixels(Adjustments(**{key: value}), levels)
            self.assertLess(max(abs(a-b) for old, new in zip(baseline, result)
                                for a, b in zip(old, new)), 9, key)
            for channel in range(3):
                values = [p[channel] for p in result]
                self.assertTrue(all(b >= a-2 for a, b in zip(values, values[1:])), key)
                self.assertGreater(len(set(values)), 190, key)

    def test_extremes_have_useful_correction_strength(self):
        baseline = self.pixels(Adjustments())[2]
        bright = self.pixels(Adjustments(brightness=.5))[2]
        dark = self.pixels(Adjustments(brightness=-.5))[2]
        self.assertGreater(bright[0] - baseline[0], 75)
        self.assertGreater(baseline[0] - dark[0], 75)
        warm = self.pixels(Adjustments(temperature=1))[2]
        magenta = self.pixels(Adjustments(tint=1))[2]
        self.assertGreater(warm[0] - warm[2], 75)
        self.assertGreater(min(magenta[0], magenta[2]) - magenta[1], 65)

    def test_neutral_does_not_change_yuv_samples(self):
        raw = bytes(range(256)) * 3
        for color_range in ('tv', 'pc'):
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'yuv444p',
                '-video_size', '16x16', '-i', 'pipe:0', '-frames:v', '1',
                '-vf', filter_graph(Adjustments(), color_range=color_range),
                '-pix_fmt', 'yuv444p', '-f', 'rawvideo', 'pipe:1'],
                input=raw, capture_output=True, check=True)
            self.assertEqual(result.stdout, raw)
