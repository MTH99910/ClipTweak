# ClipTweak

A small native Linux video viewer with live color correction. Open a video,
watch it, adjust six sliders, stage changes across several videos, and press
**Apply** to save every staged correction.
No timeline, tracks, projects, browser, cloud service, or account is involved.

Built with Python, PySide6, embedded libmpv, and FFmpeg. Designed for SDR Sony
NEX-5 AVCHD `.MTS` footage and FFmpeg-remuxed MP4 files. Other common video
containers can also be opened.

## Arch Linux / CachyOS

Use the distribution's Python and Qt packages together:

```sh
sudo pacman -Syu --needed python pyside6 qt6-wayland mpv ffmpeg
cd "/path/to/ClipTweak"
./cliptweak-launcher
# Or open a file directly:
./cliptweak-launcher "/path/to/footage/My clip.MTS"
```

Python 3.10+, PySide6 6.6+, libmpv with the software render API, and FFmpeg 6+
with `libx264`, `lutrgb`, `colorchannelmixer`, `scale`, `setparams`, `bwdif`, and `idet` are required.
The tested environment has Python 3.14, Qt/PySide6 6.11, FFmpeg 9, and libmpv
client API 2.5. No `python-mpv` package is needed: a small ctypes binding is
included.

Qt normally selects Wayland automatically on Plasma. To select it explicitly:

```sh
QT_QPA_PLATFORM=wayland ./cliptweak-launcher
```

If Qt cannot connect, run from a terminal inside your desktop session and check
that `qt6-wayland` is installed. `QT_QPA_PLATFORM=xcb ./cliptweak-launcher` is an optional
XWayland fallback. The app does not embed an X11 window or require this fallback.

For a launcher in KDE's application menu:

```sh
./scripts/install-desktop.sh
```

This installs a desktop entry pointing at this checkout and copies the bundled
ClipTweak PNG to the user icon directory. Both KDE and the application window
use this icon, including when paths contain spaces. Keep the checkout in place. To uninstall the entry, remove
`~/.local/share/applications/cliptweak.desktop` (or the corresponding
`$XDG_DATA_HOME` location).

Qt's desktop/platform initialization can select a regional C-runtime numeric
locale. libmpv refuses to create a player unless `LC_NUMERIC` is `C`; ClipTweak
sets that category inside its process after Qt initializes and before starting
its player. Other locale categories and Unicode path handling are preserved.
This follows [mpv's Qt initialization example](https://github.com/mpv-player/mpv-examples/blob/master/libmpv/qt/qtexample.cpp).

For a blank preview, click **Diagnostics**. The report includes the actual
application path/version, Qt platform, player state, received frames, paint events,
and sampled frame colors. **Copy report** copies it to the clipboard. **Test
preview display** shows colored bars in the video area independently of mpv;
open Diagnostics again and choose **Restore video** to return to playback.
This distinguishes decoding problems from display/compositor problems and also
checks whether a launcher is running a different installed copy.

If the preview is blank or playback stays at 00:00, launch from a terminal to
see the error at the bottom of the window. Audio startup is checked in a separate
process with a timeout, so an inaccessible or stalled sound server cannot block
video decoding. The player checks PipeWire, PulseAudio, then ALSA. If none works,
video plays silently and the window reports why; saving still retains audio.
You can select a backend explicitly:

```sh
CLIPTWEAK_AUDIO=pipewire ./cliptweak-launcher
# Diagnostic: bypass audio entirely, while retaining audio in saved output.
CLIPTWEAK_AUDIO=null ./cliptweak-launcher
```

Alternatively, install the Python package into a virtual environment:

```sh
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/cliptweak
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
- Previous, play/pause, and next use icons beside the seek bar. Click anywhere
  on the seek bar to jump directly; dragging its handle also works.
- Hover an adjustment slider and turn the wheel for one integer step per
  detent (0.01 in the displayed value). Seeking and volume use standard controls.
- Use the time display, volume, and fullscreen controls.
- Brightness defaults to 0; contrast, gamma, and saturation to 1; tint and
  temperature to 0. Each slider has a reset, plus **Reset all**.
- Positive tint adds magenta relative to green; positive temperature warms the
  image by increasing red relative to blue. These apply logarithmic RGB channel balance with
  smooth highlight rolloff and preserved black. Brightness spans an
  exposure-like gain of ±3 stops; brightness, tint, and temperature have three
  times the adjustment strength of version 0.1.3. They are subjective photo
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

**Apply (N)** starts a background batch of every video with unsaved, non-neutral
adjustments in this session, including videos outside the currently visible
folder. The folder panel marks these files **staged**. Simply opening a file or
resetting its sliders to neutral does not queue a save. After a successful
separate save, those exact settings are marked saved; editing them stages the
source again. Settings/staging are session-only, so apply before quitting.

The entire batch uses immutable snapshots taken when Apply is clicked and
encodes one file at a time. Progress names the current file and shows the overall
batch percentage. Changing sliders or browsing cannot change those snapshots.
**Cancel save** stops the active encode and skips remaining files; files already
completed stay saved. Failed and cancelled files remain staged for retry, and a
failed file does not prevent other files in the batch from saving.

With in-place replacement, adjustment controls for queued MP4 sources are
locked until the batch ends, to avoid applying changes against obsolete source
pixels. Browsing/playback remain available. Separate saves permit further
adjustments while the batch runs; those later changes stay staged.

The default is `clip_corrected.mp4` beside the source. Existing results are kept;
subsequent saves use `_corrected_2`, `_corrected_3`, etc. Video is re-encoded using
CPU H.264 (`libx264`, CRF 16, medium preset), 8-bit YUV 4:2:0, with fast-start MP4.
There is no misleading stream-copy mode for corrected video.

Audio is copied for AAC, MP3, AC-3, E-AC-3, ALAC, Opus, FLAC, and DTS; other audio
is converted to AAC at 192 kbit/s. Each track is handled separately, including mixed-codec inputs; all audio streams
are retained. Playback of
less common MP4 audio codecs depends on the receiving player; use a recent
FFmpeg build. Sony AVCHD AC-3 audio is retained.

The optional **Replace MP4 (no backup)** preference is remembered. Replacement
is allowed only for `.mp4` sources; other containers still produce a separate
MP4. After successful encoding and validation, the original MP4 is replaced
atomically. No backup file is created; the original content is no longer retained.

Every encode writes to a unique temporary file in the source folder. A zero
FFmpeg exit status, readable video stream, output size, and matching duration
are checked before publication. The source must still have its original size
and modification time. Separate outputs use atomic, no-clobber publication;
replacement uses an atomic rename without a backup. Destination
lock files prevent concurrent app instances from writing the same target.
Failure or cancellation removes the temporary output and leaves the source
intact. A hard crash may leave a hidden `.videotint-*` temporary file or
`*.videotint-lock`; remove those only after confirming no save is running.
The folder must allow file creation and hard links (normal local Linux
filesystems do). A read-only folder or unsupported filesystem produces an error.

On success, **Open saved result** opens the last saved video with neutral sliders.
Replacing the currently viewed source reloads the saved video at neutral.

## Processing consistency

Preview and saving call the same `processing_graph()` function:

1. Interlaced footage: BWDIF, `send_frame`, preserving the original frame rate.
   Its preview filter stays in place while the separate color filter changes.
2. Neutral settings bypass color processing completely; progressive decoded
   YUV pixels remain unchanged, including their chroma and range.
3. For adjustments, explicitly convert source YUV to full-range 16-bit RGB.
4. Apply RGB contrast/gamma curves and gentle exposure-like brightness, with
   logarithmic temperature/tint gains and smooth highlight rolloff via `lutrgb`.
   Warm increases red relative to blue; magenta decreases green relative to
   red/blue. These balance channels across the image, preserving black, rather
   than adding colored midtone offsets or rotating hue.
5. Apply luminance-preserving RGB saturation with `colorchannelmixer`.
6. Convert once to 8-bit YUV 4:2:0 using the source matrix/range. Both conversion
   directions specify their scaling flags; output frames explicitly declare
   the resulting range and matrix so mpv cannot infer a conflicting one.

Known color-matrix, range, primaries, and transfer tags are retained. Full-range
footage stays full-range. Untagged SDR uses BT.709 above 576 lines and BT.601
otherwise; output gets that matrix tag. Unknown range is assumed limited, as is
usual for AVCHD. Original comparison retains the same deinterlacing decision.

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
  libmpv control runs on another thread to avoid render-API deadlocks.
  Render-update callbacks wake the renderer, and frames have explicit opaque
  alpha before they are passed to Qt/compositors. The AMD
  Radeon RX 6700 XT is not used for acceleration. Large/4K files may be CPU-bound.
- HDR PQ/HLG files are rejected with a clear message. Output is SDR 8-bit H.264;
  high-bit-depth input loses precision. This is not a managed HDR grading tool.
- Preview/output use identical color processing, but H.264 is lossy and mpv's
  final display scaling/color conversion can differ slightly from FFmpeg's.
  Generated progressive/interlaced smoke tests compare both videos through
  mpv: unencoded filter references require SSIM >0.98; lossy H.264 output
  requires SSIM >0.93 and PSNR >38 dB (flat saturated bars amplify SSIM errors);
  bit-identical display output is not claimed.
- Sliders update the paused or playing frame without reloading the file or
  seeking. Updates are debounced by 70 ms; filter rebuilding can briefly stall
  playback on a slow CPU.
- Thumbnail generation is limited to two workers; navigating folders discards
  pending work for the old folder. JPEGs are cached under
  `${XDG_CACHE_HOME:-~/.cache}/cliptweak`, keyed by full path, size, and nanosecond
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
XDG_CACHE_HOME=/tmp/cliptweak-test-cache \
QT_QPA_PLATFORM=offscreen CLIPTWEAK_TEST_AUDIO=null \
python scripts/smoke_test.py
# Repeat with generated interlaced MTS input:
XDG_CACHE_HOME=/tmp/cliptweak-test-cache \
QT_QPA_PLATFORM=offscreen CLIPTWEAK_TEST_AUDIO=null \
python scripts/smoke_test.py --interlaced
```

The tests generate their own files in temporary directories. Audio regressions
cover working backends, stalled servers, and a visible silent fallback. A real
libmpv regression reproduces null-player creation under `en_GB.UTF-8` and checks
that the numeric-locale reset allows successful creation without changing other
locale categories. Run the
GUI smoke test without `CLIPTWEAK_TEST_AUDIO` to exercise normal audio startup.
The generated samples still save their audio even when preview audio is unavailable. They cover filter
mapping, natural sorting, paths with spaces/Unicode/shell characters, unique
names, output locking, encode failure, cancellation, replacement without backups, multi-file snapshots/partial failure, AC-3
copying, PCM-to-AAC conversion, sample aspect ratio, rotation, full range,
BT.709 tags, real interlaced H.264 MTS and remuxed MP4, missing-field-metadata
`idet` detection, and variable frame intervals.

The GUI smoke test checks the actual painted Qt preview surface and plays through libmpv, changes a paused frame without
moving position, saves through the UI, compares a preview screenshot with a
decoded saved frame through the same mpv RGB converter using SSIM and PSNR, checks asynchronous thumbnails/natural folder
ordering, reopens the saved result, and checks per-file settings and export
snapshot isolation. Qt event tests also cover click-to-seek, one-step wheel
adjustments, accessible transport icons, staged-file counting, and resetting
replaced sources. `CLIPTWEAK_TEST_AUDIO=null` is a test-only silent audio
backend; remove it for ordinary playback.

The build session could run offscreen Qt but could not connect even a minimal
Qt application to its advertised Wayland display, and could not access the
desktop audio server. Visible Plasma/Wayland behavior, audible playback, and RX 6700 XT desktop
performance therefore still need a local check. A subsequent regression check
played actual Sony NEX-5 MTS footage from the local Videos folder read-only, with
the production stylesheet, GUI-thread frame delivery, and the diagnostic display
test. A further check played `00023.mp4` while repeatedly changing temperature and
tint (241 rendered frames in eight seconds, no playback errors). Pixel tests
verify neutral YUV identity, both balance directions, preserved black/highlights,
and gentle, near-monotonic gradients. The original footage was not modified.

The batch GUI smoke tests stage two generated videos through folder navigation,
click Apply, check both outputs and an untouched neutral file, then play a saved
result. They also cover separate-output snapshot isolation and replacement
without extra files:

```sh
QT_QPA_PLATFORM=offscreen CLIPTWEAK_AUDIO=null \
XDG_CACHE_HOME=/tmp/cliptweak-test-cache python scripts/batch_smoke_test.py
QT_QPA_PLATFORM=offscreen CLIPTWEAK_AUDIO=null \
XDG_CACHE_HOME=/tmp/cliptweak-test-cache python scripts/batch_smoke_test.py --replace
```

Run `./scripts/install-desktop.sh` after moving or renaming the checkout so the
menu entry points at its new location. The obsolete Video Tint launcher has
been removed; use `./cliptweak-launcher` or the installed `cliptweak` command.

## License and dependencies

ClipTweak's application source is [MIT licensed](LICENSE). Dependency binaries
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

For the full GUI smoke test under the previously failing desktop numeric locale
(on a system with `en_GB.UTF-8` installed):

```sh
QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/tmp/cliptweak-test-cache \
python scripts/smoke_test.py --desktop-locale
```
