"""Headless regressions for settings validation and worker cancellation."""
import copy
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import wow_diktate as wd
import wow_diktate_gui as gui


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved = mock.Mock()
        self.window = gui.SettingsWindow(wd.DEFAULTS, self.saved)
        self.addCleanup(self.window.close)

    def test_invalid_language_is_not_saved_or_applied(self):
        before = copy.deepcopy(self.window.config)
        self.window.language_combo.setCurrentText("Portuguese")
        with mock.patch.object(gui, "save_config") as save, \
                mock.patch.object(gui.QMessageBox, "warning") as warning:
            self.window._save()
        save.assert_not_called()
        self.saved.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(self.window.config, before)

    def test_regional_language_saves_as_whisper_code(self):
        self.window.language_combo.setCurrentText("pt-BR")
        with mock.patch.object(gui, "save_config") as save:
            self.window._save()
        self.assertEqual(save.call_args.args[0]["language"], "pt")
        self.saved.assert_called_once()


class WorkerTest(unittest.TestCase):
    def test_cancel_during_send_never_reports_success(self):
        worker = gui.DictationWorker(copy.deepcopy(wd.DEFAULTS), False)
        results = []
        worker.result.connect(results.append)

        def send(text, stop):
            stop.set()
            return False

        with mock.patch.object(gui, "get_model"), \
                mock.patch.object(gui, "record", return_value="audio"), \
                mock.patch.object(gui, "transcribe", return_value="party hello"), \
                mock.patch.object(gui, "type_and_send", side_effect=send):
            worker.run()
        self.assertEqual(results, [""])

    def test_cancel_during_model_load_does_not_open_microphone(self):
        worker = gui.DictationWorker(copy.deepcopy(wd.DEFAULTS), False)
        with mock.patch.object(gui, "get_model", side_effect=lambda size: worker.stop()), \
                mock.patch.object(gui, "record") as record:
            worker.run()
        record.assert_not_called()
