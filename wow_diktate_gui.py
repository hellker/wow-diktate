#!/usr/bin/env python3
"""wow-diktate GUI — PyQt6 tray icon + settings window."""
import argparse
import copy
import sys
import threading

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSlider, QSpinBox,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from wow_diktate import (
    get_model, load_config, normalize_config, parse_channel, record,
    save_config, transcribe, type_and_send,
)

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]


class DictationWorker(QThread):
    # Not called "finished": that would shadow QThread's own finished signal.
    status = pyqtSignal(str)
    result = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config, dry_run):
        super().__init__()
        self.config = config
        self.dry_run = dry_run
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self.status.emit("loading model...")
            get_model(self.config["model"])
            self.status.emit("listening...")
            audio = record(self.config, self._stop)
            text = transcribe(audio, self.config) if audio is not None else ""
            prefix, msg = parse_channel(
                text, self.config["channels"], self.config["default_channel"])
            if not msg.strip() or self._stop.is_set():
                self.result.emit("")
                return
            full = prefix + msg
            if not self.dry_run:
                type_and_send(full)
            self.result.emit(full)
        except Exception as e:
            # Never let an exception escape a QThread/slot: PyQt aborts the app.
            self.error.emit(str(e))


class SettingsWindow(QMainWindow):
    def __init__(self, config, on_save):
        super().__init__()
        self.config = copy.deepcopy(config)  # edit a copy until Save
        self.on_save = on_save
        self.setWindowTitle("WoW Diktate Settings")
        self.resize(420, 520)
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
        # Default is 2 decimals, which would silently round 0.005 to 0.01.
        self.threshold_spin.setDecimals(3)
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
        self.model_combo.addItems(MODEL_SIZES)
        if self.config["model"] not in MODEL_SIZES:
            # Keep a hand-edited model instead of silently switching to "tiny".
            self.model_combo.addItem(self.config["model"])
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
        rem_btn = QPushButton("−")
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
            if w and p:
                channels[w.text()] = p.text()
        self.config["channels"] = channels
        normalize_config(self.config)

        try:
            save_config(self.config)
        except OSError as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return
        self.on_save(self.config)
        self.close()

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
    def __init__(self, argv, dry_run=False):
        self.config = load_config()
        self.app = QApplication(argv)
        self.app.setQuitOnLastWindowClosed(False)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, "WoW Diktate",
                                 "No system tray found. This GUI needs a tray "
                                 "(on GNOME, install the AppIndicator extension).")
            sys.exit(1)

        icon = self._make_icon()
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("WoW Diktate")

        self.toggle_action = QAction("Dictate", self.app)
        self.toggle_action.triggered.connect(self._toggle)
        self.dry_run_action = QAction("Test mode (don't type)", self.app)
        self.dry_run_action.setCheckable(True)
        self.dry_run_action.setChecked(dry_run)
        self.settings_action = QAction("Settings", self.app)
        self.settings_action.triggered.connect(self._open_settings)
        self.quit_action = QAction("Quit", self.app)
        self.quit_action.triggered.connect(self._quit)

        # Keep a reference: the tray does not take ownership of the menu.
        self.menu = QMenu()
        self.menu.addAction(self.toggle_action)
        self.menu.addAction(self.dry_run_action)
        self.menu.addSeparator()
        self.menu.addAction(self.settings_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)

        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.worker = None
        self.settings_win = None

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

    def _busy(self):
        return self.worker is not None and self.worker.isRunning()

    def _toggle(self):
        if self._busy():
            # Ask the worker to stop; it reports back through result/error.
            self.worker.stop()
            self.tray.setToolTip("WoW Diktate (stopping...)")
            return
        self.worker = DictationWorker(copy.deepcopy(self.config),
                                      self.dry_run_action.isChecked())
        self.worker.status.connect(
            lambda s: self.tray.setToolTip(f"WoW Diktate ({s})"))
        self.worker.result.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_result(self, text):
        self.tray.setToolTip("WoW Diktate")
        if not text:
            self.tray.showMessage("WoW Diktate", "No speech detected",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)
        elif self.worker.dry_run:
            self.tray.showMessage("WoW Diktate (test mode)", f"Would send: {text}",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        else:
            self.tray.showMessage("WoW Diktate", f"Sent: {text}",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)

    def _on_error(self, err):
        print(f"wow-diktate error: {err}", file=sys.stderr)
        self.tray.setToolTip("WoW Diktate")
        self.tray.showMessage("WoW Diktate Error", err[:200],
                              QSystemTrayIcon.MessageIcon.Critical, 5000)

    def _open_settings(self):
        if self.settings_win is None or not self.settings_win.isVisible():
            self.settings_win = SettingsWindow(self.config, self._config_saved)
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()

    def _config_saved(self, cfg):
        self.config = cfg

    def _quit(self):
        if self._busy():
            self.worker.stop()
            self.worker.wait(5000)
        self.app.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="start in test mode: show the message instead of typing it")
    args, qt_args = parser.parse_known_args()
    app = WowDiktateApp([sys.argv[0], *qt_args], dry_run=args.dry_run)
    return app.app.exec()


if __name__ == "__main__":
    sys.exit(main())
