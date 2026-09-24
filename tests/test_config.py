"""Testes da configuração (não precisam de servidor de áudio)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio_mic.config import Config  # noqa: E402
from audio_mic.core import SHARE_ALL, SHARE_APPS, parse_module_args  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sub", "config.json")

    def test_defaults_and_persistence(self):
        cfg = Config(self.path)
        self.assertEqual(cfg.get("share_mode"), SHARE_APPS)
        self.assertIn("chrome", cfg.get("excluded_apps"))
        cfg.set("mix_mode", "media")
        self.assertEqual(Config(self.path).get("mix_mode"), "media")

    def test_rules_per_mode(self):
        cfg = Config(self.path)
        self.assertFalse(cfg.wants_shared("firefox"))
        cfg.remember_app("firefox", True)
        self.assertTrue(cfg.wants_shared("firefox"))
        cfg.set("share_mode", SHARE_ALL)
        self.assertTrue(cfg.wants_shared("vlc"))
        self.assertFalse(cfg.wants_shared("chrome"))
        cfg.remember_app("vlc", False)
        self.assertFalse(cfg.wants_shared("vlc"))
        cfg.remember_app("chrome", True)
        self.assertTrue(cfg.wants_shared("chrome"))

    def test_two_processes_do_not_erase_each_other(self):
        gui, cli = Config(self.path), Config(self.path)
        cli.remember_app("firefox", True)      # e.g. `audio-mic share firefox`
        gui.set("mix_mode", "media")           # GUI saves an unrelated change
        gui.set("voice_volume", 70, save=False)
        gui.remember_app("vlc", True)          # must keep firefox and the pending volume
        fresh = Config(self.path)
        self.assertEqual(fresh.get("shared_apps"), ["firefox", "vlc"])
        self.assertEqual(fresh.get("mix_mode"), "media")
        self.assertEqual(fresh.get("voice_volume"), 70)

    def test_corrupt_file_falls_back_to_defaults(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(Config(self.path).get("mix_mode"), "both")


class ModuleArgsTest(unittest.TestCase):
    def test_parse_quoted(self):
        args = parse_module_args(
            "source=a.monitor sink=b sink_input_properties='media.name=\"X Y\" k=v'")
        self.assertEqual(args["source"], "a.monitor")
        self.assertEqual(args["sink"], "b")
        self.assertEqual(args["sink_input_properties"], 'media.name="X Y" k=v')


if __name__ == "__main__":
    unittest.main()
