"""Check audio in an isolated process before allowing libmpv to open a device."""
import os
import shutil
import subprocess

BACKENDS = ('pipewire', 'pulse', 'alsa')


def select_audio_backend():
    """A broken sound server must not hold the in-process player core forever.

    Probe with 80 ms of silence in a disposable mpv process. Backend selection
    happens once on the player worker, never on the Qt thread. Failure affects
    preview audio only; FFmpeg exports continue to preserve the source audio.
    """
    requested = os.environ.get('VIDEOTINT_AUDIO', 'auto')
    if os.environ.get('VIDEOTINT_TEST_AUDIO') == 'null':
        return 'null', ''
    if requested == 'null':
        return 'null', 'Silent preview selected; saved videos retain their audio.'
    if requested not in {'auto', *BACKENDS}:
        raise ValueError('VIDEOTINT_AUDIO must be auto, pipewire, pulse, alsa, or null')
    executable = shutil.which('mpv')
    if not executable:
        return 'null', 'Install the mpv executable to enable preview audio; playing silently.'
    failures = []
    for backend in BACKENDS if requested == 'auto' else (requested,):
        command = [
            executable, '--no-config', '--terminal=no', '--vo=null',
            '--audio-display=no', f'--ao={backend}', '--audio-fallback-to-null=no',
            '--length=0.08', 'av://lavfi:anullsrc=r=48000:cl=stereo',
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=2)
            if result.returncode == 0:
                return backend, ''
            failures.append(backend + ' unavailable')
        except subprocess.TimeoutExpired:
            # subprocess.run kills and reaps the stalled process on timeout.
            failures.append(backend + ' timed out')
        except OSError:
            failures.append(backend + ' could not start')
    return 'null', ('Preview is playing silently: ' + ', '.join(failures) +
                    '. Saved videos retain audio. Check your desktop sound service.')
