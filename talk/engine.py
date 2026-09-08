"""Audio capture and speech recognition.

Recording happens on a PortAudio callback thread; transcription runs on
a single worker thread so the UI never blocks. The Whisper model is
loaded once at startup (in the background) and kept in memory.
"""

# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import queue
import sys
import threading
import time

import numpy as np

from .config import MODEL_DIR

log = logging.getLogger("talk.engine")

SAMPLE_RATE = 16000
# Whisper, not Parakeet. Parakeet's multilingual build drops whole
# sentences out of the middle of a long dictation: measured on one
# 175-second recording it kept 28 of 30, where every English-only
# Parakeet variant and every Whisper size kept all 30. Splitting the
# recording made it worse (21-25 of 30 - each cut damages a sentence at
# the join), and full precision was no better (26 of 30). Whisper small
# keeps everything, costs 21 seconds against 14 on that recording,
# downloads 464 MB rather than 600, and hears far more languages.
MODEL_NAME = "small"
# What its cache folder is called, for telling a finished download from
# a half-finished one, and from the model this app used to use.
MODEL_DIR_NAME = "models--Systran--faster-whisper-small"
MAX_SECONDS = 1200  # hard cap on one dictation, keeps memory bounded
WARMUP_SECONDS = 0.12  # discarded from the start of every recording


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
    # An FFT treats the buffer as periodic - it implicitly assumes the
    # last sample wraps seamlessly back to the first. Live mic audio
    # essentially never starts or ends at exactly zero, so that seam is
    # a real discontinuity, which leaks into a broadband click right at
    # the edges of the resampled output. Since this runs on almost
    # every real audio device (most only do 44.1/48kHz natively), that
    # click landed at the start of nearly every dictation, and got
    # heard by the model as a stray consonant. A short taper on each
    # edge removes the seam before it ever reaches the FFT - the first/
    # last ~5ms of a push-to-talk recording is silence anyway.
    taper = min(len(audio) // 2, max(1, int(orig_rate * 0.005)))
    if taper > 1:
        audio = audio.copy()
        window = np.hanning(taper * 2)
        audio[:taper] *= window[:taper]
        audio[-taper:] *= window[taper:]
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


_DOWNLOADED_MARKER = os.path.join(MODEL_DIR, ".talk-download-complete")


# The model is about 464 MB. Anything much smaller than this is not a
# model, whatever the folder looks like — it is the wreckage of a
# download that stopped early.
_MIN_CACHED_BYTES = 380 * 1024 * 1024


def _cached_bytes():
    """Bytes of finished model data on disk, and whether any is partial.

    Counts THIS model's folder alone. It used to add up every
    "models--" folder it found, which was fine while only one model had
    ever been downloaded — but the app has changed model since, and an
    old 600 MB Parakeet left behind would otherwise be counted as proof
    that Whisper is present, sending the app offline around a model it
    has not got.
    """
    model_dir = os.path.join(MODEL_DIR, MODEL_DIR_NAME)
    total = 0
    partial = False
    try:
        for root, _dirs, files in os.walk(model_dir):
            for name in files:
                path = os.path.join(root, name)
                if name.endswith(".incomplete"):
                    partial = True
                    continue
                try:
                    if not os.path.islink(path):
                        total += os.path.getsize(path)
                except OSError:
                    continue
    except OSError:
        pass
    return total, partial


def _purge_superseded_models():
    """Delete the model this app used to use, once the new one is in.

    Six hundred megabytes of a model nothing loads any more, sitting in
    a folder the user never chose and cannot see. Removing it is the
    only decent thing to do, and it happens after the replacement is
    known good so a failure here can never leave someone with neither.
    """
    import shutil
    for parent in (MODEL_DIR, os.path.join(MODEL_DIR, "hub")):
        try:
            entries = os.listdir(parent)
        except OSError:
            continue
        for entry in entries:
            if not entry.startswith("models--") or entry == MODEL_DIR_NAME:
                continue
            path = os.path.join(parent, entry)
            try:
                shutil.rmtree(path)
                log.info("removed the superseded model at %s", path)
            except OSError:
                log.warning("could not remove %s", path, exc_info=True)


def _model_cached():
    """Whether a usable model is already on disk.

    Two ways to get this wrong, both of which happened. Counting a
    partial download as cached pins the app offline around a model that
    cannot load, and it then fails identically on every start with no way
    back. And counting ANY finished file as cached does the same thing
    more quietly: a download interrupted after the small files but before
    the big ones leaves a config file and a vocabulary list, no
    ".incomplete" anywhere, and about a megabyte of "model" — which the
    old check accepted, marked complete, and went offline around.

    So: no partial files, and enough bytes to actually be the model. The
    marker written by a successful load is trusted only while the bytes
    still back it up, which lets a machine already in that state heal
    itself rather than needing the folder deleted by hand.
    """
    total, partial = _cached_bytes()
    if os.path.exists(_DOWNLOADED_MARKER):
        if total >= _MIN_CACHED_BYTES:
            return True
        log.warning("the cache is marked complete but holds only %.1f MB; "
                    "fetching the model again", total / (1024 * 1024))
        _clear_cached_marker()
        return False
    if partial or total < _MIN_CACHED_BYTES:
        return False
    _mark_cached()
    return True


def _clear_cached_marker():
    """Forget that the model was downloaded, so the next load re-fetches."""
    try:
        os.remove(_DOWNLOADED_MARKER)
    except OSError:
        pass


def _mark_cached():
    os.makedirs(MODEL_DIR, exist_ok=True)
    open(_DOWNLOADED_MARKER, "w", encoding="utf-8").close()


def _configure_hub(offline):
    """Keep the model download inside our own folder, and quiet.

    The real offline lock is local_files_only=True on the loaded model,
    which makes the library incapable of reaching the network rather
    than merely disinclined; this is the belt to that pair of braces,
    and it keeps anything the hub writes on its own account inside the
    app's folder instead of the user's home cache.
    """
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Make the folder before pointing anything at it. On a first run it
    # does not exist yet, and a cache directory that is not there is one
    # more way for a download to fail before it starts.
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
    except OSError:
        log.warning("could not create the model folder %s", MODEL_DIR)
    os.environ["HF_HOME"] = MODEL_DIR
    # Without a timeout a stalled connection hangs forever: the socket
    # stays open, no bytes arrive, and the app sits on its "downloading"
    # spinner with nothing to show for it. Seen for real — the transfer
    # died at 74 MB of 600 and never recovered. A bounded timeout turns
    # that into an error we can retry, and Hugging Face resumes from
    # where the file stopped rather than starting again.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "20")
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
    # The environment variable alone is not enough once the library is
    # already loaded: huggingface_hub reads every one of them at import
    # time into module constants and never looks at the environment
    # again. The call that pins this process offline happens after the
    # first-run download, which is necessarily after the import — so it
    # silently did nothing, on the one run where it was the only thing
    # standing between "downloaded the model once" and "free to make
    # further requests for the rest of the session". Setting the
    # constant too is what actually closes it. Only if the library is
    # already loaded: importing it here just to set a flag would drag a
    # heavy import into startup for no reason.
    hub = sys.modules.get("huggingface_hub")
    if hub is not None:
        try:
            hub.constants.HF_HUB_OFFLINE = bool(offline)
        except Exception:
            log.warning("could not pin huggingface_hub offline=%s", offline,
                        exc_info=True)


def _save_debug_copy(audio):
    """Keep the recording as a file, but only when asked to in so many words.

    Off unless TALK_SAVE_AUDIO is set in the environment. There is no
    setting for it and no way to turn it on by accident: this app's whole
    claim is that what you say stays in memory and goes nowhere, so
    writing your voice to disk has to be a deliberate act taken to
    diagnose one fault, not a switch someone can leave on and forget.
    """
    if not os.environ.get("TALK_SAVE_AUDIO") or len(audio) == 0:
        return
    import wave
    from .config import _LOG_DIR
    path = os.path.join(
        _LOG_DIR, time.strftime("talk-audio-%Y%m%d-%H%M%S.wav"))
    try:
        pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        log.warning("TALK_SAVE_AUDIO is on: recording written to %s", path)
    except Exception:
        log.exception("could not write the debug recording")


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
        self._frames = 0
        self._warned = False
        self._capped = False
        self._native_rate = SAMPLE_RATE
        self._started_at = None
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
            self._frames = 0
            self._warned = False
            self._capped = False
            self._started_at = time.monotonic()
            device = self._device()
            self._native_rate = self._device_rate(device)
            cap_frames = self._native_rate * MAX_SECONDS

            def callback(indata, frames, time_info, status):
                # The MAX_SECONDS cap compares ACTUAL captured frames,
                # counted as they arrive. It used to be estimated as
                # len(chunks) * this callback's frame count - but with
                # no fixed blocksize requested, PipeWire/PortAudio
                # delivers wildly variable buffer sizes, so every large
                # buffer made that product spuriously exceed the cap
                # and silently DROP the chunk (then a small buffer
                # would shrink the estimate and appending resumed).
                # The result was holes punched all through longer
                # dictations - stream live, level meter moving, words
                # missing. Measured at 3-15%+ of audio lost on a
                # realistic small-quantum-with-spikes buffer pattern.
                # A real driver-side overflow ALSO loses audio - log the
                # first one per recording so the two causes can never be
                # confused again if words go missing.
                if status and not self._warned:
                    self._warned = True
                    log.warning("audio callback status: %s", status)
                with self._lock:
                    if self._frames < cap_frames:
                        chunk = indata[:, 0].copy()
                        self._chunks.append(chunk)
                        self._frames += len(chunk)
                    elif not self._capped:
                        # The cap exists to bound memory, not to vanish
                        # words silently. Recording continued past this
                        # point but nothing after it was kept — stop()
                        # reports that so the app can say so, instead of
                        # quietly transcribing a dictation that is
                        # missing its own ending.
                        self._capped = True
                        log.warning(
                            "recording hit the %ds cap; audio after this "
                            "point was not captured", MAX_SECONDS)
                if self.on_level is not None:
                    rms = float(np.sqrt(np.mean(indata ** 2)))
                    self.on_level(min(1.0, rms * 8))

            self._stream = sd.InputStream(
                samplerate=self._native_rate, channels=1, dtype="float32",
                device=device, callback=callback)
            self._stream.start()

    def stop(self):
        """Stop capture and return the recording as float32 mono 16 kHz.

        PortAudio's own stop() blocks until every already-captured buffer
        has been handed to our callback - it is documented to wait for
        pending audio, not just tell the device to shut up. This used to
        grab self._chunks BEFORE calling it, so whatever the driver was
        still delivering at the instant of the click landed in a fresh
        list nobody ever read, and every recording quietly lost however
        much audio happened to be in flight at that exact moment. Always
        the end, because that is where "in flight when you clicked stop"
        always is - and the amount lost rode entirely on how large a
        buffer PipeWire happened to be holding right then, which is
        exactly the wildly variable, seemingly random pattern this
        produced in practice.

        Stopping the stream before touching self._chunks - and outside
        the lock, so the callback's own last append is never blocked
        waiting for a lock this method is sitting on - is what actually
        guarantees nothing said is missing.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            native_rate = self._native_rate
            started_at = self._started_at
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
            frames = self._frames

        # Did the sound system actually hand over as much audio as the
        # clock says it should have? Nothing else in this pipeline can
        # tell. A dictation that comes back missing its last stretch
        # looks identical whether the words were never captured or were
        # lost later, and every other stage has been ruled out by
        # measurement — so measure this one too rather than trusting it.
        # PortAudio reports a status flag for overflows it notices; this
        # catches the case where frames simply never arrive and nothing
        # is flagged at all.
        if started_at is not None and native_rate:
            wall = time.monotonic() - started_at
            captured = frames / float(native_rate)
            if wall > 1.0 and captured < wall * 0.97:
                log.warning(
                    "captured %.1fs of audio but the microphone was open "
                    "for %.1fs — %.1fs (%.0f%%) never arrived",
                    captured, wall, wall - captured,
                    100 * (wall - captured) / wall)
            else:
                log.info("captured %.1fs of audio in %.1fs", captured, wall)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks)
        # PortAudio/ALSA streams commonly produce a brief click or pop
        # in their first callback buffer(s) while the device is still
        # settling right after open - a hardware/driver-level transient
        # the FFT-periodicity taper in _resample() doesn't touch at all
        # (that one only smooths the buffer's OWN edges, it can't remove
        # a real artifact baked into the captured samples themselves).
        # The model kept hearing that transient as a stray leading
        # consonant. Push-to-talk users don't speak in the instant they
        # press the key anyway, so discarding a small warm-up window
        # up front is silent in practice and clears it at the source.
        warmup = min(len(audio) // 3, int(native_rate * WARMUP_SECONDS))
        if warmup > 0:
            audio = audio[warmup:]
        if native_rate != SAMPLE_RATE:
            audio = _resample(audio, native_rate, SAMPLE_RATE)
        _save_debug_copy(audio)
        return audio

    @property
    def recording(self):
        with self._lock:
            return self._stream is not None

    @property
    def capped(self):
        """Whether the just-finished recording ran into MAX_SECONDS.

        Valid after stop(): true means what got transcribed is missing
        whatever was said past the limit, and the caller should tell the
        user that in as many words rather than let a cut-off dictation
        look like a complete one.
        """
        return self._capped


class Transcriber:
    """Owns the Whisper model and a serial transcription queue."""

    def __init__(self, on_ready=None, on_error=None, on_downloading=None):
        self._model = None
        self._queue = queue.Queue()
        self.on_ready = on_ready
        self.on_error = on_error
        self.on_downloading = on_downloading
        self._loading = False
        self._start_loader()

    def _start_loader(self):
        if self._loading or self._model is not None:
            return False
        self._loading = True
        threading.Thread(target=self._run, name="transcriber",
                         daemon=True).start()
        return True

    def retry(self):
        """Try the download again after it gave up.

        The loading thread ends when the model cannot be fetched, so
        without this the app sits there with nothing running behind it —
        which is what "Resume" used to do: change the icon and nothing
        else.
        """
        return self._start_loader()

    @property
    def ready(self):
        return self._model is not None

    def _load(self):
        try:
            self._load_once()
        except Exception:
            # A model that will not load while we believe it is cached
            # means the cache is wrong, not the network. Clear the marker
            # and fetch it again rather than failing identically on every
            # future start.
            if not _model_cached():
                raise
            log.warning("cached model failed to load; re-fetching it")
            _clear_cached_marker()
            self._load_once()

    def _load_once(self):
        cached = _model_cached()
        _configure_hub(offline=cached)
        if not cached:
            log.info("model not cached — this run will download it once")
            if self.on_downloading is not None:
                self.on_downloading()
        from faster_whisper import WhisperModel
        log.info("loading whisper %s", MODEL_NAME)
        if cached:
            # local_files_only is the real lock, not the environment
            # variable: it makes the library incapable of reaching the
            # network for this model rather than merely disinclined.
            self._model = WhisperModel(
                MODEL_NAME, device="cpu", compute_type="int8",
                download_root=MODEL_DIR, local_files_only=True)
        else:
            self._model = self._download_with_retries(WhisperModel)
            _mark_cached()
            _configure_hub(offline=True)
        log.info("model ready")
        # Only once the replacement is loaded and working.
        _purge_superseded_models()
        if self.on_ready is not None:
            self.on_ready()

    # The first-run download is ~464 MB over a public endpoint, and a
    # stalled transfer is common enough that one attempt is not enough.
    _DOWNLOAD_ATTEMPTS = 5

    def _download_with_retries(self, WhisperModel):
        """Fetch the model, resuming after a stall rather than hanging."""
        last = None
        for attempt in range(1, self._DOWNLOAD_ATTEMPTS + 1):
            try:
                return WhisperModel(
                    MODEL_NAME, device="cpu", compute_type="int8",
                    download_root=MODEL_DIR, local_files_only=False)
            except Exception as exc:
                last = exc
                log.warning("model download attempt %d/%d failed: %s",
                            attempt, self._DOWNLOAD_ATTEMPTS, exc)
                if self.on_downloading is not None:
                    self.on_downloading()
                time.sleep(min(5 * attempt, 20))
        raise last

    def _run(self):
        try:
            self._load()
        except Exception:
            log.exception("model failed to load")
            self._loading = False
            if self.on_error is not None:
                self.on_error("error.model")
            return
        self._loading = False
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
        # language=None: the model settles on whichever one is being
        # spoken, which is what the app has always promised and why
        # there is no language to choose before dictating.
        # beam_size=1 is greedy decoding - the wider search costs
        # seconds per dictation and changed nothing measurable here.
        segments, _info = self._model.transcribe(
            audio, language=None, beam_size=1)
        # transcribe() returns a generator: nothing is actually decoded
        # until this is walked.
        return " ".join(s.text.strip() for s in segments).strip()

    def submit(self, audio, callback):
        """Queue audio; callback(text, error_key) runs on worker thread."""
        self._queue.put((audio, callback))
