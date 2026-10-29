"""Tests for the parts of wow_diktate that don't need a mic, a model or the game.

Run with:  venv/bin/python -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

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

    def test_regional_language_is_normalized(self):
        self.write(json.dumps({"language": " PT-BR "}))
        self.assertEqual(wd.load_config(self.path)["language"], "pt")
        self.assertEqual(wd.normalize_language("en_US"), "en")

    def test_invalid_language_keeps_other_settings(self):
        self.write(json.dumps({"language": "Portuguese", "gain": 5.0}))
        with self.assertLogs("wow_diktate", "WARNING"):
            cfg = wd.load_config(self.path)
        self.assertEqual(cfg["language"], "en")
        self.assertEqual(cfg["gain"], 5.0)

    def test_invalid_language_is_rejected(self):
        for value in ("", "invalid", None, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wd.normalize_language(value)


class GetModelTest(unittest.TestCase):
    def setUp(self):
        wd.get_model.cache_clear()
        self.addCleanup(wd.get_model.cache_clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model_path = self.tmp.name
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
            with open(os.path.join(self.model_path, name), "w") as output:
                output.write("{}")

    def load(self, whisper_model, download):
        with mock.patch("faster_whisper.WhisperModel", whisper_model), \
                mock.patch("faster_whisper.utils.download_model", download):
            return wd.get_model("small")

    def test_cached_model_loads_without_network(self):
        whisper_model = mock.Mock(return_value="model")
        download = mock.Mock(return_value=self.model_path)
        self.assertEqual(self.load(whisper_model, download), "model")
        download.assert_called_once_with("small", local_files_only=True)
        whisper_model.assert_called_once_with(
            self.model_path, device="cpu", compute_type="int8", local_files_only=True)

    def test_downloads_when_not_cached(self):
        whisper_model = mock.Mock(return_value="model")
        download = mock.Mock(side_effect=[FileNotFoundError("not cached"), self.model_path])
        self.assertEqual(self.load(whisper_model, download), "model")
        self.assertEqual(download.call_args_list,
                         [mock.call("small", local_files_only=True), mock.call("small")])

    def test_partial_cache_is_completed_before_constructing_model(self):
        missing = os.path.join(self.model_path, "model.bin")
        os.unlink(missing)

        def download(size, **kwargs):
            if not kwargs.get("local_files_only"):
                with open(missing, "w") as output:
                    output.write("model")
            return self.model_path

        fetch = mock.Mock(side_effect=download)
        model = mock.Mock(return_value="model")
        self.assertEqual(self.load(model, fetch), "model")
        self.assertEqual(fetch.call_count, 2)
        model.assert_called_once()

    def test_local_incomplete_model_reports_error_without_download(self):
        os.unlink(os.path.join(self.model_path, "tokenizer.json"))
        with mock.patch("faster_whisper.utils.download_model") as download:
            with self.assertRaisesRegex(RuntimeError, "Incomplete Whisper model"):
                wd.get_model(self.model_path)
        download.assert_not_called()


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
        self.assertEqual(self.transcribe("pt"), ("Party, vamos puxar o boss", "pt"))

    def test_transcription_does_not_request_translation(self):
        model = FakeModel("hello")
        with mock.patch.object(wd, "get_model", return_value=model):
            wd.transcribe("audio", wd.DEFAULTS)
        self.assertEqual(model.kwargs["task"], "transcribe")

    def test_auto_lets_whisper_detect_the_language(self):
        self.assertEqual(self.transcribe("auto")[1], None)

    def test_hallucinations_still_rejected(self):
        self.assertEqual(self.transcribe("en", "Thanks for watching!")[0], "")


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class RecordTest(unittest.TestCase):
    def record(self, silence_chunks, voiced=5, first=0):
        class InputStream:
            def __init__(self, **kwargs):
                self.index = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, count):
                level = 0.1 if first <= self.index < first + voiced else 0
                self.index += 1
                return np.full((count, 1), level, dtype=np.float32), False

        with mock.patch.dict(sys.modules, {"sounddevice": mock.Mock(InputStream=InputStream)}):
            return wd.record(dict(wd.DEFAULTS, silence_chunks=silence_chunks))

    def test_short_phrase_survives_short_pause_setting(self):
        for chunks in (5, 15):
            with self.subTest(chunks=chunks):
                self.assertIsNotNone(self.record(chunks))

    def test_silence_is_still_rejected(self):
        self.assertIsNone(self.record(5, voiced=0))

    def test_phrase_cut_off_by_hard_limit_is_still_rejected(self):
        self.assertIsNone(self.record(5, voiced=1, first=99))


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
            ["type", "--file", "-"],
            ["key", "28:1", "28:0"],
        ])
        self.assertEqual(run.call_args_list[2].kwargs["input"], "/p pull boss")

    def test_long_message_is_inserted_completely_before_final_enter(self):
        text = "/p " + "x" * 250
        with mock.patch.object(wd, "_run_ydotool", return_value=(0, YDOTOOL_1_HELP)) as run:
            self.assertTrue(wd.type_and_send(text))
        typed = [c.kwargs["input_text"] for c in run.call_args_list if c.args[0] == "type"]
        self.assertEqual("".join(typed), text)
        self.assertTrue(all(len(chunk) <= 16 for chunk in typed))
        self.assertEqual(run.call_args.args, ("key", "28:1", "28:0"))

    def test_legacy_ydotool_keeps_symbolic_keys_and_argv_typing(self):
        with mock.patch.object(wd, "_run_ydotool", return_value=(0, YDOTOOL_0_HELP)) as run:
            self.assertTrue(wd.type_and_send("/p hello"))
        self.assertEqual([c.args for c in run.call_args_list], [
            ("key", "--help"), ("key", "enter"),
            ("type", "--", "/p hello"), ("key", "enter")])

    def test_ascii_backslashes_are_literal_stdin(self):
        text = r"/p use C:\new"
        with mock.patch.object(subprocess, "run", return_value=completed(YDOTOOL_1_HELP)) as run:
            wd.type_and_send(text)
        typed = [call for call in run.call_args_list if call.args[0][1] == "type"]
        self.assertEqual(typed[0].args[0], ["ydotool", "type", "--file", "-"])
        self.assertEqual(typed[0].kwargs["input"], text)

    def test_cancel_during_type_prevents_remaining_text_and_send(self):
        stop = threading.Event()

        def run(*args, **kwargs):
            if args[0] == "type":
                stop.set()
            return 0, YDOTOOL_1_HELP

        with mock.patch.object(wd, "_run_ydotool", side_effect=run) as command:
            self.assertFalse(wd.type_and_send("/p " + "x" * 40, stop))
        self.assertEqual([c.args[0] for c in command.call_args_list], ["key", "key", "type"])

    def test_pre_cancelled_message_emits_no_keys(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.object(wd, "_run_ydotool") as run:
            self.assertFalse(wd.type_and_send("/p hello", stop))
        run.assert_not_called()

    def test_unicode_uses_clipboard_and_balanced_paste_keys(self):
        with mock.patch.object(wd, "_copy_text") as copy, \
                mock.patch.object(wd, "_run_ydotool", return_value=(0, YDOTOOL_1_HELP)) as run:
            self.assertTrue(wd.type_and_send("/p não ação"))
        copy.assert_called_once_with("/p não ação")
        sent = [c.args for c in run.call_args_list]
        self.assertNotIn("type", [args[0] for args in sent])
        self.assertIn(("key", "29:1", "47:1", "47:0", "29:0"), sent)
        self.assertEqual(sent[-1], ("key", "28:1", "28:0"))

    def test_missing_clipboard_helper_does_not_open_chat(self):
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
                mock.patch.object(wd.shutil, "which", return_value=None), \
                mock.patch.object(wd, "_run_ydotool") as run:
            with self.assertRaisesRegex(RuntimeError, "wl-copy"):
                wd.type_and_send("/p não")
        run.assert_not_called()

    def test_timeout_is_reported_without_traceback_or_final_enter(self):
        with mock.patch.object(wd, "_enter_key", return_value=("28:1", "28:0")), \
                mock.patch.object(subprocess, "run", side_effect=[
                    completed(), subprocess.TimeoutExpired("ydotool", 10)]) as run:
            with self.assertRaisesRegex(RuntimeError, "message was not sent"):
                wd.type_and_send("/p hello")
        self.assertEqual(run.call_count, 2)


class ClipboardTest(unittest.TestCase):
    def test_wayland_preserves_utf8_and_sets_mime_type(self):
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
                mock.patch.object(wd.shutil, "which", return_value="/bin/wl-copy"), \
                mock.patch.object(subprocess, "run", return_value=completed()) as run:
            wd._copy_text("não ação")
        self.assertEqual(run.call_args.args[0], ["wl-copy", "--type", "text/plain;charset=utf-8"])
        self.assertEqual(run.call_args.kwargs["input"], "não ação")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_x11_requests_utf8_clipboard_target(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ":99"}, clear=True), \
                mock.patch.object(wd.shutil, "which", return_value="/bin/xclip"), \
                mock.patch.object(subprocess, "run", return_value=completed()) as run:
            wd._copy_text("não ação")
        self.assertEqual(run.call_args.args[0],
                         ["xclip", "-selection", "clipboard", "-target", "UTF8_STRING", "-in"])


if __name__ == "__main__":
    unittest.main()
