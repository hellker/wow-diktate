#!/usr/bin/env python3
"""wow-diktate — Decktation-style voice dictation for World of Warcraft on Linux."""
import subprocess, time
from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np

CHANNELS = {
    "say": "/s ", "party": "/p ", "raid": "/ra ",
    "guild": "/g ", "officer": "/o ", "yell": "/y ",
    "instance": "/i ", "whisper": "/w ", "reply": "/r ",
    "one": "/1 ", "two": "/2 ",
}

DEFAULT_CHANNEL = "/s "
GAIN = 3.0
MIN_DURATION = 1.5
MIN_AMPLITUDE = 0.05

HALLUCINATIONS = {
    "thanks for watching", "thanks for watching and subscribing",
    "subscribe to my channel", "thank you for watching",
    "thanks for listening", "i hope you enjoyed this video",
    "don't forget to like and subscribe", "see you in the next one",
}

model = WhisperModel("small", device="cpu", compute_type="int8")

def record_and_transcribe():
    frames = []
    started = False
    silence_count = 0
    SILENCE_THRESHOLD = 0.005
    SILENCE_CHUNKS = 15

    with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                        blocksize=1600) as stream:
        for _ in range(100):
            data, _ = stream.read(1600)
            level = np.abs(data).mean()
            frames.append(data)
            if not started:
                if level > SILENCE_THRESHOLD:
                    started = True
                    print("  (speech detected, recording...)")
            else:
                if level < SILENCE_THRESHOLD:
                    silence_count += 1
                    if silence_count >= SILENCE_CHUNKS:
                        print("  (pause detected, stopping)")
                        break
                else:
                    silence_count = 0

    if not started:
        return ""

    start_idx = 0
    for i, f in enumerate(frames):
        if np.abs(f).mean() > SILENCE_THRESHOLD:
            start_idx = max(0, i - 2)
            break

    audio = np.concatenate(frames[start_idx:])
    audio = audio.flatten()
    audio = np.clip(audio * GAIN, -1.0, 1.0)

    duration = len(audio) / 16000
    amplitude = np.abs(audio).max()
    if duration < MIN_DURATION:
        print(f"  (too short: {duration:.1f}s, skipping)")
        return ""
    if amplitude < MIN_AMPLITUDE:
        print(f"  (too quiet: {amplitude:.4f}, skipping)")
        return ""

    segments, _ = model.transcribe(audio, language="en",
        initial_prompt="World of Warcraft chat. Channels: say, party, raid, guild, officer, yell, instance, whisper. Short phrases: gg, brb, afk, pull, ready, on cooldown.")
    text = " ".join(s.text.strip() for s in segments)

    if text.lower() in HALLUCINATIONS:
        print(f"  (hallucination rejected: \"{text}\")")
        return ""

    return text

def parse_channel(text):
    words = text.split(None, 1)
    if words and words[0].lower().rstrip(".,:") in CHANNELS:
        return CHANNELS[words[0].lower().rstrip(".,:")], words[1] if len(words) > 1 else ""
    return DEFAULT_CHANNEL, text

def type_and_send(text):
    subprocess.run(["ydotool", "key", "enter"])
    time.sleep(0.1)
    subprocess.run(["ydotool", "type", "--", text])
    time.sleep(0.1)
    subprocess.run(["ydotool", "key", "enter"])

def main():
    print("Listening... (Ctrl+C to quit)")
    text = record_and_transcribe()
    if text:
        prefix, msg = parse_channel(text)
        full = prefix + msg
        print(f"  → {full}")
        type_and_send(full)

if __name__ == "__main__":
    main()
