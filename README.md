# Video Tint

A small native Linux video viewer with live color correction. Open a video,
watch it, adjust six sliders, and press **Apply** to save a corrected MP4.
No timeline, tracks, projects, browser, cloud service, or account is involved.

Built with Python, PySide6, embedded libmpv, and FFmpeg. Designed for SDR Sony
NEX-5 AVCHD `.MTS` footage and FFmpeg-remuxed MP4 files. Other common video
containers can also be opened.

## Arch Linux / CachyOS

Use the distribution's Python and Qt packages together:

```sh
sudo pacman -Syu --needed python pyside6 qt6-wayland mpv ffmpeg
cd "/path/to/Video adjuster"
./video-tint
# Or open a file directly:
./video-tint "/path/to/footage/My clip.MTS"
```

Python 3.10+, PySide6 6.6+, libmpv with the software render API, and FFmpeg 6+
with `libx264`, `eq`, `colorbalance`, `scale`, `bwdif`, and `idet` are required.
The tested environment has Python 3.14, Qt/PySide6 6.11, FFmpeg 9, and libmpv
client API 2.5. No `python-mpv` package is needed: a small ctypes binding is
included.

Qt normally selects Wayland automatically on Plasma. To select it explicitly:

```sh
QT_QPA_PLATFORM=wayland ./video-tint
```

If Qt cannot connect, run from a terminal inside your desktop session and check
that `qt6-wayland` is installed. `QT_QPA_PLATFORM=xcb ./video-tint` is an optional
XWayland fallback. The app does not embed an X11 window or require this fallback.

For a launcher in KDE's application menu:

```sh
./scripts/install-desktop.sh
```

This installs a desktop entry pointing at this checkout, including paths with
spaces. Keep the checkout in place. To uninstall the entry, remove
`~/.local/share/applications/video-tint.desktop` (or the corresponding
`$XDG_DATA_HOME` location).

Alternatively, install the Python package into a virtual environment:

```sh
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/video-tint
```

FFmpeg and libmpv still come from system packages. The checkout launcher
prefers `.venv/bin/python` when present. `requirements.txt` and `pyproject.toml`
declare the Python dependency.

## Using the viewer

- Open with **Open video**, **Ctrl+O**, a command-line path, or drag-and-drop.
- The folder is discovered automatically and filenames are naturally sorted
  (`clip2` before `clip10`). Previous/next navigation stops at folder boundaries.
- Move the pointer to the preview's right edge to reveal thumbnails and names.
  The panel stays open while interacting with it; **Pin folder panel** keeps it
  visible. Click a thumbnail to open that video.
- Use play/pause, the seek bar, time display, volume, and fullscreen controls.
- Brightness defaults to 0; contrast, gamma, and saturation to 1; tint and
  temperature to 0. Each slider has a reset, plus **Reset all**.
- Positive tint adds magenta relative to green; positive temperature warms the
  image by increasing red relative to blue. These adjust RGB midtones with
  lightness preservation, rather than rotating hue. They are subjective photo
  adjustments, not calibrated Kelvin or RAW white-balance controls.
- Hold **Hold to compare original**, or press **C** to toggle the original.
  Interlaced originals are still deinterlaced for a useful comparison.
- Adjustments are remembered per file during the session. Opening a saved
  result starts at neutral settings so it is not corrected twice.

| Shortcut | Action |
| --- | --- |
| Space | Play / pause |
| Left / Right | Seek backward / forward 5 seconds |
| Alt+Left / Alt+Right | Previous / next file |
| Ctrl+O | Open video |
| F / double-click preview | Toggle fullscreen |
| Esc | Leave fullscreen |
| C | Toggle original comparison |
| Ctrl+R | Reset all adjustments |
| Ctrl+Enter | Apply |

## Saving

**Apply** immediately starts a background encode with a snapshot of the current
sliders. Changing sliders or browsing during saving cannot alter that encode.
A progress bar and **Cancel save** remain available. One encode runs per window.

The default is `clip_corrected.mp4` beside the source. Existing results are kept;
subsequent saves use `_corrected_2`, `_corrected_3`, etc. Video is re-encoded using
CPU H.264 (`libx264`, CRF 16, medium preset), 8-bit YUV 4:2:0, with fast-start MP4.
There is no misleading stream-copy mode for corrected video.

Audio is copied for AAC, MP3, AC-3, E-AC-3, ALAC, Opus, FLAC, and DTS; other audio
is converted to AAC at 192 kbit/s. Each track is handled separately, including mixed-codec inputs; all audio streams
are retained. Playback of
less common MP4 audio codecs depends on the receiving player; use a recent
FFmpeg build. Sony AVCHD AC-3 audio is retained.

The optional **Replace MP4 (keep backup)** preference is remembered. Replacement
is allowed only for `.mp4` sources; other containers still produce a separate
MP4. The original MP4 is retained as `clip_backup.mp4` (or a unique numbered
backup) before replacement. Backups are never silently overwritten.

Every encode writes to a unique temporary file in the source folder. A zero
FFmpeg exit status, readable video stream, output size, and matching duration
are checked before publication. The source must still have its original size
and modification time. Separate outputs use atomic, no-clobber publication;
replacement uses an atomic rename after retaining the original backup. Destination
lock files prevent concurrent app instances from writing the same target.
Failure or cancellation removes the temporary output and leaves the source
intact. A hard crash may leave a hidden `.videotint-*` temporary file or
`*.videotint-lock`; remove those only after confirming no save is running.
The folder must allow file creation and hard links (normal local Linux
filesystems do). A read-only folder or unsupported filesystem produces an error.

On success, **Open saved result** opens the new video with neutral sliders.
Replacing the currently viewed source reloads the saved video at neutral.

## Processing consistency

Preview and saving call the same `processing_graph()` function:

1. Interlaced footage: BWDIF, `send_frame`, preserving the original frame rate.
2. FFmpeg `eq`: brightness, contrast, gamma, saturation.
3. Explicit YUV-to-RGB conversion using the source color matrix.
4. FFmpeg `colorbalance`: temperature and tint midtone channel adjustments.
5. RGB-to-YUV conversion using that same matrix and source range, then YUV 4:2:0.

Known color-matrix, range, primaries, and transfer tags are retained. Full-range
footage stays full-range. Untagged SDR uses a documented assumption: BT.709 above
576 lines, BT.601 otherwise; output gets that matrix tag. Unknown range is
assumed limited, as is usual for AVCHD. There is no forced BT.709 conversion of
correctly tagged SD footage. The neutral adjustment graph still traverses RGB;
**original comparison** bypasses correction and that RGB round trip.

Field order is inspected off the UI thread. Stream metadata determines whether
to deinterlace; when missing, FFmpeg `idet` examines up to 120 frames and chooses
field parity. Preview and output share the resulting decision. `send_frame`
produces 25p from 50i or 29.97p from 59.94i instead of doubling the frame rate.
This trades field-rate motion smoothness for preservation of frame timing.
Mixed progressive/interlaced footage or incorrect metadata can require a more
specialized workflow.

Resolution and sample aspect ratio are retained. FFmpeg autorotation bakes the
orientation into pixels and clears the rotation tag, so a 90-degree rotation
swaps stored width/height while retaining the displayed orientation. Frame
intervals are passed through; a container may normalize initial timestamps.
Global metadata, chapters, audio metadata, and normal stream properties are
preserved where FFmpeg/MP4 support them. AVCHD vendor metadata, subtitles,
attachments, extra video streams, and camera sidecars are not preserved.

## Limitations and implementation

- Software decoding, filtering, and rendering deliberately avoid X11 embedding
  and GPU driver/Wayland context issues. Rendering runs on a separate thread;
  libmpv control runs on another thread to avoid render-API deadlocks. The AMD
  Radeon RX 6700 XT is not used for acceleration. Large/4K files may be CPU-bound.
- HDR PQ/HLG files are rejected with a clear message. Output is SDR 8-bit H.264;
  high-bit-depth input loses precision. This is not a managed HDR grading tool.
- Preview/output use identical color processing, but H.264 is lossy and mpv's
  final display scaling/color conversion can differ slightly from FFmpeg's.
  Generated progressive and interlaced preview-versus-saved-frame smoke tests
  measured SSIM above 0.98;
  bit-identical display output is not claimed.
- Sliders update the paused or playing frame without reloading the file or
  seeking. Updates are debounced by 70 ms; filter rebuilding can briefly stall
  playback on a slow CPU.
- Thumbnail generation is limited to two workers; navigating folders discards
  pending work for the old folder. JPEGs are cached under
  `${XDG_CACHE_HOME:-~/.cache}/video-tint`, keyed by full path, size, and nanosecond
  modification time. The cache is reusable and can be deleted at any time.
  It has no automatic size cap.
- Closing during a save requests cancellation; close again after cancellation
  finishes. Closing may wait briefly for thumbnail/probe workers to finish.
- Preferences are stored through Qt's user settings; no footage, credentials,
  generated files, or local environment files belong in Git.

Modules: `core.py` handles probing, filter mapping, and transactional saving;
`player.py` owns libmpv rendering and control; `app.py` provides Qt UI, session
state, folder browsing, and background jobs.

## Validation

```sh
python -m unittest discover -s tests -v
XDG_CACHE_HOME=/tmp/video-tint-test-cache \
QT_QPA_PLATFORM=offscreen VIDEOTINT_TEST_AUDIO=null \
python scripts/smoke_test.py
# Repeat with generated interlaced MTS input:
XDG_CACHE_HOME=/tmp/video-tint-test-cache \
QT_QPA_PLATFORM=offscreen VIDEOTINT_TEST_AUDIO=null \
python scripts/smoke_test.py --interlaced
```

The tests generate their own files in temporary directories. They cover filter
mapping, natural sorting, paths with spaces/Unicode/shell characters, unique
names, output locking, encode failure, cancellation, backup replacement, AC-3
copying, PCM-to-AAC conversion, sample aspect ratio, rotation, full range,
BT.709 tags, real interlaced H.264 MTS and remuxed MP4, missing-field-metadata
`idet` detection, and variable frame intervals.

The GUI smoke test actually plays through libmpv, changes a paused frame without
moving position, saves through the UI, compares a preview screenshot with a
decoded saved frame using SSIM, checks asynchronous thumbnails/natural folder
ordering, reopens the saved result, and checks per-file settings and export
snapshot isolation. `VIDEOTINT_TEST_AUDIO=null` is a test-only silent audio
backend; remove it for ordinary playback.

The build session could run offscreen Qt but could not connect even a minimal
Qt application to its advertised Wayland display, and could not access the
desktop audio server. Visible Plasma/Wayland behavior, audible playback, actual
Sony NEX-5 footage, and RX 6700 XT desktop performance therefore still need a
local check. No user footage was available to the build session.

## License and dependencies

Video Tint's application source is [MIT licensed](LICENSE). Dependency binaries
are not bundled and retain their own licenses:

- [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html):
  LGPLv3/GPLv3 or commercial terms; individual Qt components can differ.
- [mpv / libmpv](https://github.com/mpv-player/mpv/blob/master/Copyright):
  GPLv2+ by default, with LGPL builds possible under their build conditions.
- [FFmpeg](https://ffmpeg.org/legal.html): LGPLv2.1+ by default; enabled components
  can make a build GPL and/or version 3. Arch's build enables GPL components.
- [x264](https://code.videolan.org/videolan/x264/-/blob/master/COPYING): GPLv2+.
- [Python](https://docs.python.org/3/license.html): PSF license and included
  third-party notices.

MIT applies to this project's code, not to these dependencies. Redistribution
of binaries must follow the licenses of the actual dependency builds, including
GPL requirements where applicable.

Reference behavior is documented in the [mpv render API](https://github.com/mpv-player/mpv/blob/master/include/mpv/render.h)
and [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html).
See [PUBLISHING.md](PUBLISHING.md) for the remaining GitHub publication commands.
