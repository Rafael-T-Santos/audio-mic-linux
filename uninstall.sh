#!/bin/sh
# Remove o AudioMic instalado pelo install.sh (sem sudo).
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/audio-mic"
if [ -x "$DEST/audio-mic" ]; then
    "$DEST/audio-mic" stop >/dev/null 2>&1 || true
fi
rm -rf "$DEST"
rm -f "$HOME/.local/bin/audio-mic" "$DATA/applications/audio-mic.desktop"
echo "AudioMic removido. (Suas preferências ficam em ~/.config/audio-mic; apague se quiser.)"
