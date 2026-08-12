"""Audio capture and speech recognition.

Recording happens on a PortAudio callback thread; transcription runs on
a single worker thread so the UI never blocks. The Parakeet model is
loaded once at startup (in the background) and kept in memory.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import queue
import threading

import numpy as np

from .config import MODEL_DIR

log = logging.getLogger("talkin.engine")

SAMPLE_RATE = 16000
MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
MAX_SECONDS = 300  # hard cap on one dictation, keeps memory bounded


def _resample(audio, orig_rate, target_rate):
    """Bandlimited resample via FFT (numpy only — no scipy).

    scipy's own vendored libgfortran/libquadmath collided with numpy's
    inside the AppImage bundle (mismatched hash-suffixed filenames
    linuxdeploy couldn't resolve), breaking the build outright. This
    is the same core technique scipy.signal.resample uses internally,
    just without dragging in a second copy of Fortran runtime libs for
    one function.
    """
    if len(audio) == 0:
        return audio
    n_target = int(round(len(audio) * target_rate / orig_rate))
    spectrum = np.fft.rfft(audio)
    n_freq_target = n_target // 2 + 1
    if n_freq_target <= len(spectrum):
        spectrum = spectrum[:n_freq_target]
    else:
        spectrum = np.pad(spectrum, (0, n_freq_target - len(spectrum)))
    resampled = np.fft.irfft(spectrum, n=n_target)
    resampled *= (n_target / len(audio))
    return resampled.astype(np.float32)


_DOWNLOADED_MARKER = os.path.join(MODEL_DIR, ".talkin-download-complete")


def _model_cached():
    if os.path.exists(_DOWNLOADED_MARKER):
        return True
    # No marker yet, but the cache may already be populated (e.g. an
    # install from before this check existed). A real downloaded file
    # in any model's blobs/ dir means the download actually finished,
    # as opposed to the empty ref/snapshot dirs the hub client creates
    # up front — so only that counts as cached, not a bare folder.
    hub_dir = os.path.join(MODEL_DIR, "hub")
    try:
        for entry in os.listdir(hub_dir):
            if not entry.startswith("models--"):
                continue
            blobs_dir = os.path.join(hub_dir, entry, "blobs")
            if any(os.scandir(blobs_dir)):
                _mark_cached()
                return True
    except OSError:
        pass
    return False


def _mark_cached():
    os.makedirs(MODEL_DIR, exist_ok=True)
    open(_DOWNLOADED_MARKER, "w", encoding="utf-8").close()


def _configure_hub(offline):
    """Point the Hugging Face client at our own cache folder.

    Talkin is offline by default. The one exception is the very first
    run, before the model has ever been downloaded — that single
    download is allowed, then this pins the process hard-offline for
    good, so no request is ever made again.
    """
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HOME"] = MODEL_DIR
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)


def list_microphones():
    """Input devices as (id, name) with the system default first."""
    import sounddevice as sd
    mics = [("default", None)]
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                mics.append((str(idx), dev["name"]))
    except Exception:
        log.exception("could not list input devices")
    return mics


class Recorder:
    """Push-to-talk microphone capture with live level reporting.

    Captured at whatever rate the device actually supports, not a
    hardcoded 16kHz: PortAudio's direct-ALSA path (used for any
    specific hardware device, as opposed to the "default"/"pipewire"
    aliases which resample internally) refuses a rate the hardware
    doesn't natively support — most interfaces only do 44.1/48kHz —
    so forcing 16kHz there failed outright with "Invalid sample rate"
    on every non-default device. Recorded audio is resampled to what
    the speech model needs once, in stop(), rather than fighting the
    hardware for an exact rate up front.
    """

    def __init__(self, config, on_level=None):
        self.config = config
        self.on_level = on_level  # called with 0..1 RMS from audio thread
        self._stream = None
        self._chunks = []
        self._native_rate = SAMPLE_RATE
        self._lock = threading.Lock()

    def _device(self):
        mic = self.config.get("mic")
        if mic == "default":
            return None
        try:
            return int(mic)
        except (TypeError, ValueError):
            return None

    def _device_rate(self, device):
        import sounddevice as sd
        try:
            info = (sd.query_devices(kind="input") if device is None
                    else sd.query_devices(device))
            return int(info["default_samplerate"])
        except Exception:
            log.warning("could not read device sample rate, using %dHz",
                       SAMPLE_RATE, exc_info=True)
            return SAMPLE_RATE

    def start(self):
        import sounddevice as sd
        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            device = self._device()
            self._native_rate = self._device_rate(device)
            cap_frames = self._native_rate * MAX_SECONDS

            def callback(indata, frames, time_info, status):
                with self._lock:
                    if len(self._chunks) * frames < cap_frames:
                        self._chunks.append(indata[:, 0].copy())
                if self.on_level is not None:
                    rms = float(np.sqrt(np.mean(indata ** 2)))
                    self.on_level(min(1.0, rms * 8))

            self._stream = sd.InputStream(
                samplerate=self._native_rate, channels=1, dtype="float32",
                device=device, callback=callback)
            self._stream.start()

    def stop(self):
        """Stop capture and return the recording as float32 mono 16 kHz."""
        with self._lock:
            stream, self._stream = self._stream, None
            chunks, self._chunks = self._chunks, []
            native_rate = self._native_rate
        if stream is not None:
            stream.stop()
            stream.close()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        if native_rate != SAMPLE_RATE:
            audio = _resample(audio, native_rate, SAMPLE_RATE)
        return audio

    @property
    def recording(self):
        with self._lock:
            return self._stream is not None


class Transcriber:
    """Owns the Parakeet model and a serial transcription queue."""

    def __init__(self, on_ready=None, on_error=None, on_downloading=None):
        self._model = None
        self._queue = queue.Queue()
        self.on_ready = on_ready
        self.on_error = on_error
        self.on_downloading = on_downloading
        threading.Thread(target=self._run, name="transcriber",
                         daemon=True).start()

    @property
    def ready(self):
        return self._model is not None

    def _load(self):
        cached = _model_cached()
        _configure_hub(offline=cached)
        if not cached:
            log.info("model not cached — this run will download it once")
            if self.on_downloading is not None:
                self.on_downloading()
        import onnx_asr
        log.info("loading %s", MODEL_NAME)
        self._model = onnx_asr.load_model(MODEL_NAME, quantization="int8")
        if not cached:
            _mark_cached()
            _configure_hub(offline=True)
        log.info("model ready")
        if self.on_ready is not None:
            self.on_ready()

    def _run(self):
        try:
            self._load()
        except Exception:
            log.exception("model failed to load")
            if self.on_error is not None:
                self.on_error("error.model")
            return
        while True:
            audio, callback = self._queue.get()
            try:
                text = self._recognize(audio)
                callback(text, None)
            except Exception:
                log.exception("transcription failed")
                callback(None, "error.generic")

    def _recognize(self, audio):
        if len(audio) < SAMPLE_RATE // 4:  # under 0.25s: nothing said
            return ""
        return self._model.recognize(audio, sample_rate=SAMPLE_RATE).strip()

    def submit(self, audio, callback):
        """Queue audio; callback(text, error_key) runs on worker thread."""
        self._queue.put((audio, callback))
