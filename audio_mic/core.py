"""Motor do AudioMic: monta e controla o microfone virtual via protocolo PulseAudio.

Funciona tanto com PulseAudio quanto com PipeWire (pipewire-pulse), sem sudo e sem
precisar do `pactl`: falamos direto com o servidor de áudio pela libpulse (pulsectl).

Grafo criado:

    apps compartilhados -> [audiomic_share] --monitor--> loopback "media"   -> [audiomic_mix]
                                            \\-monitor--> loopback "monitor" -> fone
    microfone real ------------------------------------> loopback "voice"   -> [audiomic_mix]
    [audiomic_mix].monitor -> remap-source [audiomic_mic]  (o "Microfone Virtual" da call)

Todo o estado vive no próprio servidor de áudio (módulos carregados), então o motor
pode ser recriado a qualquer momento (GUI, CLI, depois de um crash) e "adota" o que
já existe.
"""

import shlex
import time

from ._vendor import pulsectl

SHARE_SINK = "audiomic_share"
MIX_SINK = "audiomic_mix"
VIRTUAL_MIC = "audiomic_mic"
OWN_NAMES = (SHARE_SINK, MIX_SINK, VIRTUAL_MIC)

SHARE_DESC = "AudioMic - Compartilhar (interno)"
MIX_DESC = "AudioMic - Mistura (interno)"
MIC_DESC = "Microfone Virtual (AudioMic)"

LATENCY_MS = 30

# Mix modes: what the call hears.
MIX_BOTH, MIX_MEDIA, MIX_VOICE = "both", "media", "voice"
MIX_MODES = (MIX_BOTH, MIX_MEDIA, MIX_VOICE)

# Share modes: which apps go to the call.
SHARE_APPS, SHARE_ALL = "apps", "all"
SHARE_MODES = (SHARE_APPS, SHARE_ALL)

ROLE_PROP = "audiomic.role"
PA_INVALID_INDEX = 0xFFFFFFFF


class AudioMicError(Exception):
    pass


def _q(value):
    """Quote a value for a module argument (key='value with spaces')."""
    return "'" + value.replace("'", "") + "'"


def _props(props):
    """Format a {key: value} dict as a quoted property list for a module argument."""
    parts = ['{}="{}"'.format(k, str(v).replace('"', "")) for k, v in props.items()]
    return _q(" ".join(parts))


def _stream_props(role):
    # A unique media.name (and no application.name/media.role) gives each loopback its
    # own key in the session manager's "restore stream" database; otherwise muting the
    # voice would be restored on the media loopback next time. We also ask it not to
    # restore anything: we set mute/volume ourselves from the config.
    return _props({
        "media.name": "AudioMic " + role,
        ROLE_PROP: role,
        "state.restore-props": "false",
        "state.restore-target": "false",
        "module-stream-restore.id": "audiomic-" + role,
    })


def parse_module_args(argument):
    """Parse 'a=b c="d e"' style module arguments into a dict (best effort)."""
    try:
        tokens = shlex.split(argument or "")
    except ValueError:
        tokens = (argument or "").split()
    args = {}
    for tok in tokens:
        if "=" in tok:
            key, value = tok.split("=", 1)
            args[key] = value
    return args


class App(object):
    """A playback stream (sink-input) that could be shared with the call."""

    def __init__(self, sink_input, shared):
        props = sink_input.proplist
        self.index = sink_input.index
        self.name = props.get("application.name") or props.get("node.name") or "Desconhecido"
        self.binary = props.get("application.process.binary") or ""
        self.title = props.get("media.name") or ""
        self.icon = props.get("application.icon_name") or ""
        self.corked = bool(sink_input.corked)
        self.sink = sink_input.sink
        self.shared = shared
        self.key = app_key(props)

    def __repr__(self):
        return "<App {} {!r} shared={}>".format(self.index, self.key, self.shared)


def app_key(props):
    """Stable identifier for an application, used to remember per-app choices."""
    key = props.get("application.process.binary") or props.get("application.name") or ""
    key = key.strip().lower()
    # Normalise common browser/app variants so a choice sticks between versions.
    aliases = {
        "google-chrome": "chrome", "google-chrome-stable": "chrome", "chromium-browser": "chromium",
        "firefox-bin": "firefox", "firefox-esr": "firefox", "zoom.real": "zoom",
    }
    return aliases.get(key, key)


class Engine(object):
    """Controls the virtual microphone graph. Not thread safe: use from one thread."""

    def __init__(self, client_name="audio-mic"):
        try:
            self.pulse = pulsectl.Pulse(client_name)
        except pulsectl.PulseError as err:
            raise AudioMicError(
                "Não consegui conectar ao servidor de áudio (PipeWire/PulseAudio): {}".format(err))

    def close(self):
        self.pulse.close()

    # ------------------------------------------------------------------ discovery

    def _modules(self):
        """Map role -> module for the modules we own."""
        found = {}
        for mod in self.pulse.module_list():
            args = parse_module_args(mod.argument)
            if mod.name == "module-null-sink":
                if args.get("sink_name") == SHARE_SINK:
                    found["share"] = mod
                elif args.get("sink_name") == MIX_SINK:
                    found["mix"] = mod
            elif mod.name == "module-remap-source" and args.get("source_name") == VIRTUAL_MIC:
                found["mic"] = mod
            elif mod.name == "module-loopback":
                src, sink = args.get("source", ""), args.get("sink", "")
                if src == VIRTUAL_MIC:
                    found["preview"] = mod
                elif src == SHARE_SINK + ".monitor" and sink == MIX_SINK:
                    found["media"] = mod
                elif src == SHARE_SINK + ".monitor":
                    found["monitor"] = mod
                elif sink == MIX_SINK:
                    found["voice"] = mod
        return found

    def is_active(self):
        mods = self._modules()
        return all(k in mods for k in ("share", "mix", "mic", "media"))

    def sinks(self):
        """Real output devices (excludes our own virtual ones)."""
        return [s for s in self.pulse.sink_list() if s.name not in OWN_NAMES]

    def sources(self):
        """Real input devices: no monitors and none of ours."""
        out = []
        for s in self.pulse.source_list():
            if s.name in OWN_NAMES or s.name.endswith(".monitor"):
                continue
            if s.monitor_of_sink is not None and s.monitor_of_sink != PA_INVALID_INDEX:
                continue
            out.append(s)
        return out

    def _default_sink_name(self):
        name = self.pulse.server_info().default_sink_name
        if name in OWN_NAMES:
            sinks = self.sinks()
            return sinks[0].name if sinks else None
        return name

    def _default_source_name(self):
        name = self.pulse.server_info().default_source_name
        if not name or name in OWN_NAMES or name.startswith("audiomic_") or name.endswith(".monitor"):
            sources = self.sources()
            return sources[0].name if sources else None
        return name

    def resolve_output(self, wanted=None):
        names = [s.name for s in self.sinks()]
        if wanted and wanted in names:
            return wanted
        return self._default_sink_name()

    def resolve_mic(self, wanted=None):
        names = [s.name for s in self.sources()]
        if wanted and wanted in names:
            return wanted
        return self._default_source_name()

    def _sink_index(self, name):
        try:
            return self.pulse.get_sink_by_name(name).index
        except pulsectl.PulseIndexError:
            return None

    def _loopback_stream(self, module):
        if module is None:
            return None
        for si in self.pulse.sink_input_list():
            if si.owner_module == module.index:
                return si
        return None

    # ------------------------------------------------------------------ lifecycle

    def start(self, mic=None, output=None, monitor=True):
        """Create (or complete) the virtual device graph. Idempotent."""
        mods = self._modules()
        load = self.pulse.module_load

        if "share" not in mods:
            load("module-null-sink", "sink_name={} sink_properties={}".format(
                SHARE_SINK, _props({"device.description": SHARE_DESC, ROLE_PROP: "share"})))
        if "mix" not in mods:
            load("module-null-sink", "sink_name={} sink_properties={}".format(
                MIX_SINK, _props({"device.description": MIX_DESC, ROLE_PROP: "mix"})))
        if "mic" not in mods:
            load("module-remap-source", "master={}.monitor source_name={} source_properties={}".format(
                MIX_SINK, VIRTUAL_MIC,
                _props({"device.description": MIC_DESC, "device.icon_name": "audio-input-microphone",
                        ROLE_PROP: "mic"})))
        if "media" not in mods:
            self._load_loopback("media", SHARE_SINK + ".monitor", MIX_SINK)

        self.set_mic(mic)
        self.set_output(output, monitor)

    def activate(self, cfg):
        """Start with everything configured in cfg (a Config) and route the apps."""
        self.start(cfg.get("mic"), cfg.get("output"), cfg.get("monitor"))
        self.apply_mix_settings(cfg)
        self.apply_rules(cfg)

    def apply_mix_settings(self, cfg, verify=True):
        """Apply mix mode and volumes from cfg.

        The session manager may still restore an old mute/volume on freshly created
        loopbacks a moment after they appear, so check again and re-apply if needed.
        """
        def apply():
            self.set_mix_mode(cfg.get("mix_mode"))
            self.set_volume("voice", cfg.get("voice_volume"))
            self.set_volume("media", cfg.get("media_volume"))

        def applied():
            return (self.mix_mode() == cfg.get("mix_mode")
                    and self.volume("voice") == cfg.get("voice_volume")
                    and self.volume("media") == cfg.get("media_volume"))

        apply()
        for _ in range(3 if verify else 0):
            time.sleep(0.15)
            if not applied():
                apply()

    def _load_loopback(self, role, source, sink):
        index = self.pulse.module_load("module-loopback", " ".join([
            "source={}".format(source),
            "sink={}".format(sink),
            "latency_msec={}".format(LATENCY_MS),
            "source_dont_move=true",
            "sink_dont_move=true",
            "sink_input_properties={}".format(_stream_props(role)),
            "source_output_properties={}".format(_stream_props(role)),
        ]))
        self._wait_stream(index)
        return index

    def _wait_stream(self, module_index, timeout=2.0):
        """PipeWire creates the loopback stream asynchronously (and may restore an old mute
        state on it). Wait until it exists so that mute/volume we set right after stick."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(si.owner_module == module_index for si in self.pulse.sink_input_list()):
                return True
            time.sleep(0.02)
        return False

    def set_mic(self, mic=None):
        """(Re)connect the real microphone to the mix. Keeps mute/volume."""
        mods = self._modules()
        mic_name = self.resolve_mic(mic)
        old = mods.get("voice")
        if old is not None and parse_module_args(old.argument).get("source") == mic_name:
            return mic_name
        state = self._stream_state(old)
        if old is not None:
            self.pulse.module_unload(old.index)
        if mic_name:
            self._load_loopback("voice", mic_name, MIX_SINK)
            self._restore_stream_state("voice", state)
        return mic_name

    def set_output(self, output=None, monitor=True):
        """(Re)connect the 'hear what is shared' loopback to the headphones."""
        mods = self._modules()
        out_name = self.resolve_output(output)
        preview = mods.get("preview")
        if preview is not None and parse_module_args(preview.argument).get("sink") != out_name:
            self.set_preview(True, output)
        old = mods.get("monitor")
        if old is not None:
            same = parse_module_args(old.argument).get("sink") == out_name
            if same and monitor:
                return out_name
            self.pulse.module_unload(old.index)
        if monitor and out_name:
            self._load_loopback("monitor", SHARE_SINK + ".monitor", out_name)
        return out_name

    def set_preview(self, enabled, output=None):
        """Play the virtual mic in the headphones, to hear exactly what the call hears."""
        mods = self._modules()
        old = mods.get("preview")
        if old is not None:
            self.pulse.module_unload(old.index)
        if enabled:
            if "mic" not in mods:
                raise AudioMicError("AudioMic não está ativo.")
            out_name = self.resolve_output(output)
            if out_name:
                self._load_loopback("preview", VIRTUAL_MIC, out_name)

    def preview_enabled(self):
        return "preview" in self._modules()

    def current_devices(self):
        mods = self._modules()
        voice, mon = mods.get("voice"), mods.get("monitor")
        return {
            "mic": parse_module_args(voice.argument).get("source") if voice else None,
            "output": parse_module_args(mon.argument).get("sink") if mon else None,
        }

    def stop(self):
        """Remove everything we created.

        Apps playing on our sink are rescued by the server to the default output. We don't
        move them ourselves: an explicit move would pin the app to that device forever.
        """
        mods = self._modules()
        for role in ("preview", "voice", "media", "monitor", "mic", "mix", "share"):
            mod = mods.get(role)
            if mod is not None:
                try:
                    self.pulse.module_unload(mod.index)
                except pulsectl.PulseError:
                    pass

    # ------------------------------------------------------------------ mix controls

    def _stream_state(self, module):
        si = self._loopback_stream(module)
        if si is None:
            return None
        return {"mute": bool(si.mute), "volume": si.volume.value_flat}

    def _restore_stream_state(self, role, state):
        if not state:
            return
        si = self._loopback_stream(self._modules().get(role))
        if si is None:
            return
        self.pulse.sink_input_mute(si.index, state["mute"])
        self.pulse.volume_set_all_chans(si, state["volume"])

    def set_mix_mode(self, mode):
        if mode not in MIX_MODES:
            raise AudioMicError("Modo inválido: {} (use {})".format(mode, ", ".join(MIX_MODES)))
        mods = self._modules()
        voice = self._loopback_stream(mods.get("voice"))
        media = self._loopback_stream(mods.get("media"))
        if voice is not None:
            self.pulse.sink_input_mute(voice.index, mode == MIX_MEDIA)
        if media is not None:
            self.pulse.sink_input_mute(media.index, mode == MIX_VOICE)

    def mix_mode(self):
        mods = self._modules()
        voice = self._loopback_stream(mods.get("voice"))
        media = self._loopback_stream(mods.get("media"))
        voice_on = voice is not None and not voice.mute
        media_on = media is not None and not media.mute
        if voice_on and not media_on:
            return MIX_VOICE
        if media_on and not voice_on:
            return MIX_MEDIA
        return MIX_BOTH

    def set_volume(self, role, percent):
        """Volume of 'voice' or 'media' inside the mix (0-150 %)."""
        if role not in ("voice", "media", "monitor"):
            raise AudioMicError("Use 'voice', 'media' ou 'monitor'.")
        si = self._loopback_stream(self._modules().get(role))
        if si is None:
            raise AudioMicError("AudioMic não está ativo.")
        percent = max(0, min(150, int(percent)))
        self.pulse.volume_set_all_chans(si, percent / 100.0)

    def volume(self, role):
        si = self._loopback_stream(self._modules().get(role))
        return None if si is None else int(round(si.volume.value_flat * 100))

    # ------------------------------------------------------------------ apps

    def apps(self):
        share_index = self._sink_index(SHARE_SINK)
        module_names = {m.index: m.name for m in self.pulse.module_list()}
        owned = {m.index for m in self._modules().values()}
        apps = []
        for si in self.pulse.sink_input_list():
            props = si.proplist
            if si.owner_module in owned or props.get(ROLE_PROP):
                continue
            # Streams created by other modules (echo-cancel, combine-sink...) are plumbing.
            # On PulseAudio, normal apps are "owned" by the native protocol module.
            owner = module_names.get(si.owner_module)
            if owner and not owner.startswith("module-native-protocol"):
                continue
            # Our own level meters / pavucontrol peak streams.
            if props.get("application.id") == "org.PulseAudio.pavucontrol":
                continue
            apps.append(App(si, share_index is not None and si.sink == share_index))
        return apps

    def find_apps(self, what):
        """Find apps by stream index or by (partial) application name/binary."""
        apps = self.apps()
        if str(what).isdigit():
            return [a for a in apps if a.index == int(what)]
        what = str(what).lower()
        return [a for a in apps if what in a.key or what in a.name.lower()]

    def share(self, index, shared=True, output=None):
        """Send one stream to the call (shared=True) or only to the headphones."""
        if shared:
            target = self._sink_index(SHARE_SINK)
            if target is None:
                raise AudioMicError("AudioMic não está ativo.")
        else:
            target = self._sink_index(self.resolve_output(output) or "")
            if target is None:
                raise AudioMicError("Nenhuma saída de áudio encontrada.")
        self.pulse.sink_input_move(index, target)

    def apply_rules(self, cfg, only_indexes=None):
        """Put every app where the configuration says it belongs.

        only_indexes: restrict to these sink-input indexes (e.g. freshly created streams),
        so manual per-stream choices on older streams are preserved.
        Returns the list of apps that were moved.
        """
        if not self.is_active():
            return []
        moved = []
        for app in self.apps():
            if only_indexes is not None and app.index not in only_indexes:
                continue
            want = cfg.wants_shared(app.key)
            if want != app.shared:
                try:
                    self.share(app.index, want, cfg.get("output"))
                    moved.append(app)
                except pulsectl.PulseError:
                    pass  # stream vanished meanwhile
        return moved

    # ------------------------------------------------------------------ status

    def status(self):
        mods = self._modules()
        active = all(k in mods for k in ("share", "mix", "mic", "media"))
        info = {"active": active}
        if active:
            info.update(self.current_devices())
            info["mix_mode"] = self.mix_mode()
            info["voice_volume"] = self.volume("voice")
            info["media_volume"] = self.volume("media")
            info["monitor"] = "monitor" in mods
        return info


def event_listener(on_event, stop_flag, client_name="audio-mic-events"):
    """Blocking loop that calls on_event(kind) on server changes. Run in a thread.

    stop_flag: threading.Event; the loop returns shortly after it is set.
    Reconnects automatically if the audio server restarts.
    """
    import time

    while not stop_flag.is_set():
        try:
            with pulsectl.Pulse(client_name) as pulse:
                pulse.event_mask_set("sink_input", "sink", "source", "module", "server")

                def cb(ev):
                    on_event(ev)

                pulse.event_callback_set(cb)
                while not stop_flag.is_set():
                    pulse.event_listen(timeout=0.5)
        except pulsectl.PulseError:
            if stop_flag.wait(2):
                return
            on_event(None)  # reconnected / server restarted: refresh everything
        except Exception:  # never let the listener thread die silently
            time.sleep(1)
