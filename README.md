`python3 -c "
`content = '''# WoW Diktate
`
`Decktation-style voice dictation for World of Warcraft on Linux.
`
`Say **\"party pull boss\"** → types \`/p pull boss\` and auto-sends it in chat.
`
`## How it works
`1. You press a hotkey → script starts listening
`2. You speak a phrase (optionally starting with a channel name)
`3. Whisper transcribes it locally
`4. The channel prefix is parsed and prepended
`5. \`ydotool\` types the message and presses Enter
`
`## Supported channels
`| Say | Sends |
`|-----|-------|
`| \"say\" | \`/s \` |
`| \"party\" | \`/p \` |
`| \"raid\" | \`/ra \` |
`| \"guild\" | \`/g \` |
`| \"officer\" | \`/o \` |
`| \"yell\" | \`/y \` |
`| \"instance\" | \`/i \` |
`| \"whisper\" | \`/w \` |
`| \"reply\" | \`/r \` |
`
`No channel word → defaults to \`/s \`.
`
`## Requirements
`- Linux (PipeWire or PulseAudio)
`- Python 3.10+
`- \`ydotool\` (keystroke injection)
`- PortAudio (microphone access)
`
`## Install
`
`### Arch
`
`\`\`\`bash
`sudo pacman -S ydotool python
`\`\`\`
`
`### Debian / Ubuntu
`
`\`\`\`bash
`sudo apt install ydotool libportaudio2 python3
`\`\`\`
`
`### Fedora
`
`\`\`\`bash
`sudo dnf install portaudio python3
`git clone https://github.com/ReimuNotMoe/ydotool && cd ydotool && make && sudo make install
`sudo systemctl enable --now ydotool
`\`\`\`
`
`### Setup
`
`\`\`\`bash
`python3 -m venv venv
`venv/bin/pip install -r requirements.txt
`\`\`\`
`
`## Usage
`
`### Manual
`
`\`\`\`bash
`venv/bin/python3 wow_diktate.py
`\`\`\`
`
`### Toggle script (recommended)
`
`\`\`\`bash
`chmod +x toggle.sh
`./toggle.sh
`\`\`\`
`
`### KDE Plasma shortcut
`1. System Settings → Keyboard → Shortcuts → Commands → \"+ Add New\" → Command or Script
`2. Command: \`/path/to/toggle.sh\`
`3. Shortcut: pick a key (e.g. F9)
`
`## Configuration
`Edit the constants at the top of \`wow_diktate.py\`:
`- \`GAIN\` — script-only mic boost (doesn't affect system volume)
`- \`SILENCE_THRESHOLD\` — sensitivity for speech detection
`- \`SILENCE_CHUNKS\` — how long to wait after a pause before stopping
`- \`MIN_DURATION\` / \`MIN_AMPLITUDE\` — filters to reject noise/hallucinations
`- \`CHANNELS\` — add/remove channel words
`
`## Credits
`- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech recognition
`- [Whisper](https://github.com/openai/whisper) — model architecture
`- [ydotool](https://github.com/ReimuNotMoe/ydotool) — input injection
`
`## License
`AGPL-3.0
`'''
`open('/home/hellker/wow-diktate/README.md', 'w').write(content)
`print('Done')
`"   
