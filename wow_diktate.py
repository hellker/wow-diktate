#!/usr/bin/env python3
"""wow-diktate — Decktation-style voice dictation for World of Warcraft on Linux.

This file is both the command-line tool and the shared core used by the tray
GUI (wow_diktate_gui.py): config, recording, transcription, channel parsing
and typing all live here so the two front-ends behave the same.
"""
import argparse
import copy
import functools
import json
import logging
import os
import shutil
import string
import subprocess
import sys
import threading
import time

import numpy as np

log = logging.getLogger("wow_diktate")

SAMPLE_RATE = 16000
CHUNK = 1600            # samples per read = 0.1 s
MAX_CHUNKS = 100        # hard cap on one recording: 10 s
PRE_ROLL_CHUNKS = 2     # keep 0.2 s of audio from before speech was detected

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "wow-diktate", "config.json")

DEFAULTS = {
    "gain": 3.0,                  # script-only mic boost
    "silence_threshold": 0.005,   # mean level that counts as speech
    "silence_chunks": 15,         # chunks of silence (x 0.1 s) that end a phrase
    "min_duration": 1.5,          # seconds; shorter recordings are dropped
    "min_amplitude": 0.05,        # peak level; quieter recordings are dropped
    "model": "small",
    "language": "en",             # spoken language ("pt", "es", ...) or "auto"
    "default_channel": "/s ",
    "channels": {
        "say": "/s ", "party": "/p ", "raid": "/ra ",
        "guild": "/g ", "officer": "/o ", "yell": "/y ",
        "instance": "/i ", "whisper": "/w ", "reply": "/r ",
        "one": "/1 ", "two": "/2 ",
    },
}

INITIAL_PROMPT = (
    "World of Warcraft chat. Channels: say, party, raid, guild, officer, yell, "
    "instance, whisper. Short phrases: gg, brb, afk, pull, ready, on cooldown.")

# Phrases Whisper tends to invent from silence/noise (YouTube training data).
HALLUCINATIONS = {
    "thanks for watching", "thanks for watching and subscribing",
    "subscribe to my channel", "thank you for watching",
    "thanks for listening", "i hope you enjoyed this video",
    "don't forget to like and subscribe", "see you in the next one",
}


# --- Config -----------------------------------------------------------------

def _clean(text):
    """Lowercase and drop surrounding whitespace/punctuation: 'Party,' -> 'party'."""
    return text.strip().strip(string.punctuation + string.whitespace).lower()


def _prefix(prefix):
    """Chat prefixes always end in exactly one space ('/p' -> '/p ')."""
    prefix = prefix.strip()
    return prefix + " " if prefix else ""


def normalize_config(cfg):
    if "language" in cfg:
        cfg["language"] = normalize_language(cfg["language"])
    cfg["default_channel"] = _prefix(cfg["default_channel"])
    cfg["channels"] = {_clean(word): _prefix(prefix)
                       for word, prefix in cfg["channels"].items() if _clean(word)}
    return cfg


def normalize_language(value):
    """Accept Whisper codes and regional variants such as pt-BR / en_US."""
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    if isinstance(value, str):
        language = value.strip().lower().replace("_", "-").split("-", 1)[0]
        if language == "auto" or language in _LANGUAGE_CODES:
            return language
    raise ValueError('Invalid spoken language. Use a code such as "pt", "en", or "auto".')


def load_config(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(path) as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("expected a JSON object")
        cfg.update(saved)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable config %s: %s", path, e)
    try:
        return normalize_config(cfg)
    except ValueError as e:
        log.warning("%s Using the default language %s.", e, DEFAULTS["language"])
        cfg["language"] = DEFAULTS["language"]
    return normalize_config(cfg)


def save_config(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write to a temp file first so a crash never leaves a half-written config.
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


# --- Speech -----------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def get_model(size):
    """Load the Whisper model once and reuse it (loading takes seconds)."""
    from faster_whisper import WhisperModel  # heavy import, only when needed
    from faster_whisper.utils import download_model

    start = time.monotonic()
    if os.path.isdir(size):
        model_path = size
    else:
        try:
            model_path = download_model(size, local_files_only=True)
        except FileNotFoundError:
            model_path = None
        # An existing snapshot directory can still be missing files after an
        # interrupted download. Resolve/download before constructing CTranslate2.
        if model_path is None or not _model_complete(model_path):
            log.info("downloading missing files for model %s...", size)
            model_path = download_model(size)
    if not _model_complete(model_path):
        raise RuntimeError(f"Incomplete Whisper model in {model_path}; missing model files")
    model = WhisperModel(model_path, device="cpu", compute_type="int8", local_files_only=True)
    log.info("model %s loaded in %.1fs", size, time.monotonic() - start)
    return model


def _model_complete(path):
    required = ("model.bin", "config.json", "tokenizer.json")
    return (all(os.path.isfile(os.path.join(path, name)) for name in required)
            and any(os.path.isfile(os.path.join(path, name))
                    for name in ("vocabulary.json", "vocabulary.txt")))


def record(cfg, stop_event=None):
    """Record from the default mic until a pause after speech.

    Returns float32 16 kHz mono audio with gain applied, or None when nothing
    usable was heard or stop_event (a threading.Event) was set.
    """
    import sounddevice as sd

    threshold = cfg["silence_threshold"]
    frames = []
    first_voiced = None
    silence_count = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK) as stream:
        for i in range(MAX_CHUNKS):
            if stop_event is not None and stop_event.is_set():
                log.info("cancelled")
                return None
            data, _ = stream.read(CHUNK)
            frames.append(data)
            level = np.abs(data).mean()
            if first_voiced is None:
                if level > threshold:
                    first_voiced = i
                    log.info("speech detected, recording...")
            elif level < threshold:
                silence_count += 1
                if silence_count >= cfg["silence_chunks"]:
                    log.info("pause detected, stopping")
                    break
            else:
                silence_count = 0

    if first_voiced is None:
        log.info("no speech detected")
        return None

    audio = np.concatenate(frames[max(0, first_voiced - PRE_ROLL_CHUNKS):]).flatten()
    audio = np.clip(audio * cfg["gain"], -1.0, 1.0)

    duration = len(audio) / SAMPLE_RATE
    # A short configured pause must not make every short phrase fail a minimum
    # that includes the silence tail. Keep the existing default behavior.
    minimum = min(cfg["min_duration"], (cfg["silence_chunks"] + 1) * CHUNK / SAMPLE_RATE)
    if duration < minimum:
        log.info("too short: %.1fs, skipping", duration)
        return None
    amplitude = np.abs(audio).max()
    if amplitude < cfg["min_amplitude"]:
        log.info("too quiet: %.4f, skipping", amplitude)
        return None
    return audio


def is_hallucination(text):
    return _clean(text) in HALLUCINATIONS


def transcribe(audio, cfg):
    """Transcribe audio (numpy array or file path); '' if it was a hallucination."""
    language = normalize_language(cfg["language"])
    language = None if language == "auto" else language
    start = time.monotonic()
    segments, info = get_model(cfg["model"]).transcribe(
        audio, language=language, task="transcribe", initial_prompt=INITIAL_PROMPT)
    text = " ".join(s.text.strip() for s in segments).strip()
    log.info("transcribed in %.1fs (language: %s)", time.monotonic() - start, info.language)
    if is_hallucination(text):
        log.info('hallucination rejected: "%s"', text)
        return ""
    return text


def parse_channel(text, channels, default_channel):
    """Split a leading channel word off: 'Party, pull boss' -> ('/p ', 'pull boss')."""
    words = text.split(None, 1)
    if words and _clean(words[0]) in channels:
        return channels[_clean(words[0])], words[1] if len(words) > 1 else ""
    return default_channel, text


# --- Typing -----------------------------------------------------------------

def _run_ydotool(*args, timeout=10, input_text=None):
    try:
        res = subprocess.run(["ydotool", *args], input=input_text,
                             capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("ydotool is not installed (see README)") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ydotool {args[0]} timed out after {timeout:.1f}s; message was not sent") from None
    output = (res.stdout + res.stderr).strip()
    if "failed to connect socket" in output:
        raise RuntimeError("ydotoold is not running or its socket is not accessible:\n" + output)
    return res.returncode, output


@functools.lru_cache(maxsize=1)
def _enter_key():
    """ydotool arguments that press Enter.

    ydotool >= 1.0 (Arch, Fedora, Debian 13, ...) only takes raw keycodes and
    silently ignores names like "enter"; old 0.1.x builds only take names.
    """
    _, help_text = _run_ydotool("key", "--help")
    keys = ("28:1", "28:0") if "keycode" in help_text.lower() else ("enter",)  # 28 = KEY_ENTER
    log.info("ydotool Enter: key %s (help starts with %r)",
             " ".join(keys), help_text.splitlines()[0] if help_text else "")
    return keys


def _copy_text(text):
    """Clipboard owners stay alive after these helpers return."""
    if os.environ.get("WAYLAND_DISPLAY"):
        command = ["wl-copy", "--type", "text/plain;charset=utf-8"]
    elif os.environ.get("DISPLAY"):
        command = ["xclip", "-selection", "clipboard", "-target", "UTF8_STRING", "-in"]
    else:
        raise RuntimeError("Unicode input needs a Wayland or X11 clipboard session")
    if not shutil.which(command[0]):
        raise RuntimeError(f"Unicode input needs {command[0]}; install it (see README)")
    try:
        # Do not capture helper pipes: a forked selection owner may retain them.
        result = subprocess.run(command, input=text, text=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Could not copy Unicode text: {e}") from None
    if result.returncode:
        raise RuntimeError(f"{command[0]} could not copy Unicode text")


def type_and_send(text, stop_event=None):
    """Open chat, insert and send; False on cancellation, leaving chat unsent."""
    stopped = stop_event.is_set if stop_event is not None else lambda: False
    if stopped():
        return False
    # Prepare Unicode before opening chat, so missing helpers cannot leave a
    # half-open chat or pass UTF-8 bytes to ydotool's ASCII keycode table.
    unicode_text = not text.isascii()
    if unicode_text:
        _copy_text(text)
    enter = ("key", *_enter_key())
    modern = enter[1:] == ("28:1", "28:0")
    if unicode_text:
        paste = (("key", "29:1", "47:1", "47:0", "29:0") if modern
                 else ("key", "ctrl+v"))
        insert = [(paste, None)]
    else:
        # Bound cancellation latency without killing a key-down process. Every
        # short invocation gets to release its keys before we check cancellation.
        # stdin is literal in both ydotool 1.0.1 and 1.0.4; argv escaping and
        # the -e option differ between those versions.
        chunks = [text[i:i + 16] for i in range(0, len(text), 16)]
        insert = [(("type", "--file", "-"), chunk) if modern
                  else (("type", "--", chunk), None) for chunk in chunks]
    commands = ((enter, None), *insert, (enter, None))
    for index, (args, input_text) in enumerate(commands):
        if stopped():
            return False
        start = time.monotonic()
        timeout = max(10, 2 + len(input_text or args[-1]) * 0.1) if args[0] == "type" else 10
        code, output = _run_ydotool(*args, timeout=timeout, input_text=input_text)
        log.info("ydotool %s took %.2fs", args[0], time.monotonic() - start)
        if code != 0:
            raise RuntimeError(f"ydotool {args[0]} failed ({code}): {output}")
        if index == len(commands) - 1:
            return True  # the final Enter has committed the send
        if stop_event is not None:
            if stop_event.wait(0.1):
                return False
        else:
            time.sleep(0.1)  # give the game time to open the chat box / paste
    return True


# --- CLI --------------------------------------------------------------------

def main(argv=None, stop_event=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chat message instead of typing it (no ydotool needed)")
    parser.add_argument("--file", metavar="AUDIO",
                        help="transcribe an audio file instead of the microphone")
    parser.add_argument("--model", help="Whisper model size (overrides config)")
    parser.add_argument("--language", type=normalize_language,
                        help='spoken language, e.g. "pt", or "auto" (overrides config)')
    args = parser.parse_args(argv)
    stop_event = stop_event if stop_event is not None else threading.Event()

    logging.basicConfig(format="  (%(message)s)")
    log.setLevel(logging.INFO)  # our progress messages, not faster-whisper's
    cfg = load_config()
    if args.model:
        cfg["model"] = args.model
    if args.language:
        cfg["language"] = args.language

    get_model(cfg["model"])  # load before listening so no speech is lost
    if stop_event.is_set():
        return 0
    if args.file:
        text = transcribe(args.file, cfg)
    else:
        print("Listening... (Ctrl+C to quit)")
        audio = record(cfg, stop_event)
        text = transcribe(audio, cfg) if audio is not None else ""

    if stop_event.is_set():
        return 0

    prefix, msg = parse_channel(text, cfg["channels"], cfg["default_channel"])
    if not msg.strip():
        print("  (nothing to send)")
        return 0
    full = prefix + msg
    print(f"  → {full}")
    if not args.dry_run:
        try:
            type_and_send(full, stop_event)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
