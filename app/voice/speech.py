"""Speech to text and text to speech, through services the owner chooses.

Speech recognition (``voice.stt``):

* ``openai``: OpenAI's transcription API with the owner's key from mCode's
  own ``.env`` (``OPENAI_API_KEY``); a vocabulary hint (ESP32, PlatformIO,
  GPIO, I2C, ...) makes engineering words come out right.
* ``command``: any local recogniser (whisper.cpp, Vosk, ...): mCode runs
  the configured command with ``{wav}`` replaced by the recording and uses
  what it prints.
* ``auto``: OpenAI when a key is set, else the command when one is set.

No speech model is installed or run by default: a Pi Zero 2 W cannot run a
useful one, and the brief asks not to load heavy models.

Spoken feedback (``voice.tts``): OpenAI speech or eSpeak NG; every phrase is
synthesised once and cached as a WAV file in the data folder, so after the
first time feedback needs no network. Without either, cues are tones.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from typing import Optional

log = logging.getLogger("mcode.speech")

VOCABULARY = ("ESP32, ESP32-S3, ESP32-C3, ESP32-C6, ESP-IDF, Arduino, Arduino Uno, Arduino Mega, "
              "Arduino Nano, PlatformIO, Raspberry Pi Pico, GPIO, I2C, SPI, UART, PWM, ADC, "
              "PCA9685, servo, stepper, MPU6050, BME280, DHT22, NeoPixel, WS2812, OLED, SSD1306, "
              "firmware, sketch, compile, build, flash, upload, serial monitor, baud rate, "
              "Wi-Fi, Bluetooth, MQTT, mCode")


class SpeechError(Exception):
    pass


def _base(secrets) -> str:
    return (secrets.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")


def stt_provider(settings, secrets) -> str:
    choice = settings.get("voice.stt")
    if choice == "auto":
        if secrets.has("OPENAI_API_KEY"):
            return "openai"
        if settings.get("voice.stt_command"):
            return "command"
        return "off"
    return choice


def transcribe(wav: bytes, settings, secrets, timeout: float = 60.0) -> str:
    provider = stt_provider(settings, secrets)
    if provider == "openai":
        return _openai_transcribe(wav, settings, secrets, timeout)
    if provider == "command":
        return _command_transcribe(wav, settings.get("voice.stt_command"), timeout)
    raise SpeechError("Speech recognition is off. Add an OpenAI key to mCode's .env file or set "
                      "a local speech command in Settings > Voice; or type on a keyboard.")


def _multipart(fields: dict, files: dict) -> tuple:
    boundary = "mcode-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                      f"{value}\r\n".encode())
    for name, (filename, data, ctype) in files.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                      f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode() + data +
                      b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _openai_transcribe(wav: bytes, settings, secrets, timeout: float) -> str:
    key = secrets.get("OPENAI_API_KEY")
    if not key:
        raise SpeechError("No OpenAI key: add OPENAI_API_KEY to mCode's .env file.")
    fields = {"model": settings.get("voice.stt_model") or "gpt-4o-mini-transcribe",
              "response_format": "json", "prompt": f"Embedded development vocabulary: {VOCABULARY}."}
    language = (settings.get("voice.language") or "").strip()
    if language:
        fields["language"] = language
    body, ctype = _multipart(fields, {"file": ("speech.wav", wav, "audio/wav")})
    request = urllib.request.Request(_base(secrets) + "/audio/transcriptions", data=body,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SpeechError(_http_problem(exc)) from None
    except (OSError, ValueError) as exc:
        raise SpeechError(f"Speech service not reachable: {exc}") from None
    return (data.get("text") or "").strip()


def _http_problem(exc: "urllib.error.HTTPError") -> str:
    if exc.code == 401:
        return "The OpenAI key was refused (check OPENAI_API_KEY)."
    if exc.code == 429:
        return "The speech service is busy or the quota is used up."
    return f"The speech service answered {exc.code}."


def _command_transcribe(wav: bytes, command: str, timeout: float) -> str:
    if not command or "{wav}" not in command:
        raise SpeechError("Set a speech command containing {wav} in Settings > Voice.")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(wav)
        path = handle.name
    try:
        argv = [part.replace("{wav}", path) for part in shlex.split(command)]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise SpeechError("The speech command took too long.") from None
    except OSError as exc:
        raise SpeechError(f"Cannot run the speech command: {exc.strerror}") from None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode != 0:
        raise SpeechError(f"The speech command failed: {(result.stderr or '').strip()[-200:]}")
    return " ".join(result.stdout.split()).strip()


# ------------------------------------------------------------------- speaking

def tts_provider(settings, secrets) -> str:
    choice = settings.get("voice.tts")
    if choice == "auto":
        if secrets.has("OPENAI_API_KEY"):
            return "openai"
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            return "espeak"
        return "off"
    return choice


def synthesize(text: str, settings, secrets, cache_dir: str, timeout: float = 30.0) -> Optional[bytes]:
    """A WAV for ``text`` (from the cache when possible), or None when no voice is set up."""
    provider = tts_provider(settings, secrets)
    if provider == "off":
        return None
    key = hashlib.sha256(f"{provider}|{text}".encode()).hexdigest()[:20]
    path = os.path.join(cache_dir, f"{key}.wav")
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        pass
    if provider == "openai":
        data = _openai_speech(text, secrets, timeout)
    else:
        data = _espeak(text, settings)
    if data:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    return data


def _openai_speech(text: str, secrets, timeout: float) -> Optional[bytes]:
    key = secrets.get("OPENAI_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": "gpt-4o-mini-tts", "voice": "alloy", "input": text,
                       "response_format": "wav",
                       "instructions": "Short, friendly, calm status message."}).encode()
    request = urllib.request.Request(_base(secrets) + "/audio/speech", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, urllib.error.HTTPError) as exc:
        log.info("spoken feedback unavailable: %s", exc)
        return None


def _espeak(text: str, settings) -> Optional[bytes]:
    program = shutil.which("espeak-ng") or shutil.which("espeak")
    if not program:
        return None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = handle.name
    try:
        language = (settings.get("voice.language") or "en").strip() or "en"
        subprocess.run([program, "-v", language, "-s", "165", "-w", path, text],
                       capture_output=True, timeout=20, stdin=subprocess.DEVNULL, check=True)
        with open(path, "rb") as handle:
            return handle.read()
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("eSpeak failed: %s", exc)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
