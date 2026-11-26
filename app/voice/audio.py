"""Sound in and out, standard library only (no NumPy: it must run on a Pi Zero).

* :class:`PushToTalk` keeps one ``arecord`` stream open while the AI
  Engineer screen is shown (the WM8960's ADC needs ~0.7 s to power up on a
  cold open, which would cut the first word), throws the audio away until
  the button is held, keeps 0.4 s of pre-roll so the first syllable is not
  lost, and returns the recording when the button is released. Leaving the
  screen closes the microphone completely.
* Conversion: 48 kHz stereo from the card -> 16 kHz mono WAV for speech
  recognition (the louder channel: opi2w's microphone is on the left only).
* :func:`play_pcm` writes 48 kHz mono to ``aplay``; :func:`tone` makes cues.
"""

from __future__ import annotations

import array
import io
import logging
import math
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from typing import Callable, Optional

log = logging.getLogger("mcode.audio")

RATE = 48000
CHUNK = 7680                    # 40 ms of 48 kHz 16-bit stereo
PREROLL_CHUNKS = 10             # 0.4 s
MAX_SECONDS = 60


def spawn_arecord(device: str):
    command = ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "2"]
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                            stdin=subprocess.DEVNULL)


def spawn_aplay(device: str, rate: int = RATE):
    command = ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-"]
    return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdout=subprocess.DEVNULL)


def _samples(pcm: bytes) -> array.array:
    data = array.array("h")
    data.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        data.byteswap()
    return data


def _bytes(samples: array.array) -> bytes:
    if sys.byteorder == "big":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def level_dbfs(pcm: bytes) -> float:
    """Rough level of a chunk (a sample of it: this runs for every 40 ms)."""
    data = _samples(pcm)[::7]
    if not data:
        return -90.0
    power = sum(s * s for s in data) / len(data)
    return 10 * math.log10(power / (32768.0 ** 2)) if power > 0 else -90.0


def stereo48_to_mono16(pcm: bytes) -> bytes:
    """48 kHz stereo -> 16 kHz mono: the louder channel, averaged in threes."""
    data = _samples(pcm)
    left, right = data[0::2], data[1::2]
    sample_l, sample_r = left[::40], right[::40]
    energy_l = sum(abs(s) for s in sample_l)
    energy_r = sum(abs(s) for s in sample_r)
    mono = left if energy_l >= energy_r else right
    n = len(mono) // 3 * 3
    out = array.array("h", ((mono[i] + mono[i + 1] + mono[i + 2]) // 3 for i in range(0, n, 3)))
    return _bytes(out)


def wav_bytes(pcm: bytes, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def wav_to_mono48(data: bytes) -> bytes:
    """Any 16-bit WAV -> 48 kHz mono PCM (linear interpolation; short phrases only)."""
    with wave.open(io.BytesIO(data), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV is supported")
    samples = _samples(frames)
    if channels > 1:
        samples = array.array("h", samples[0::channels])
    if rate == RATE:
        return _bytes(samples)
    ratio = rate / RATE
    count = int(len(samples) / ratio)
    out = array.array("h", bytes(2 * count))
    last = len(samples) - 1
    for i in range(count):
        pos = i * ratio
        j = int(pos)
        frac = pos - j
        a = samples[j]
        b = samples[j + 1] if j < last else a
        out[i] = int(a + (b - a) * frac)
    return _bytes(out)


def tone(freq: float, seconds: float, volume: float = 0.25) -> bytes:
    count = int(RATE * seconds)
    fade = max(1, int(RATE * 0.008))
    out = array.array("h", bytes(2 * count))
    for i in range(count):
        envelope = min(1.0, i / fade, (count - i) / fade)
        out[i] = int(32767 * volume * envelope * math.sin(2 * math.pi * freq * i / RATE))
    return _bytes(out)


CUES = {
    "listen": [(880, 0.06), (1175, 0.07)],
    "done": [(988, 0.05), (740, 0.06)],
    "success": [(784, 0.07), (988, 0.07), (1319, 0.10)],
    "error": [(330, 0.16), (262, 0.20)],
    "confirm": [(660, 0.05), (660, 0.05)],
}


def cue_pcm(kind: str) -> bytes:
    return b"".join(tone(f, s) for f, s in CUES.get(kind, CUES["done"]))


class Player:
    """One clip at a time on the speaker; ``stop`` cuts it (e.g. the user starts talking)."""

    def __init__(self, device: Optional[str], spawn: Callable = spawn_aplay):
        self.device = device
        self.spawn = spawn
        self._lock = threading.Lock()
        self._process = None

    @property
    def available(self) -> bool:
        return bool(self.device)

    def play(self, pcm: bytes) -> bool:
        if not self.device or not pcm:
            return False
        self.stop()
        try:
            process = self.spawn(self.device)
        except OSError as exc:
            log.warning("cannot start aplay: %s", exc)
            return False
        with self._lock:
            self._process = process
        try:
            process.stdin.write(pcm)
            process.stdin.close()
            process.wait(timeout=len(pcm) / (2 * RATE) + 5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            self._kill(process)
        with self._lock:
            if self._process is process:
                self._process = None
        return process.returncode == 0

    def play_async(self, pcm: bytes) -> None:
        threading.Thread(target=self.play, args=(pcm,), name="voice-play", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            self._kill(process)

    @staticmethod
    def _kill(process) -> None:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1)
        except Exception:
            pass


class PushToTalk:
    """The microphone for push-to-talk. ``on_level(dbfs)`` runs on the capture thread."""

    def __init__(self, device: Optional[str], spawn: Callable = spawn_arecord,
                 on_level: Callable[[float], None] = lambda level: None):
        self.device = device
        self.spawn = spawn
        self.on_level = on_level
        self.error = ""
        self._lock = threading.Lock()
        self._process = None
        self._thread: Optional[threading.Thread] = None
        self._preroll: deque = deque(maxlen=PREROLL_CHUNKS)
        self._recording: Optional[list] = None
        self._recorded = 0
        self._last_level = 0.0

    @property
    def available(self) -> bool:
        return bool(self.device)

    @property
    def open(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def arm(self) -> bool:
        """Open the microphone (idempotent)."""
        if not self.device:
            self.error = "No microphone found."
            return False
        with self._lock:
            if self.open:
                return True
            try:
                process = self.spawn(self.device)
            except OSError as exc:
                self.error = f"cannot start arecord: {exc}"
                return False
            self._process = process
            self._preroll.clear()
            self.error = ""
            self._thread = threading.Thread(target=self._read, args=(process,), name="voice-mic",
                                            daemon=True)
            self._thread.start()
        return True

    def disarm(self) -> None:
        """Close the microphone: it is off, not merely ignored."""
        with self._lock:
            process, self._process = self._process, None
            thread, self._thread = self._thread, None
            self._recording = None
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=1)
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

    def start(self) -> bool:
        if not self.arm():
            return False
        with self._lock:
            self._recording = list(self._preroll)
            self._recorded = sum(len(c) for c in self._recording)
        return True

    def stop(self) -> bytes:
        """End the recording; returns 48 kHz stereo PCM (b"" if nothing)."""
        with self._lock:
            chunks, self._recording = self._recording, None
        return b"".join(chunks or [])

    def _read(self, process) -> None:
        while True:
            try:
                chunk = process.stdout.read(CHUNK)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                break
            with self._lock:
                if self._process is not process:
                    return
                if self._recording is not None:
                    if self._recorded < MAX_SECONDS * RATE * 4:
                        self._recording.append(chunk)
                        self._recorded += len(chunk)
                else:
                    self._preroll.append(chunk)
            now = time.monotonic()
            if now - self._last_level > 0.08:
                self._last_level = now
                self.on_level(level_dbfs(chunk))
        with self._lock:
            if self._process is process:
                try:
                    err = (process.stderr.read() or b"").decode("utf-8", "replace").strip()
                except (OSError, ValueError):
                    err = ""
                self.error = err.splitlines()[-1] if err else "the microphone stopped"
                self._process = None
                self._thread = None
                log.warning("microphone stopped: %s", self.error)
