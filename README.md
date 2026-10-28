# WoW Diktate

Decktation-style voice dictation for World of Warcraft on Linux.

Say **"party pull boss"** → types `/p pull boss` and auto-sends it in chat.

## How it works
1. You press a hotkey (or click the tray icon) → it starts listening
2. You speak a phrase (optionally starting with a channel name)
3. Whisper transcribes it locally
4. The channel prefix is parsed and prepended
5. `ydotool` presses Enter, types the message and presses Enter again

## Supported channels
| Say | Sends |
|-----|-------|
| "say" | `/s ` |
| "party" | `/p ` |
| "raid" | `/ra ` |
| "guild" | `/g ` |
| "officer" | `/o ` |
| "yell" | `/y ` |
| "instance" | `/i ` |
| "whisper" | `/w ` |
| "reply" | `/r ` |
| "one" | `/1 ` |
| "two" | `/2 ` |

No channel word → defaults to `/s `.

## Requirements
- Linux (PipeWire or PulseAudio)
- Python 3.10+
- `ydotool` (keystroke injection). Version 1.0+ needs the `ydotoold` daemon running.
- PortAudio (microphone access)
- A **US keyboard layout**: ydotool types US key positions, so on other layouts
  (e.g. ABNT2) the `/` can come out as a different character.

## Install

## AppImage (Recommended)

Get the latest form [Releases](https://github.com/hellker/wow-diktate/releases).

### Arch
```bash
sudo pacman -S ydotool portaudio python
```
### Debian / Ubuntu
```bash
sudo apt install ydotool libportaudio2 python3 python3-venv
```
### Fedora
```bash
sudo dnf install portaudio python3
git clone https://github.com/ReimuNotMoe/ydotool && cd ydotool && make && sudo make install
```

### Start ydotoold
`ydotoold` needs access to `/dev/uinput` (usually root). Run it so your user can use its socket:
```bash
sudo ydotoold --socket-path="$XDG_RUNTIME_DIR/.ydotool_socket" --socket-own="$(id -u):$(id -g)" &
```

### Setup
```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Usage

### Tray app
```bash
venv/bin/python3 wow_diktate_gui.py
```
Left-click the tray icon to dictate (click again to cancel). Right-click for
Settings and **Test mode** (shows the message instead of typing it).

### Toggle script + hotkey
`toggle.sh` runs one dictation; pressing the hotkey again cancels it. Output goes
to `$XDG_RUNTIME_DIR/wow_diktate.log`.
```bash
chmod +x toggle.sh
./toggle.sh
```

KDE Plasma shortcut:
1. System Settings → Keyboard → Shortcuts → Commands → "+ Add New" → Command or Script
2. Command: /path/to/toggle.sh
3. Shortcut: pick a key (e.g. F9)

## Testing without WoW
The game is not needed — `ydotool` types into whatever window has focus.

- **Mic + Whisper only** (no typing, no ydotool):
  `venv/bin/python3 wow_diktate.py --dry-run` and say "party pull boss" → prints `/p pull boss`
- **Without a mic**: `venv/bin/python3 wow_diktate.py --dry-run --file phrase.wav`
- **Tray app**: `venv/bin/python3 wow_diktate_gui.py --dry-run`
- **Real typing**: open a text editor (e.g. Kate), start dictation with the hotkey, keep the
  editor focused and speak. You should get an empty line (the "open chat" Enter),
  `/p pull boss`, and a new line (the "send" Enter).
- **Unit tests**: `venv/bin/python3 -m unittest discover tests`

## Configuration
Both the tray app and the CLI read `~/.config/wow-diktate/config.json`. The
Settings window edits it; you can also edit it by hand:

- `gain` — script-only mic boost (doesn't affect system volume)
- `silence_threshold` — sensitivity for speech detection
- `silence_chunks` — how long (× 0.1 s) to wait after a pause before stopping
- `min_duration` / `min_amplitude` — filters to reject noise/hallucinations
- `model` — Whisper model size (`tiny` … `large-v3`; bigger = better but slower)
- `default_channel`, `channels` — channel words and their prefixes

## Credits
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech recognition
- [Whisper](https://github.com/openai/whisper) — model architecture
- [ydotool](https://github.com/ReimuNotMoe/ydotool) — input injection
