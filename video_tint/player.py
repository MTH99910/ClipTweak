"""Small ctypes libmpv binding. All decode/render work lives on a worker thread."""
import ctypes as C
import ctypes.util
import locale
import queue
import threading
import time
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from .audio import select_audio_backend

# Values from the public libmpv client/render headers.
API_TYPE, SW_SIZE, SW_FORMAT, SW_STRIDE, SW_POINTER = 1, 17, 18, 19, 20
FORMAT_FLAG, FORMAT_DOUBLE = 3, 5
EVENT_NONE, EVENT_LOG_MESSAGE, EVENT_END_FILE = 0, 2, 7


class Param(C.Structure):
    _fields_ = [('type', C.c_int), ('data', C.c_void_p)]

class Event(C.Structure):
    _fields_ = [('event_id', C.c_int), ('error', C.c_int), ('reply_userdata', C.c_uint64), ('data', C.c_void_p)]

class EndFile(C.Structure):
    _fields_ = [('reason', C.c_int), ('error', C.c_int)]

class Log(C.Structure):
    _fields_ = [('prefix', C.c_char_p), ('level', C.c_char_p), ('text', C.c_char_p)]

class Player(QThread):
    frame = Signal(QImage)
    status = Signal(float, float, bool)
    error = Signal(str)
    notice = Signal(str)

    def __init__(self):
        super().__init__()
        self.commands = queue.Queue()
        self.running = True
        self.size = (960, 540)
        # QApplication/platform plugins can set the C runtime's numeric locale.
        # libmpv rejects regional locales even when their decimal separator is '.'.
        # Do this on the creating (GUI) thread, before starting any player work,
        # and retain it for mpv's lifetime. LC_CTYPE/Unicode and other categories
        # are deliberately untouched, as is Qt's separate QLocale formatting.
        previous_numeric_locale = locale.setlocale(locale.LC_NUMERIC)
        numeric_locale = locale.setlocale(locale.LC_NUMERIC, 'C')
        self.diagnostics = {
            'stage': 'Starting', 'rendered_frames': 0,
            'numeric_locale_before': previous_numeric_locale,
            'numeric_locale': numeric_locale,
        }

    def command(self, *args):
        self.commands.put(tuple((str(x) for x in args)))

    def run(self):
        ctx = handle = control = None
        try:
            library = ctypes.util.find_library('mpv') or 'libmpv.so.2'
            self.diagnostics['library'] = library
            lib = C.CDLL(library)

            def bind(name, restype, *args):
                f = getattr(lib, name)
                f.restype = restype
                f.argtypes = list(args)
                return f
            create = bind('mpv_create', C.c_void_p)
            option = bind('mpv_set_option_string', C.c_int, C.c_void_p, C.c_char_p, C.c_char_p)
            init = bind('mpv_initialize', C.c_int, C.c_void_p)
            command = bind('mpv_command', C.c_int, C.c_void_p, C.POINTER(C.c_char_p))
            get = bind('mpv_get_property', C.c_int, C.c_void_p, C.c_char_p, C.c_int, C.c_void_p)
            render_create = bind('mpv_render_context_create', C.c_int, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(Param))
            render = bind('mpv_render_context_render', C.c_int, C.c_void_p, C.POINTER(Param))
            update = bind('mpv_render_context_update', C.c_uint64, C.c_void_p)
            free = bind('mpv_render_context_free', None, C.c_void_p)
            destroy = bind('mpv_terminate_destroy', None, C.c_void_p)
            wait_event = bind('mpv_wait_event', C.POINTER(Event), C.c_void_p, C.c_double)
            request_logs = bind('mpv_request_log_messages', C.c_int, C.c_void_p, C.c_char_p)
            error_string = bind('mpv_error_string', C.c_char_p, C.c_int)
            update_callback_type = C.CFUNCTYPE(None, C.c_void_p)
            set_update_callback = bind(
                'mpv_render_context_set_update_callback', None,
                C.c_void_p, update_callback_type, C.c_void_p,
            )
            self.diagnostics['numeric_locale_at_create'] = locale.setlocale(locale.LC_NUMERIC)
            handle = create()
            if not handle:
                raise RuntimeError('Could not create a libmpv player ' +
                                   '(numeric locale: ' + locale.setlocale(locale.LC_NUMERIC) + ')')
            for k, v in {'vo': 'libmpv', 'hwdec': 'no', 'config': 'no', 'idle': 'yes', 'keep-open': 'yes', 'audio-display': 'no', 'deinterlace': 'no', 'input-default-bindings': 'no'}.items():
                if option(handle, k.encode(), v.encode()) < 0:
                    raise RuntimeError('Unsupported mpv option: ' + k)
            self.diagnostics['stage'] = 'Checking audio'
            backend, audio_notice = select_audio_backend()
            self.diagnostics['audio_backend'] = backend
            if option(handle, b'ao', backend.encode()) < 0:
                option(handle, b'ao', b'null')
                audio_notice = 'Audio backend unsupported; preview is playing silently.'
            if audio_notice:
                self.notice.emit(audio_notice)
            # A later device failure should disable sound rather than video.
            option(handle, b'audio-fallback-to-null', b'yes')
            if init(handle) < 0:
                raise RuntimeError('Could not initialize libmpv')
            request_logs(handle, b'error')
            ctx = C.c_void_p()
            if render_create(C.byref(ctx), handle, (Param * 2)(Param(API_TYPE, C.cast(C.c_char_p(b'sw'), C.c_void_p)), Param(0, None))) < 0:
                raise RuntimeError('libmpv software renderer unavailable')

            self.diagnostics['stage'] = 'Renderer ready'
            render_pending = threading.Event()
            # Retain the ctypes callback until the render context is freed.
            # Callbacks only wake our renderer; calling mpv here can deadlock.
            @update_callback_type
            def on_render_update(_):
                render_pending.set()
            set_update_callback(ctx, on_render_update, None)

            def control_loop():
                """Never block the render thread on normal libmpv commands or properties."""
                last_status = 0
                while self.running:
                    while not self.commands.empty() and self.running:
                        args = self.commands.get()
                        arr = (C.c_char_p * (len(args) + 1))(*(x.encode() for x in args), None)
                        self.diagnostics['last_command'] = ' '.join(args)
                        rc = command(handle, arr)
                        self.diagnostics['last_command_result'] = rc
                        if rc < 0:
                            self.error.emit(f'{args[0]}: {error_string(rc).decode()}')
                    while self.running:
                        event = wait_event(handle, 0).contents
                        if event.event_id == EVENT_NONE:
                            break
                        if event.event_id == 8:
                            self.diagnostics['stage'] = 'File loaded'
                        if event.event_id == EVENT_END_FILE and event.data:
                            end = C.cast(event.data, C.POINTER(EndFile)).contents
                            if end.error < 0:
                                self.error.emit('Playback failed: ' + error_string(end.error).decode())
                        elif event.event_id == EVENT_LOG_MESSAGE and event.data:
                            log = C.cast(event.data, C.POINTER(Log)).contents
                            self.error.emit(f"{log.prefix.decode(errors='replace')}: {log.text.decode(errors='replace').strip()}")
                    if time.monotonic() - last_status > 0.2:
                        pos = C.c_double()
                        duration = C.c_double()
                        pause = C.c_int()
                        get(handle, b'time-pos', FORMAT_DOUBLE, C.byref(pos))
                        get(handle, b'duration', FORMAT_DOUBLE, C.byref(duration))
                        get(handle, b'pause', FORMAT_FLAG, C.byref(pause))
                        self.diagnostics.update(position=pos.value, duration=duration.value, paused=bool(pause.value))
                        self.status.emit(pos.value, duration.value, bool(pause.value))
                        last_status = time.monotonic()
                    time.sleep(0.02)
            # Rendering must keep servicing requests while control calls block.
            control = threading.Thread(target=control_loop, name='mpv-control')
            control.start()
            last_size = None
            while self.running:
                size = self.size
                changed = size != last_size
                if changed:
                    w, h = size
                    stride = (w * 4 + 63) // 64 * 64
                    raw = C.create_string_buffer(stride * h + 64)
                    ptr = (C.addressof(raw) + 63) & ~63
                    dims = (C.c_int * 2)(w, h)
                    pitch = C.c_size_t(stride)
                    params = (Param * 5)(Param(SW_SIZE, C.cast(dims, C.c_void_p)), Param(SW_FORMAT, C.cast(C.c_char_p(b'bgr0'), C.c_void_p)), Param(SW_STRIDE, C.cast(C.pointer(pitch), C.c_void_p)), Param(SW_POINTER, ptr), Param(0, None))
                    last_size = size
                render_pending.clear()
                # Poll as a fallback too: an event can arrive just before clear().
                if update(ctx) & 1 or changed:
                    if render(ctx, params) < 0:
                        raise RuntimeError('Video rendering failed')
                    pixels = bytearray(C.string_at(ptr, stride * h))
                    # mpv's bgr0 padding byte is undefined; Qt RGB32 requires
                    # opaque 0xff. Never upload zero/garbage alpha to a compositor.
                    pixels[3::4] = b'\xff' * (len(pixels) // 4)
                    img = QImage(pixels, w, h, stride, QImage.Format.Format_RGB32).copy()
                    self.diagnostics['rendered_frames'] += 1
                    self.frame.emit(img)
                render_pending.wait(.015)
        except Exception as e:
            self.diagnostics.update(stage='Player failed', error=str(e))
            self.error.emit(str(e))
        finally:
            self.running = False
            if ctx:
                free(ctx)
            if control:
                control.join()
            if handle:
                destroy(handle)

    def stop(self):
        self.running = False
        self.wait()
