# WoW Diktate

Decktation-style voice dictation for World of Warcraft on Linux.

Say **"party pull boss"** → types `/p pull boss` and auto-sends it in chat.

## How it works
1. You press a hotkey → script starts listening
2. You speak a phrase (optionally starting with a channel name)
3. Whisper transcribes it locally
4. The channel prefix is parsed and prepended
5. `ydotool` types the message and presses Enter

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

No channel word → defaults to `/s `.

## Requirements
- Linux (PipeWire or PulseAudio)
- Python 3.10+
- `ydotool` (keystroke injection)
- PortAudio (microphone access)

## Install

### Arch / CachyOS
```bash
sudo pacman -S ydotool python
