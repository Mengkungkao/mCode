"""Voice: talk to the AI Engineer and hear short status messages.

States published on "voice": idle -> listening -> processing -> heard
(text) or error. The LCD's AI Engineer screen arms the microphone when it
opens and disarms it when it closes; holding the button records; releasing
transcribes off the interface thread.

Feedback (``voice.feedback``): "spoken" says short phrases ("Build
successful."), "tones" plays cues only, "off" is silent. Feedback plays only
while mCode is on the screen (the LCD interface decides) and never while the
microphone records.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from ..core.envfile import Secrets, create_from_template
from ..paths import CODE_DIR
from . import audio, devices, speech

log = logging.getLogger("mcode.voice")

PHRASES = {
    "ready": "Ready.",
    "listening": "Listening.",
    "working": "Claude is working on it.",
    "creating": "Creating your project.",
    "project_ready": "Your project is ready.",
    "building": "Building.",
    "build_ok": "Build successful.",
    "build_failed": "The build failed.",
    "flashing": "Flashing the board.",
    "flash_ok": "Firmware uploaded. Your board is ready.",
    "confirm": "Please confirm on the screen.",
    "review": "Claude has changes for you to review.",
    "done": "Done.",
    "error": "Something went wrong.",
}
PHRASE_CUE = {"ready": "done", "listening": "listen", "build_ok": "success", "flash_ok": "success",
              "project_ready": "success", "done": "success", "build_failed": "error",
              "error": "error", "confirm": "confirm", "review": "confirm"}
MIN_SPEECH_SECONDS = 0.4


class VoiceService:
    def __init__(self, studio, capture_spawn=None, play_spawn=None):
        self.studio = studio
        self.settings = studio.settings
        template = os.path.join(CODE_DIR, ".env.example")
        if os.path.exists(template):
            try:
                create_from_template(template, studio.paths.env_file)
            except OSError as exc:
                log.info("could not create %s: %s", studio.paths.env_file, exc)
        self.secrets = Secrets(studio.paths.env_file)
        self.state = "idle"
        self.text = ""
        self.error = ""
        self.level = -90.0
        self.started = 0.0
        self._lock = threading.Lock()
        capture, self.capture_info = devices.resolve("capture", self.settings.get("voice.capture_device"))
        playback, self.playback_info = devices.resolve("playback",
                                                       self.settings.get("voice.playback_device"))
        self.mic = audio.PushToTalk(capture, spawn=capture_spawn or audio.spawn_arecord,
                                    on_level=self._on_level)
        self.player = audio.Player(playback, spawn=play_spawn or audio.spawn_aplay)
        self.enabled_output = True           # the LCD turns this off while mCode is not in front

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        provider = speech.stt_provider(self.settings, self.secrets)
        return {"state": self.state, "text": self.text, "error": self.error,
                "level": round(self.level, 1), "microphone": self.mic.device or "",
                "microphone_info": self.capture_info, "speaker": self.player.device or "",
                "speaker_info": self.playback_info, "stt": provider,
                "stt_ready": provider != "off", "tts": speech.tts_provider(self.settings,
                                                                            self.secrets),
                "feedback": self.settings.get("voice.feedback"),
                "keys": self.secrets.describe()}

    def _set(self, state: str, text: str = "", error: str = "") -> None:
        with self._lock:
            self.state = state
            if text or state in ("idle", "listening"):
                self.text = text
            self.error = error
        self.studio.events.publish("voice", self.status())

    def _on_level(self, level: float) -> None:
        self.level = level
        if self.state == "listening":
            self.studio.events.publish("voice.level", {"level": round(level, 1)})

    # ------------------------------------------------------------- listening
    def arm(self) -> bool:
        return self.mic.arm()

    def disarm(self) -> None:
        self.mic.disarm()
        if self.state == "listening":
            self._set("idle")

    def start_talk(self) -> bool:
        self.player.stop()
        if not self.mic.start():
            self._set("error", error=self.mic.error or "No microphone found.")
            return False
        self.started = time.monotonic()
        self._set("listening")
        return True

    def end_talk(self) -> None:
        pcm = self.mic.stop()
        seconds = len(pcm) / (audio.RATE * 4)
        if self.state != "listening":
            return
        if seconds < MIN_SPEECH_SECONDS + 0.4:
            self._set("idle", error="")
            return
        self._set("processing")
        threading.Thread(target=self._transcribe, args=(pcm,), name="voice-stt",
                         daemon=True).start()

    def cancel(self) -> None:
        self.mic.stop()
        self._set("idle")

    def _transcribe(self, pcm: bytes) -> None:
        try:
            wav = audio.wav_bytes(audio.stereo48_to_mono16(pcm))
            text = speech.transcribe(wav, self.settings, self.secrets)
        except speech.SpeechError as exc:
            self._set("error", error=str(exc))
            return
        except Exception as exc:        # isolation boundary: report, keep the app alive
            log.exception("transcription failed")
            self._set("error", error=f"Speech recognition failed: {exc}")
            return
        if not text:
            self._set("error", error="I did not catch that. Hold the button and speak again.")
            return
        log.info("heard %d characters", len(text))
        self._set("heard", text=text)

    def clear(self) -> None:
        self._set("idle")

    # --------------------------------------------------------------- speaking
    def say(self, phrase: str) -> None:
        """Short feedback: spoken, a tone, or nothing (per the setting)."""
        mode = self.settings.get("voice.feedback")
        if mode == "off" or not self.enabled_output or not self.player.available:
            return
        if self.state == "listening":
            return
        threading.Thread(target=self._say, args=(phrase, mode), name="voice-say",
                         daemon=True).start()

    def _say(self, phrase: str, mode: str) -> None:
        pcm = b""
        if mode == "spoken":
            text = PHRASES.get(phrase, phrase)
            try:
                wav = speech.synthesize(text, self.settings, self.secrets,
                                        self.studio.paths.voice_cache)
                if wav:
                    pcm = audio.wav_to_mono48(wav)
            except Exception as exc:        # feedback is optional: fall back to a tone
                log.info("spoken feedback failed: %s", exc)
        if not pcm:
            pcm = audio.cue_pcm(PHRASE_CUE.get(phrase, "done"))
        self.player.play(pcm)

    def cue(self, kind: str) -> None:
        if self.settings.get("voice.feedback") == "off" or not self.enabled_output:
            return
        self.player.play_async(audio.cue_pcm(kind))

    def stop(self) -> None:
        self.mic.disarm()
        self.player.stop()
