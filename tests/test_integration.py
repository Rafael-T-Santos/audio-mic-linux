"""Teste de ponta a ponta contra um servidor de áudio real (PipeWire ou PulseAudio).

Cria dispositivos falsos ("fone" e "micfake"), toca tons com `paplay` fingindo ser o
Firefox e o microfone, grava do Microfone Virtual com `parec` e confere o que chega em
cada modo. É pulado se não houver servidor de áudio ou pulseaudio-utils.

    python3 -m unittest tests.test_integration -v
"""

import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio_mic._vendor import pulsectl  # noqa: E402
from audio_mic.config import Config  # noqa: E402
from audio_mic.core import (  # noqa: E402
    MIX_BOTH, MIX_MEDIA, MIX_VOICE, SHARE_ALL, VIRTUAL_MIC, Engine)

RATE = 48000
MEDIA_HZ = 440   # tom do "YouTube"
VOICE_HZ = 1000  # tom da "voz"


def server_available():
    if not (shutil.which("paplay") and shutil.which("parec")):
        return False
    try:
        pulsectl.Pulse("audio-mic-test-probe").close()
        return True
    except pulsectl.PulseError:
        return False


def write_tone(path, hz, seconds=30):
    samples = array.array("h", (int(8000 * math.sin(2 * math.pi * hz * i / RATE))
                                for i in range(int(RATE * seconds))))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def tone_power(samples, hz):
    """Goertzel: power of one frequency in a block of mono samples."""
    k = 2 * math.cos(2 * math.pi * hz / RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return (s1 * s1 + s2 * s2 - k * s1 * s2) / len(samples)


def record(source, seconds=0.6):
    proc = subprocess.Popen(
        ["parec", "-d", source, "--format=s16le", "--rate=%d" % RATE, "--channels=1",
         "--latency-msec=20"], stdout=subprocess.PIPE)
    time.sleep(seconds)
    proc.terminate()
    data = proc.communicate()[0]
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    return samples[len(samples) // 3:]  # skip the start-up transient


@unittest.skipUnless(server_available(), "sem servidor de áudio / pulseaudio-utils")
class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.pulse = pulsectl.Pulse("audio-mic-test")
        load = cls.pulse.module_load
        cls.fake_mods = [
            load("module-null-sink", "sink_name=test_fone"),
            load("module-null-sink", "sink_name=test_micsrc"),
            load("module-remap-source", "master=test_micsrc.monitor source_name=test_mic"),
            load("module-remap-source", "master=test_micsrc.monitor source_name=test_mic2"),
        ]
        time.sleep(0.3)
        cls.pulse.sink_default_set("test_fone")
        cls.pulse.source_default_set("test_mic")
        media, voice = os.path.join(cls.tmp, "m.wav"), os.path.join(cls.tmp, "v.wav")
        write_tone(media, MEDIA_HZ)
        write_tone(voice, VOICE_HZ)
        cls.players = [
            subprocess.Popen(["paplay", "-d", "test_fone",
                              "--property=application.name=Firefox",
                              "--property=application.process.binary=firefox", media]),
            subprocess.Popen(["paplay", "-d", "test_micsrc",
                              "--property=application.name=FakeVoice",
                              "--property=application.process.binary=fakevoice", voice]),
        ]
        time.sleep(0.8)

    @classmethod
    def tearDownClass(cls):
        for p in cls.players:
            p.terminate()
        Engine().stop()
        for idx in cls.fake_mods:
            try:
                cls.pulse.module_unload(idx)
            except pulsectl.PulseError:
                pass
        cls.pulse.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.cfg = Config(os.path.join(self.tmp, "config-%s.json" % self.id()))
        self.cfg.set("mic", "test_mic")
        self.cfg.set("output", "test_fone")
        self.cfg.set("shared_apps", ["firefox"])
        self.engine = Engine()
        self.engine.activate(self.cfg)
        time.sleep(0.8)

    def tearDown(self):
        self.engine.stop()
        self.engine.close()
        time.sleep(0.3)

    def levels(self, source=VIRTUAL_MIC):
        s = record(source)
        self.assertTrue(len(s) > RATE // 10, "o servidor de áudio não entregou amostras de " + source)
        return tone_power(s, MEDIA_HZ), tone_power(s, VOICE_HZ)

    # Um tom audível dá ~1e11; resíduos de mute/rampa ficam abaixo de ~1e5 (-60 dB).
    HEARD, SILENT = 1e9, 1e7

    def expect(self, media, voice, source=VIRTUAL_MIC, tries=6):
        """Wait until `source` has (True) / lacks (False) each tone; None = don't care.

        New loopbacks take a moment to start flowing, so poll instead of one snapshot.
        """
        def ok(level, want):
            return want is None or (level > self.HEARD if want else level < self.SILENT)

        for _ in range(tries):
            m, v = self.levels(source)
            if ok(m, media) and ok(v, voice):
                return
        self.fail("{}: mídia={:.3g} (esperado {}), voz={:.3g} (esperado {})".format(
            source, m, media, v, voice))

    def test_graph_created_and_idempotent(self):
        self.assertTrue(self.engine.is_active())
        before = len(self.engine._modules())
        self.engine.start("test_mic", "test_fone")
        self.assertEqual(len(self.engine._modules()), before)
        self.assertEqual(self.engine.current_devices(), {"mic": "test_mic", "output": "test_fone"})

    def test_voice_and_media(self):
        firefox = self.engine.find_apps("firefox")
        self.assertEqual(len(firefox), 1)
        self.assertTrue(firefox[0].shared)
        self.expect(media=True, voice=True)
        # Continuo ouvindo a mídia no fone, mas não a minha própria voz.
        self.expect(media=True, voice=False, source="test_fone.monitor")

    def test_media_only_and_voice_only(self):
        self.engine.set_mix_mode(MIX_MEDIA)
        self.assertEqual(self.engine.mix_mode(), MIX_MEDIA)
        self.expect(media=True, voice=False)

        self.engine.set_mix_mode(MIX_VOICE)
        self.expect(media=False, voice=True)
        self.engine.set_mix_mode(MIX_BOTH)

    def test_unshare_app(self):
        app = self.engine.find_apps("firefox")[0]
        self.engine.share(app.index, False, "test_fone")
        self.expect(media=False, voice=True)
        self.expect(media=True, voice=None, source="test_fone.monitor")  # ainda toca no fone

    def test_share_all_mode_excludes_call_apps(self):
        self.cfg.set("share_mode", SHARE_ALL)
        self.cfg.set("excluded_apps", ["firefox", "fakevoice"])  # finge que o Firefox é a call
        self.engine.apply_rules(self.cfg)
        time.sleep(0.5)
        self.assertFalse(self.engine.find_apps("firefox")[0].shared)
        self.cfg.set("excluded_apps", ["fakevoice"])
        self.engine.apply_rules(self.cfg)
        time.sleep(0.5)
        self.assertTrue(self.engine.find_apps("firefox")[0].shared)

    def test_change_mic_keeps_mode(self):
        self.engine.set_mix_mode(MIX_MEDIA)
        self.engine.set_volume("voice", 50)
        self.engine.set_mic("test_mic2")
        self.assertEqual(self.engine.current_devices()["mic"], "test_mic2")
        self.assertEqual(self.engine.mix_mode(), MIX_MEDIA)
        self.assertEqual(self.engine.volume("voice"), 50)
        # Monitores não são microfones: cai para o padrão do sistema.
        self.engine.set_mic("test_micsrc.monitor")
        self.assertEqual(self.engine.current_devices()["mic"], "test_mic")

    def test_stop_restores_app(self):
        self.engine.stop()
        self.assertFalse(self.engine.is_active())
        self.expect(media=True, voice=None, source="test_fone.monitor")  # o app voltou sozinho


if __name__ == "__main__":
    unittest.main()
