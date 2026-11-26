"""Voice: audio conversion, push-to-talk with a fake arecord, speech providers."""

from __future__ import annotations

import array
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import Settings
from app.core.envfile import Secrets
from app.voice import audio, speech

from .helpers import FAKE_ARECORD, TempCase, write_script


class ConversionTests(unittest.TestCase):
    def test_louder_channel_and_decimation(self):
        left = [1000, 2000, 3000] * 160
        stereo = array.array("h")
        for value in left:
            stereo.extend([value, 0])
        mono = audio.stereo48_to_mono16(stereo.tobytes())
        out = array.array("h")
        out.frombytes(mono)
        self.assertEqual(len(out), len(left) // 3)
        self.assertEqual(set(out), {2000})
        right = array.array("h")
        for value in left:
            right.extend([0, value])
        out = array.array("h")
        out.frombytes(audio.stereo48_to_mono16(right.tobytes()))
        self.assertEqual(set(out), {2000})

    def test_wav_round_trip_and_resample(self):
        pcm = audio.tone(440, 0.1)
        data = audio.wav_bytes(pcm[: len(pcm) // 3], rate=16000)
        with wave.open(io.BytesIO(data)) as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels()), (16000, 1))
        up = audio.wav_to_mono48(data)
        self.assertAlmostEqual(len(up) / len(pcm[: len(pcm) // 3]), 3.0, delta=0.01)

    def test_cues_and_levels(self):
        self.assertGreater(len(audio.cue_pcm("success")), 0)
        self.assertLess(audio.level_dbfs(b"\x00\x00" * 100), -80)
        self.assertGreater(audio.level_dbfs(audio.tone(440, 0.05, volume=0.5)), -20)


class PushToTalkTests(TempCase):
    def test_record_with_preroll_and_disarm(self):
        script = write_script(os.path.join(self.tmp, "arecord"), FAKE_ARECORD)
        levels = []
        mic = audio.PushToTalk("capture", spawn=lambda device: subprocess.Popen(
            [script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0),
            on_level=levels.append)
        self.assertTrue(mic.arm())
        time.sleep(0.5)                                  # pre-roll fills
        self.assertTrue(mic.start())
        time.sleep(0.4)
        pcm = mic.stop()
        mic.disarm()
        self.assertFalse(mic.open)
        self.assertGreater(len(pcm), audio.RATE * 4 * 0.4)    # pre-roll + speech
        self.assertTrue(levels and max(levels) > -40)

    def test_no_microphone(self):
        mic = audio.PushToTalk(None)
        self.assertFalse(mic.arm())
        self.assertIn("microphone", mic.error.lower())


class FakeOpenAI(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeOpenAI.seen = {"path": self.path, "auth": self.headers.get("Authorization"),
                           "type": self.headers.get("Content-Type"), "body": body}
        if self.path.endswith("/audio/transcriptions"):
            payload = json.dumps({"text": " Create an ESP32 project. "}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            payload = audio.wav_bytes(audio.tone(500, 0.1)[:3200], rate=24000)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class SpeechTests(TempCase):
    def server(self):
        httpd = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}/v1"

    def secrets(self, **values):
        path = os.path.join(self.tmp, ".env")
        with open(path, "w") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
        os.chmod(path, 0o600)
        return Secrets(path, environ={})

    def test_openai_transcription_request(self):
        settings = Settings(None)
        secrets = self.secrets(OPENAI_API_KEY="sk-test-123", OPENAI_BASE_URL=self.server())
        self.assertEqual(speech.stt_provider(settings, secrets), "openai")
        text = speech.transcribe(audio.wav_bytes(b"\x00\x00" * 1600), settings, secrets)
        self.assertEqual(text, "Create an ESP32 project.")
        seen = FakeOpenAI.seen
        self.assertTrue(seen["path"].endswith("/audio/transcriptions"))
        self.assertEqual(seen["auth"], "Bearer sk-test-123")
        self.assertIn(b"gpt-4o-mini-transcribe", seen["body"])
        self.assertIn(b"PlatformIO", seen["body"])            # vocabulary hint

    def test_tts_is_cached(self):
        settings = Settings(None)
        secrets = self.secrets(OPENAI_API_KEY="sk-test-123", OPENAI_BASE_URL=self.server())
        cache = os.path.join(self.tmp, "cache")
        first = speech.synthesize("Build successful.", settings, secrets, cache)
        self.assertTrue(first)
        FakeOpenAI.seen = {}
        self.assertEqual(speech.synthesize("Build successful.", settings, secrets, cache), first)
        self.assertEqual(FakeOpenAI.seen, {})                 # served from the cache

    def test_command_provider_and_off(self):
        settings = Settings(None)
        secrets = self.secrets()
        self.assertEqual(speech.stt_provider(settings, secrets), "off")
        with self.assertRaises(speech.SpeechError):
            speech.transcribe(b"", settings, secrets)
        tool = write_script(os.path.join(self.tmp, "stt"), "import sys\nprint('  blink the   led ')\n")
        settings.set("voice.stt_command", f"{tool} --file {{wav}}", save=False)
        self.assertEqual(speech.stt_provider(settings, secrets), "command")
        self.assertEqual(speech.transcribe(b"RIFF", settings, secrets), "blink the led")


class VoiceServiceTests(TempCase):
    def test_talk_to_heard(self):
        from app.voice.service import VoiceService
        studio = self.studio(hardware=False, ai=False, voice=False)
        script = write_script(os.path.join(self.tmp, "arecord"), FAKE_ARECORD)
        stt = write_script(os.path.join(self.tmp, "stt"), "print('create a servo project')\n")
        studio.settings.set("voice.stt_command", f"{stt} {{wav}}", save=False)
        studio.settings.set("voice.capture_device", "fake", save=False)
        studio.settings.set("voice.playback_device", "fake", save=False)
        played = []

        class Sink(io.BytesIO):
            def close(self):
                played.append(len(self.getvalue()))
                super().close()

        class FakePlayer:
            def __init__(self):
                self.stdin = Sink()
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        voice = VoiceService(studio, capture_spawn=lambda d: subprocess.Popen(
            [script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0),
            play_spawn=lambda d: FakePlayer())
        events = []
        studio.events.subscribe(lambda t, d: events.append((t, d)) if t == "voice" else None)
        self.assertTrue(voice.arm())
        self.assertTrue(voice.start_talk())
        time.sleep(1.0)
        voice.end_talk()
        deadline = time.time() + 10
        while voice.state not in ("heard", "error") and time.time() < deadline:
            time.sleep(0.05)
        voice.disarm()
        self.assertEqual(voice.state, "heard", voice.error)
        self.assertEqual(voice.text, "create a servo project")
        self.assertEqual([d["state"] for t, d in events][:2], ["listening", "processing"])
        studio.settings.set("voice.feedback", "tones", save=False)
        voice._say("build_ok", "tones")
        self.assertTrue(played)

    def test_too_short_press_is_ignored(self):
        from app.voice.service import VoiceService
        studio = self.studio(hardware=False, ai=False, voice=False)
        script = write_script(os.path.join(self.tmp, "arecord"), FAKE_ARECORD)
        studio.settings.set("voice.capture_device", "fake", save=False)
        voice = VoiceService(studio, capture_spawn=lambda d: subprocess.Popen(
            [script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0))
        voice.start_talk()
        voice.end_talk()
        voice.disarm()
        self.assertEqual(voice.state, "idle")


if __name__ == "__main__":
    unittest.main()
