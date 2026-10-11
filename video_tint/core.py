"""Shared color processing and transactional FFmpeg export; no Qt dependencies."""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

EXTENSIONS = {'.mts', '.m2ts', '.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}
MP4_AUDIO_CODECS = {'aac', 'mp3', 'ac3', 'eac3', 'alac', 'opus', 'flac', 'dts'}
COLOR_MATRICES = {
    'bt709': 'bt709', 'bt470bg': 'bt601', 'smpte170m': 'bt601',
    'fcc': 'fcc', 'smpte240m': 'smpte240m', 'bt2020nc': 'bt2020',
}


@dataclass
class Adjustments:
    brightness: float = 0
    contrast: float = 1
    gamma: float = 1
    saturation: float = 1
    tint: float = 0
    temperature: float = 0

    def __post_init__(self):
        limits = {
            'brightness': (-.5, .5), 'contrast': (.5, 2), 'gamma': (.4, 2.5),
            'saturation': (0, 2), 'tint': (-1, 1), 'temperature': (-1, 1),
        }
        for key, (low, high) in limits.items():
            value = getattr(self, key)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')


def natural_key(path):
    return [int(x) if x.isdigit() else x.casefold()
            for x in re.split(r'(\d+)', path.name)]


def probe(path):
    """Inspect SDR video and sample field order if stream metadata is missing."""
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format',
         '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Cannot read this video')
    info = json.loads(result.stdout)
    videos = [s for s in info['streams'] if s['codec_type'] == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise RuntimeError('This file has no playable video stream')
    video = videos[0]
    if video.get('color_space') not in {None, 'unknown', *COLOR_MATRICES}:
        raise RuntimeError('Unsupported color matrix: ' + video['color_space'])
    if video.get('color_transfer') in {'smpte2084', 'arib-std-b67'}:
        raise RuntimeError('HDR footage is not supported; convert to SDR first')
    info['video'] = video
    field_order = video.get('field_order', 'unknown')
    info['interlaced'] = field_order not in {'progressive', 'unknown'}
    info['field_detection'] = 'stream metadata'
    if field_order == 'unknown':
        # Remuxing can lose AVCHD field metadata. Inspect actual decoded frames.
        scan = subprocess.run(
            ['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path),
             '-map', f"0:{video['index']}", '-an', '-vf', 'idet',
             '-frames:v', '120', '-f', 'null', '-'],
            capture_output=True, text=True, timeout=30,
        )
        counts = re.findall(
            r'Multi frame detection: TFF:\s*(\d+) BFF:\s*(\d+) Progressive:\s*(\d+)',
            scan.stderr,
        )
        if scan.returncode or not counts:
            raise RuntimeError('Cannot determine field order for this file')
        tff, bff, progressive = map(int, counts[-1])
        info['interlaced'] = tff + bff >= 12 and tff + bff > 2 * progressive
        info['field_detection'] = '120-frame idet sample'
        if info['interlaced']:
            info['detected_parity'] = 'tff' if tff >= bff else 'bff'
    return info


def color_matrix(video):
    """Preserve known matrices; explicitly infer HD/SD for untagged SDR."""
    return COLOR_MATRICES.get(
        video.get('color_space'), 'bt709' if video.get('height', 0) > 576 else 'bt601',
    )


def filter_graph(a, interlaced=False, color_range='tv', parity='auto', matrix='bt601'):
    """Photo-style controls with explicit, symmetric YUV/RGB conversions."""
    temperature, tint = a.temperature * .12, a.tint * .10
    balance = {'rm': temperature + tint / 2, 'gm': -tint, 'bm': -temperature + tint / 2}
    filters = []
    if interlaced:
        filters.append(f'bwdif=mode=send_frame:parity={parity}:deint=all')
    filters.extend([
        f'eq=brightness={a.brightness:.6f}:contrast={a.contrast:.6f}:'
        f'gamma={a.gamma:.6f}:saturation={a.saturation:.6f}',
        f'scale=in_color_matrix={matrix}:out_range=full',
        'format=gbrp',
        'colorbalance=' + ':'.join(f'{k}={v + 0.0:.6f}' for k, v in balance.items()) + ':pl=1',
        f'scale=out_color_matrix={matrix}:out_range={"full" if color_range == "pc" else "limited"}',
        'format=yuv420p',
    ])
    return ','.join(filters)


def processing_graph(settings, info):
    """The single source of truth for preview and output filter parameters."""
    return filter_graph(
        settings, info['interlaced'], info['video'].get('color_range', 'tv'),
        info.get('detected_parity', 'auto'), color_matrix(info['video']),
    )


def unique_path(source, suffix='_corrected', extension='.mp4'):
    candidate = source.with_name(source.stem + suffix + extension)
    number = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = source.with_name(f'{source.stem}{suffix}_{number}{extension}')
        number += 1
    return candidate


def export_command(source, target, settings, info):
    """Build an argument array; neither filenames nor settings enter a shell."""
    audio = [s for s in info['streams'] if s['codec_type'] == 'audio']
    command = [
        'ffmpeg', '-hide_banner', '-nostdin', '-y', '-i', str(source),
        '-map', f"0:{info['video']['index']}", '-map', '0:a?',
        '-map_metadata', '0', '-map_chapters', '0',
        '-vf', processing_graph(settings, info),
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '16',
        '-fps_mode', 'passthrough',
    ]
    for index, stream in enumerate(audio):
        copy_audio = stream['codec_name'] in MP4_AUDIO_CODECS
        command += [f'-c:a:{index}', 'copy' if copy_audio else 'aac']
        if not copy_audio:
            command += [f'-b:a:{index}', '192k']
    tags = [
        ('color_range', '-color_range'), ('color_space', '-colorspace'),
        ('color_transfer', '-color_trc'), ('color_primaries', '-color_primaries'),
    ]
    for key, option in tags:
        value = info['video'].get(key)
        if value and value != 'unknown':
            command += [option, value]
    if info['video'].get('color_space', 'unknown') == 'unknown':
        matrix = color_matrix(info['video'])
        command += ['-colorspace', 'bt709' if matrix == 'bt709' else 'smpte170m']
    # Default FFmpeg autorotation bakes orientation into pixels and clears its tag.
    command += ['-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(target)]
    return command


class Exporter:
    """One immutable settings snapshot, cancellable encode, then safe publication."""
    def __init__(self, source, settings, info, replace=False):
        self.source = Path(source)
        self.settings = Adjustments(**asdict(settings))
        self.info = info
        self.replace = replace and self.source.suffix.lower() == '.mp4'
        self.cancelled = threading.Event()

    def encode(self, target, progress):
        command = export_command(self.source, target, self.settings, self.info)
        duration = float(self.info['format'].get('duration', 0))
        with tempfile.TemporaryFile(mode='w+') as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=errors, text=True,
            )
            def cancel_watch():
                while process.poll() is None:
                    if self.cancelled.wait(.1):
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        return
            watcher = threading.Thread(target=cancel_watch, daemon=True)
            watcher.start()
            try:
                for line in process.stdout:
                    if line.startswith('out_time_us=') and duration:
                        try:
                            value = int(line.split('=')[1]) / 10000 / duration
                            progress(max(0, min(99, int(value))))
                        except ValueError:
                            pass
                code = process.wait()
            finally:
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                watcher.join()
            self.check_cancelled()
            if code:
                errors.seek(0)
                raise RuntimeError(errors.read()[-2500:])

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise RuntimeError('Save cancelled; source unchanged')

    def run(self, progress=lambda value: None):
        self.check_cancelled()
        source_stat = self.source.stat()
        target = self.source if self.replace else unique_path(self.source)
        # Exclusive destination reservation also works across app instances.
        lock = target.with_name(target.name + '.videotint-lock')
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        temporary = None
        try:
            if not self.replace and (target.exists() or target.is_symlink()):
                raise RuntimeError('Destination appeared; retry Apply')
            descriptor, name = tempfile.mkstemp(
                prefix='.videotint-', suffix='.mp4', dir=self.source.parent,
            )
            os.close(descriptor)
            temporary = Path(name)
            self.encode(temporary, progress)
            result = probe(temporary)
            if temporary.stat().st_size < 100 or not result.get('video'):
                raise RuntimeError('Output validation failed')
            expected_duration = float(self.info['format'].get('duration', 0))
            actual_duration = float(result['format'].get('duration', 0))
            if expected_duration and abs(actual_duration - expected_duration) > max(1, expected_duration * .01):
                raise RuntimeError('Output duration does not match the source')
            current_stat = self.source.stat()
            if (current_stat.st_size, current_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
                raise RuntimeError('Source changed while saving; output was not published')
            self.check_cancelled()
            if self.replace:
                backup = unique_path(self.source, '_backup', self.source.suffix)
                os.link(self.source, backup)
                os.replace(temporary, self.source)
            else:
                # Hard-link publication cannot overwrite a destination that appeared.
                os.link(temporary, target)
                temporary.unlink()
            progress(100)
            return target
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
