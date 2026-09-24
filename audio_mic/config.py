"""Configuração persistente em ~/.config/audio-mic/config.json (sem sudo)."""

import json
import os

from .core import MIX_BOTH, SHARE_APPS, SHARE_ALL

# Apps que costumam ser a própria call: no modo "tudo" eles NÃO são compartilhados,
# senão os outros participantes ouviriam a própria voz de volta (eco).
DEFAULT_CALL_APPS = [
    "chrome", "chromium", "msedge", "brave", "opera", "vivaldi",
    "zoom", "teams", "teams-for-linux", "discord", "slack", "skype", "gather",
]

DEFAULTS = {
    "mic": None,            # nome do microfone real (None = padrão do sistema)
    "output": None,         # nome da saída/fone (None = padrão do sistema)
    "monitor": True,        # ouvir no fone o que está sendo compartilhado
    "keep_on_close": False,  # manter o microfone virtual ligado ao fechar a janela
    "mix_mode": MIX_BOTH,   # both | media | voice
    "voice_volume": 100,
    "media_volume": 100,
    "share_mode": SHARE_APPS,  # apps | all
    "shared_apps": [],      # modo "apps": apps enviados para a call
    "excluded_apps": list(DEFAULT_CALL_APPS),  # modo "all": apps que NÃO vão para a call
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "audio-mic", "config.json")


class Config(object):
    def __init__(self, path=None):
        self.path = path or config_path()
        self.data = dict(DEFAULTS)
        self.data["shared_apps"] = list(DEFAULTS["shared_apps"])
        self.data["excluded_apps"] = list(DEFAULTS["excluded_apps"])
        self._dirty = set()
        self.load()

    def load(self):
        try:
            with open(self.path) as fh:
                stored = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and key not in self._dirty:
                    self.data[key] = stored[key]

    def save(self):
        """Write our changes, merged over what is on disk.

        The GUI, the CLI and `watch` may run at the same time: re-reading first means one
        process never erases a choice another one just saved.
        """
        mine = {k: self.data[k] for k in self._dirty}
        self.load()
        self.data.update(mine)
        self._dirty.clear()
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value, save=True):
        self.data[key] = value
        self._dirty.add(key)
        if save:
            self.save()

    def wants_shared(self, key):
        """Should this app go to the call, according to the current share mode?"""
        if self.get("share_mode") == SHARE_ALL:
            return key not in self.get("excluded_apps")
        return key in self.get("shared_apps")

    def remember_app(self, key, shared, save=True):
        """Remember the user's choice for an app in the current share mode."""
        if not key:
            return
        self.load()  # pick up choices saved by other processes before editing the list
        if self.get("share_mode") == SHARE_ALL:
            lst, include = list(self.get("excluded_apps")), not shared
        else:
            lst, include = list(self.get("shared_apps")), shared
        if include and key not in lst:
            lst.append(key)
        elif not include and key in lst:
            lst.remove(key)
        self.set("excluded_apps" if self.get("share_mode") == SHARE_ALL else "shared_apps", lst, save)
