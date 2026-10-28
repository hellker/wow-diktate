#!/usr/bin/env python3
"""wow-diktate GUI — PyQt6 tray icon + settings window."""
import json
import os
import subprocess
import sys
import time

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton,
    QSlider, QSpinBox, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QMenu,
)

CONFIG_PATH = os.path.expanduser("~/.config/wow-diktate/config.json")
DEFAULTS = {
    "gain": 3.0,
    "silence_threshold": 0.005,
    "silence_chunks": 15,
    "min_duration": 1.5,
    "min_amplitude": 0.05,
    "model": "small",
    "default_channel": "/s ",
    "channels": {
        "say": "/s ",
        "party": "/p ",
        "raid": "/ra ",
        "guild": "/g ",
        "officer": "/o ",
        "yell": "/y ",
        "instance": "/i ",
        "whisper": "/w ",
        "reply": "/r ",
    },
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return {**DEFAULTS, **json.load(f)}
    return dict(DEFAULTS)


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


class DictationWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            from faster_whisper import WhisperModel
            import sounddevice as sd
            import numpy as np

            model_size = self.config["model"]
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

            frames = []
            started = False
            silence_count = 0
            threshold = self.config["silence_threshold"]
            silence_chunks = self.config["silence_chunks"]

            with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                                blocksize=1600) as stream:
                for _ in range(100):
                    data, _ = stream.read(1600)
                    level = np.abs(data).mean()
                    frames.append(data)
                    if not started:
                        if level > threshold:
                            started = True
                    else:
                        if level < threshold:
                            silence_count += 1
                            if silence_count >= silence_chunks:
                                break
                        else:
                            silence_count = 0

            if not started:
                self.finished.emit("")
                return

            start_idx = 0
            for i, f in enumerate(frames):
                if np.abs(f).mean() > threshold:
                    start_idx = max(0, i - 2)
                    break

            audio = np.concatenate(frames[start_idx:])
            audio = audio.flatten()
            gain = self.config["gain"]
            audio = np.clip(audio * gain, -1.0, 1.0)

            duration = len(audio) / 16000
            amplitude = np.abs(audio).max()
            if duration < self.config["min_duration"] or amplitude < self.config["min_amplitude"]:
                self.finished.emit("")
                return

            segments, _ = model.transcribe(audio, language="en",
                initial_prompt="World of Warcraft chat. Channels: say, party, raid, guild, officer, yell, instance, whisper. Short phrases: gg, brb, afk, pull, ready, on cooldown.")
            text = " ".join(s.text.strip() for s in segments)

            channels = self.config["channels"]
            default_ch = self.config["default_channel"]
            words = text.split(None, 1)
            if words and words[0].lower().rstrip(".,:") in channels:
                prefix = channels[words[0].lower().rstrip(".,:")]
                msg = words[1] if len(words) > 1 else ""
            else:
                prefix = default_ch
                msg = text

            full = prefix + msg
            self.finished.emit(full)

        except Exception as e:
            self.error.emit(str(e))


class SettingsWindow(QMainWindow):
    def __init__(self, config, on_save):
        super().__init__()
        self.config = config
        self.on_save = on_save
        self.setWindowTitle("WoW Diktate Settings")
        self.setFixedSize(420, 520)
        self._build_ui()

    def _build_ui(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        # --- Audio section ---
        layout.addWidget(QLabel("<b>Audio</b>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Gain (mic boost):"))
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(10, 100)
        self.gain_slider.setValue(int(self.config["gain"] * 10))
        self.gain_label = QLabel(f"{self.config['gain']:.1f}x")
        self.gain_slider.valueChanged.connect(
            lambda v: self.gain_label.setText(f"{v/10:.1f}x"))
        row.addWidget(self.gain_slider)
        row.addWidget(self.gain_label)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Silence threshold:"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.001, 0.1)
        self.threshold_spin.setSingleStep(0.001)
        self.threshold_spin.setValue(self.config["silence_threshold"])
        row.addWidget(self.threshold_spin)
        row.addStretch()
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Pause before stop (chunks):"))
        self.chunks_spin = QSpinBox()
        self.chunks_spin.setRange(5, 50)
        self.chunks_spin.setValue(self.config["silence_chunks"])
        row.addWidget(self.chunks_spin)
        row.addStretch()
        layout.addLayout(row)

        # --- Model section ---
        layout.addWidget(QLabel("<b>Model</b>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Size:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.model_combo.setCurrentText(self.config["model"])
        row.addWidget(self.model_combo)
        row.addStretch()
        layout.addLayout(row)

        # --- Channel section ---
        layout.addWidget(QLabel("<b>Channels</b>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Default:"))
        self.default_ch_edit = QLineEdit(self.config["default_channel"])
        row.addWidget(self.default_ch_edit)
        row.addStretch()
        layout.addLayout(row)

        self.channel_table = QTableWidget(0, 2)
        self.channel_table.itemChanged.connect(self._on_cell_changed)
        self.channel_table.setHorizontalHeaderLabels(["Word", "Prefix"])
        self.channel_table.horizontalHeader().setStretchLastSection(True)
        self.channel_table.setColumnWidth(0, 100)
        self.channel_table.verticalHeader().setVisible(False)
        self.channel_table.setAlternatingRowColors(True)
        for word, prefix in self.config["channels"].items():
            r = self.channel_table.rowCount()
            self.channel_table.insertRow(r)
            self.channel_table.setItem(r, 0, QTableWidgetItem(word))
            self.channel_table.setItem(r, 1, QTableWidgetItem(prefix))
        layout.addWidget(self.channel_table)

        btn_row2 = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.clicked.connect(self._add_channel)
        rem_btn = QPushButton("\u2212")
        rem_btn.setFixedWidth(36)
        rem_btn.clicked.connect(self._remove_channel)
        btn_row2.addWidget(add_btn)
        btn_row2.addWidget(rem_btn)
        btn_row2.addStretch()
        layout.addLayout(btn_row2)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self.setCentralWidget(w)

    def _save(self):
        self.config["gain"] = self.gain_slider.value() / 10
        self.config["silence_threshold"] = self.threshold_spin.value()
        self.config["silence_chunks"] = self.chunks_spin.value()
        self.config["model"] = self.model_combo.currentText()
        self.config["default_channel"] = self.default_ch_edit.text()

        channels = {}
        for r in range(self.channel_table.rowCount()):
            w = self.channel_table.item(r, 0)
            p = self.channel_table.item(r, 1)
            if w and p and w.text().strip():
                channels[w.text().strip()] = p.text().strip() + " "
        self.config["channels"] = channels

        try:
            save_config(self.config)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return
        self.on_save(self.config)
        self.close()

    def _on_cell_changed(self, item):
        pass

    def _add_channel(self):
        r = self.channel_table.rowCount()
        self.channel_table.insertRow(r)
        self.channel_table.setItem(r, 0, QTableWidgetItem("word"))
        self.channel_table.setItem(r, 1, QTableWidgetItem("/x"))
        self.channel_table.editItem(self.channel_table.item(r, 0))

    def _remove_channel(self):
        row = self.channel_table.currentRow()
        if row >= 0:
            self.channel_table.removeRow(row)


class WowDiktateApp:
    def __init__(self):
        self.config = load_config()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        icon = self._make_icon()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("WoW Diktate")

        self.toggle_action = QAction("Dictate", self.app)
        self.toggle_action.triggered.connect(self._toggle)
        self.settings_action = QAction("Settings", self.app)
        self.settings_action.triggered.connect(self._open_settings)
        self.quit_action = QAction("Quit", self.app)
        self.quit_action.triggered.connect(self._quit)

        menu = QMenu()
        self.tray.setContextMenu(menu)
        menu.addAction(self.toggle_action)
        menu.addSeparator()
        menu.addAction(self.settings_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)

        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.worker = None
        self.listening = False

    def _make_icon(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 0, 0, 0))
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#8B4513"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(22, 8, 20, 32, 10, 10)
        p.drawRoundedRect(16, 36, 32, 8, 4, 4)
        p.setPen(QColor("#8B4513"))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(32, 44, 32, 56)
        p.drawLine(20, 56, 44, 56)
        p.end()
        return QIcon(pixmap)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle()

    def _toggle(self):
        if self.listening:
            if self.worker and self.worker.isRunning():
                self.worker.wait(5000)
            self.listening = False
            self.tray.setToolTip("WoW Diktate (stopped)")
        else:
            self.listening = True
            self.tray.setToolTip("WoW Diktate (listening...)")
            self.worker = DictationWorker(self.config)
            self.worker.finished.connect(self._on_result)
            self.worker.error.connect(self._on_error)
            self.worker.start()

    def _on_result(self, text):
        self.listening = False
        self.tray.setToolTip("WoW Diktate")
        if text:
            subprocess.run(["ydotool", "key", "enter"])
            time.sleep(0.1)
            subprocess.run(["ydotool", "type", "--", text])
            time.sleep(0.1)
            subprocess.run(["ydotool", "key", "enter"])
            self.tray.showMessage("WoW Diktate", f"Sent: {text}",
                                   QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            self.tray.showMessage("WoW Diktate", "No speech detected",
                                   QSystemTrayIcon.MessageIcon.Information, 2000)

    def _on_error(self, err):
        self.listening = False
        self.tray.setToolTip("WoW Diktate")
        self.tray.showMessage("WoW Diktate Error", err[:200],
                               QSystemTrayIcon.MessageIcon.Critical, 5000)

    def _open_settings(self):
        win = SettingsWindow(self.config, self._config_saved)
        win.show()

    def _config_saved(self, cfg):
        self.config = cfg

    def _quit(self):
        if self.worker and self.worker.isRunning():
            self.worker.wait(5000)
        self.app.quit()


if __name__ == "__main__":
    app = WowDiktateApp()
    sys.exit(app.app.exec())
