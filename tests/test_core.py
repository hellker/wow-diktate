"""Tests for the parts of wow_diktate that don't need a mic, a model or the game.

Run with:  venv/bin/python -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wow_diktate as wd  # noqa: E402

YDOTOOL_1_HELP = "Usage: key [OPTION]... [KEYCODES]...\nSyntax: <keycode>:<pressed>\n"
YDOTOOL_0_HELP = "Usage: key [--delay <ms>] ... <key sequence> ...\n"


class ParseChannelTest(unittest.TestCase):
    channels = wd.DEFAULTS["channels"]

    def parse(self, text):
        return wd.parse_channel(text, self.channels, "/s ")

    def test_channel_word_with_punctuation(self):
        self.assertEqual(self.parse("Party, pull boss."), ("/p ", "pull boss."))
        self.assertEqual(self.parse("RAID! ready check"), ("/ra ", "ready check"))

    def test_no_channel_word_uses_default(self):
        self.assertEqual(self.parse("gg well played"), ("/s ", "gg well played"))

    def test_channel_word_only(self):
        self.assertEqual(self.parse("Guild."), ("/g ", ""))

    def test_empty(self):
        self.assertEqual(self.parse(""), ("/s ", ""))


class HallucinationTest(unittest.TestCase):
    def test_matches_despite_case_and_punctuation(self):
        self.assertTrue(wd.is_hallucination(" Thanks for watching!"))
        self.assertTrue(wd.is_hallucination("Thank you for watching."))

    def test_real_message_passes(self):
        self.assertFalse(wd.is_hallucination("thanks for the heal"))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            f.write(text)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(wd.load_config(self.path), wd.DEFAULTS)

    def test_invalid_json_gives_defaults(self):
        self.write("{not json")
        with self.assertLogs("wow_diktate", "WARNING"):
            self.assertEqual(wd.load_config(self.path), wd.DEFAULTS)

    def test_saved_values_override_defaults(self):
        self.write(json.dumps({"gain": 5.0, "channels": {"Trade": "/2"}}))
        cfg = wd.load_config(self.path)
        self.assertEqual(cfg["gain"], 5.0)
        self.assertEqual(cfg["model"], wd.DEFAULTS["model"])
        self.assertEqual(cfg["channels"], {"trade": "/2 "})

    def test_editing_loaded_config_does_not_touch_defaults(self):
        cfg = wd.load_config(self.path)
        cfg["channels"]["new"] = "/n "
        self.assertNotIn("new", wd.DEFAULTS["channels"])

    def test_save_load_roundtrip(self):
        cfg = wd.load_config(self.path)
        cfg["silence_threshold"] = 0.007
        wd.save_config(cfg, self.path)
        self.assertEqual(wd.load_config(self.path), cfg)

    def test_normalize(self):
        cfg = wd.normalize_config({"default_channel": "/s",
                                   "channels": {" Party ": "/p", "": "/x", "raid": "/ra  "}})
        self.assertEqual(cfg["default_channel"], "/s ")
        self.assertEqual(cfg["channels"], {"party": "/p ", "raid": "/ra "})


class GetModelTest(unittest.TestCase):
    def setUp(self):
        wd.get_model.cache_clear()
        self.addCleanup(wd.get_model.cache_clear)

    def load(self, whisper_model):
        fake_module = mock.Mock(WhisperModel=whisper_model)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            return wd.get_model("small")

    def test_cached_model_loads_without_network(self):
        whisper_model = mock.Mock(return_value="model")
        self.assertEqual(self.load(whisper_model), "model")
        whisper_model.assert_called_once_with(
            "small", device="cpu", compute_type="int8", local_files_only=True)

    def test_downloads_when_not_cached(self):
        whisper_model = mock.Mock(side_effect=[FileNotFoundError("not cached"), "model"])
        self.assertEqual(self.load(whisper_model), "model")
        self.assertNotIn("local_files_only", whisper_model.call_args.kwargs)


class FakeModel:
    def __init__(self, text):
        self.text = text
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        segment = mock.Mock(text=self.text)
        return iter([segment]), mock.Mock(language=kwargs["language"] or "pt")


class TranscribeTest(unittest.TestCase):
    def transcribe(self, language, text="Party, vamos puxar o boss"):
        model = FakeModel(text)
        cfg = dict(wd.DEFAULTS, language=language)
        with mock.patch.object(wd, "get_model", return_value=model):
            result = wd.transcribe("audio", cfg)
        return result, model.kwargs["language"]

    def test_default_language_is_english(self):
        self.assertEqual(wd.DEFAULTS["language"], "en")

    def test_configured_language_is_passed_to_whisper(self):
        # Forcing "en" on Portuguese speech makes Whisper translate it.
        self.assertEqual(self.transcribe("pt"), ("Party, vamos puxar o boss", "pt"))

    def test_auto_lets_whisper_detect_the_language(self):
        self.assertEqual(self.transcribe("auto")[1], None)

    def test_hallucinations_still_rejected(self):
        self.assertEqual(self.transcribe("en", "Thanks for watching!")[0], "")


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class YdotoolTest(unittest.TestCase):
    def setUp(self):
        wd._enter_key.cache_clear()
        self.addCleanup(wd._enter_key.cache_clear)
        sleep = mock.patch.object(wd.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_ydotool_1_uses_keycodes(self):
        with mock.patch.object(subprocess, "run", return_value=completed(YDOTOOL_1_HELP)):
            self.assertEqual(wd._enter_key(), ("28:1", "28:0"))

    def test_ydotool_0_uses_key_names(self):
        # 0.1.x prints help to stderr and exits non-zero; that must not matter.
        res = subprocess.CompletedProcess([], 255, stdout="", stderr=YDOTOOL_0_HELP)
        with mock.patch.object(subprocess, "run", return_value=res):
            self.assertEqual(wd._enter_key(), ("enter",))

    def test_daemon_not_running(self):
        res = completed("failed to connect socket `/run/user/1000/.ydotool_socket'", 2)
        with mock.patch.object(subprocess, "run", return_value=res):
            with self.assertRaisesRegex(RuntimeError, "ydotoold"):
                wd.type_and_send("/p hi")

    def test_not_installed(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                wd.type_and_send("/p hi")

    def test_type_and_send_sequence(self):
        with mock.patch.object(subprocess, "run",
                               return_value=completed(YDOTOOL_1_HELP)) as run:
            wd.type_and_send("/p pull boss")
        sent = [c.args[0][1:] for c in run.call_args_list]
        self.assertEqual(sent, [
            ["key", "--help"],
            ["key", "28:1", "28:0"],
            ["type", "--", "/p pull boss"],
            ["key", "28:1", "28:0"],
        ])


if __name__ == "__main__":
    unittest.main()
