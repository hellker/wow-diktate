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
import string
import subprocess
import sys
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
    "language": "en",             # spoken language ("pt", "es", ...) or "auto";
                                  # Whisper translates speech that doesn't match it
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
    cfg["default_channel"] = _prefix(cfg["default_channel"])
    cfg["channels"] = {_clean(word): _prefix(prefix)
                       for word, prefix in cfg["channels"].items() if _clean(word)}
    return cfg


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
    start = time.monotonic()
    try:
        # Use the downloaded copy without asking HuggingFace for updates first,
        # which costs a network round-trip (or a long timeout when offline).
        model = WhisperModel(size, device="cpu", compute_type="int8", local_files_only=True)
    except FileNotFoundError:
        log.info("downloading model %s (first run only)...", size)
        model = WhisperModel(size, device="cpu", compute_type="int8")
    log.info("model %s loaded in %.1fs", size, time.monotonic() - start)
    return model


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
    if duration < cfg["min_duration"]:
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
    language = None if cfg["language"] == "auto" else cfg["language"]
    start = time.monotonic()
    segments, info = get_model(cfg["model"]).transcribe(
        audio, language=language, initial_prompt=INITIAL_PROMPT)
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

def _run_ydotool(*args):
    try:
        res = subprocess.run(["ydotool", *args], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise RuntimeError("ydotool is not installed (see README)") from None
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


def type_and_send(text):
    """Open the chat (Enter), type the message and send it (Enter)."""
    enter = ("key", *_enter_key())
    for args in (enter, ("type", "--", text), enter):
        start = time.monotonic()
        code, output = _run_ydotool(*args)
        log.info("ydotool %s took %.2fs", args[0], time.monotonic() - start)
        if code != 0:
            raise RuntimeError(f"ydotool {args[0]} failed ({code}): {output}")
        time.sleep(0.1)  # give the game time to open the chat box


# --- CLI --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chat message instead of typing it (no ydotool needed)")
    parser.add_argument("--file", metavar="AUDIO",
                        help="transcribe an audio file instead of the microphone")
    parser.add_argument("--model", help="Whisper model size (overrides config)")
    parser.add_argument("--language", help='spoken language, e.g. "pt", or "auto" (overrides config)')
    args = parser.parse_args()

    logging.basicConfig(format="  (%(message)s)")
    log.setLevel(logging.INFO)  # our progress messages, not faster-whisper's
    cfg = load_config()
    if args.model:
        cfg["model"] = args.model
    if args.language:
        cfg["language"] = args.language

    get_model(cfg["model"])  # load before listening so no speech is lost
    if args.file:
        text = transcribe(args.file, cfg)
    else:
        print("Listening... (Ctrl+C to quit)")
        audio = record(cfg)
        text = transcribe(audio, cfg) if audio is not None else ""

    prefix, msg = parse_channel(text, cfg["channels"], cfg["default_channel"])
    if not msg.strip():
        print("  (nothing to send)")
        return 0
    full = prefix + msg
    print(f"  → {full}")
    if not args.dry_run:
        try:
            type_and_send(full)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
