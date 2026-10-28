# WoW Diktate

Decktation-style voice dictation for World of Warcraft on Linux.

Say **"party pull boss"** → types `/p pull boss` and auto-sends it in chat.

## How it works
1. You press a hotkey (or click the tray icon) → it starts listening
2. You speak a phrase (optionally starting with a channel name)
3. Whisper transcribes it locally
4. The channel prefix is parsed and prepended
5. `ydotool` presses Enter, inserts the message and presses Enter again

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
- For accented / non-ASCII text: `wl-copy` (the `wl-clipboard` package) on
  Wayland, or `xclip` on X11. These messages are copied to the clipboard and
  pasted with Ctrl+V; the clipboard keeps the dictated message afterward.
- A **US keyboard layout**: ydotool types US key positions, so on other layouts
  (e.g. ABNT2) the `/` can come out as a different character.

## Install

## AppImage (Recommended)

Get the latest from [Releases](https://github.com/hellker/wow-diktate/releases).
Source changes require a newly built AppImage; existing release files do not update themselves.

### Arch
```bash
sudo pacman -S ydotool portaudio python
# For Unicode input: sudo pacman -S wl-clipboard   # Wayland
#                or sudo pacman -S xclip          # X11
```
### Debian / Ubuntu
```bash
sudo apt install ydotool libportaudio2 python3 python3-venv
# For Unicode input: sudo apt install wl-clipboard   # Wayland
#                or sudo apt install xclip          # X11
```
### Fedora
```bash
sudo dnf install portaudio python3
# For Unicode input: sudo dnf install wl-clipboard   # Wayland
#                or sudo dnf install xclip          # X11
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
`toggle.sh` runs one dictation; pressing the hotkey again requests cancellation.
It communicates with that dictation through a private local socket rather than
signalling a saved PID. Output goes to `$XDG_RUNTIME_DIR/wow-diktate/dictation.log`
(or `/tmp/wow-diktate-<uid>/dictation.log` without `XDG_RUNTIME_DIR`).
```bash
chmod +x toggle.sh
./toggle.sh
```

KDE Plasma shortcut:
1. System Settings → Keyboard → Shortcuts → Commands → "+ Add New" → Command or Script
2. Command: /path/to/toggle.sh
3. Shortcut: pick a key (e.g. F9)

Cancellation prevents the final send. Text already inserted may remain in the
open chat. During a model load or transcription, cancellation takes effect when
that operation returns; during ASCII typing it is checked between short chunks.

## Testing without WoW
The game is not needed — `ydotool` types into whatever window has focus.

- **Mic + Whisper only** (no typing, no ydotool):
  `venv/bin/python3 wow_diktate.py --dry-run` and say "party pull boss" → prints `/p pull boss`
- **Without a mic**: `venv/bin/python3 wow_diktate.py --dry-run --file phrase.wav`
- **Tray app**: `venv/bin/python3 wow_diktate_gui.py --dry-run`
- **Timings / ydotool details**: start the tray app (or the AppImage) from a terminal with
  `--verbose` to see how long each step takes and which Enter key ydotool uses.
- **Real typing**: open a text editor (e.g. Kate), start dictation with the hotkey, keep the
  editor focused and speak. You should get an empty line (the "open chat" Enter),
  `/p pull boss`, and a new line (the "send" Enter).
- **Unit tests**: `QT_QPA_PLATFORM=offscreen venv/bin/python3 -m unittest discover tests`

## Configuration
Both the tray app and the CLI read `~/.config/wow-diktate/config.json`. The
Settings window edits it; you can also edit it by hand:

- `gain` — script-only mic boost (doesn't affect system volume)
- `silence_threshold` — sensitivity for speech detection
- `silence_chunks` — how long (× 0.1 s) to wait after a pause before stopping
- `min_duration` / `min_amplitude` — filters to reject noise/hallucinations.
  The duration minimum adapts to a shorter configured pause so quick chat phrases
  are not discarded just because listening stops sooner.
- `model` — Whisper model size (`tiny` … `large-v3`; bigger = better but slower)
- `language` — the language you speak (`en`, `pt`, `es`, …, or `auto`). Use `pt`
  for Portuguese; regional forms such as `pt-BR` are also accepted. An incorrect
  forced language can produce incorrect or English output even with the
  transcription task selected. Invalid codes are rejected in Settings.
- `default_channel`, `channels` — channel words and their prefixes

## Credits
- [Decktation](https://github.com/silverfoxy/decktation) — inspiration for bringing voice dictation to all Linux distros (originally SteamOS/Steam Deck only)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech recognition
- [Whisper](https://github.com/openai/whisper) — model architecture
- [ydotool](https://github.com/ReimuNotMoe/ydotool) — input injection
