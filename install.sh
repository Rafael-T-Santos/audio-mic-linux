#!/bin/sh
# Instala o AudioMic só para o seu usuário (sem sudo):
#   ~/.local/share/audio-mic   -> programa
#   ~/.local/bin/audio-mic     -> comando
#   menu de aplicativos        -> atalho "AudioMic"
set -e

SRC="$(dirname "$(readlink -f "$0")")"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/audio-mic"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
PY=/usr/bin/python3
[ -x "$PY" ] || PY=python3

echo "Verificando dependências..."
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERRO: python3 não encontrado." >&2
    exit 1
fi
if ! "$PY" -c "import ctypes, ctypes.util, sys; ctypes.CDLL(ctypes.util.find_library('pulse') or 'libpulse.so.0')" 2>/dev/null; then
    echo "ERRO: biblioteca libpulse não encontrada (pacote libpulse0)." >&2
    echo "Ela vem em toda instalação desktop do Ubuntu; se faltar, peça para instalar: sudo apt install libpulse0" >&2
    exit 1
fi
GUI_OK=1
if ! "$PY" -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null; then
    GUI_OK=0
    echo "AVISO: GTK 3 para Python não encontrado (python3-gi / gir1.2-gtk-3.0)."
    echo "       A linha de comando funciona; a janela precisa desses pacotes."
fi
if ! "$PY" -c "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst; Gst.init(None); assert Gst.ElementFactory.find('pulsesrc')" 2>/dev/null; then
    echo "Aviso: GStreamer/pulsesrc não encontrado: os medidores de volume ficam ocultos (o resto funciona)."
fi

echo "Instalando em $DEST ..."
mkdir -p "$DEST" "$BIN" "$APPS"
rm -rf "$DEST/audio_mic"
cp -r "$SRC/audio_mic" "$DEST/"
cp "$SRC/audio-mic" "$SRC/uninstall.sh" "$DEST/"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$DEST/"
find "$DEST" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$DEST/audio-mic" "$DEST/uninstall.sh"
ln -sf "$DEST/audio-mic" "$BIN/audio-mic"

cat > "$APPS/audio-mic.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=AudioMic
GenericName=Áudio do PC no microfone
Comment=Toque o áudio do YouTube, TikTok etc. no seu microfone durante calls
Exec=$DEST/audio-mic
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=microfone;mic;audio;meet;zoom;gather;call;youtube;
StartupNotify=true
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS" 2>/dev/null || true

echo
echo "Pronto!"
[ "$GUI_OK" = 1 ] && echo "  • Abra \"AudioMic\" no menu de aplicativos,"
echo "  • ou rode no terminal: audio-mic   (audio-mic --help para os comandos)"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "  (o comando 'audio-mic' funciona depois de sair e entrar de novo na sessão;"
       echo "   até lá use: $BIN/audio-mic)" ;;
esac
