"""Interface gráfica do AudioMic (GTK 3 — já vem instalado no Ubuntu, sem sudo)."""

import sys
import threading

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk, Gdk, Pango
except (ImportError, ValueError) as _err:  # pragma: no cover - depends on the desktop
    GI_ERROR = _err
else:
    GI_ERROR = None

from . import meter
from ._vendor import pulsectl
from .config import DEFAULT_CALL_APPS, Config
from .core import (
    MIC_DESC, MIX_BOTH, MIX_MEDIA, MIX_VOICE, SHARE_ALL, SHARE_APPS, SHARE_SINK, VIRTUAL_MIC,
    AudioMicError, Engine, event_listener)

APP_ID = "io.github.rafael_t_santos.AudioMic"

CSS = b"""
.am-card { padding: 14px 16px; border-radius: 10px; }
.am-title { font-weight: bold; font-size: 1.05em; }
.am-hint { opacity: 0.7; font-size: 0.92em; }
.am-status-on { color: #2e9d5b; font-weight: bold; }
.am-status-off { opacity: 0.75; font-weight: bold; }
.am-mic-name { font-family: monospace; font-weight: bold; padding: 2px 8px;
               border-radius: 6px; background-color: alpha(@theme_selected_bg_color, 0.18); }
.am-app-name { font-weight: bold; }
.am-chip { font-size: 0.8em; padding: 1px 7px; border-radius: 9px;
           background-color: alpha(@theme_fg_color, 0.10); }
.am-chip-live { background-color: alpha(#2e9d5b, 0.22); }
.am-warn { color: #c07000; font-size: 0.88em; }
levelbar block.filled.am-ok { background-color: #2e9d5b; border-color: #2e9d5b; }
levelbar block.filled.am-hot { background-color: #e0a030; border-color: #e0a030; }
levelbar trough { min-height: 6px; }
levelbar block { min-height: 6px; }
"""


def _label(text, *classes, xalign=0.0, wrap=False, markup=False):
    lbl = Gtk.Label()
    if markup:
        lbl.set_markup(text)
    else:
        lbl.set_text(text)
    lbl.set_xalign(xalign)
    if wrap:
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for cls in classes:
        lbl.get_style_context().add_class(cls)
    return lbl


def _card(title, hint=None):
    frame = Gtk.Frame()
    frame.get_style_context().add_class("view")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.get_style_context().add_class("am-card")
    frame.add(box)
    head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    head.pack_start(_label(title, "am-title"), False, False, 0)
    if hint:
        head.pack_start(_label(hint, "am-hint", wrap=True), False, False, 0)
    box.pack_start(head, False, False, 0)
    return frame, box


def _level_bar():
    bar = Gtk.LevelBar()
    bar.set_min_value(0.0)
    bar.set_max_value(1.0)
    for name in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH, "full"):
        try:
            bar.remove_offset_value(name)
        except Exception:
            pass
    bar.add_offset_value("am-ok", 0.9)
    bar.add_offset_value("am-hot", 1.0)
    bar.set_valign(Gtk.Align.CENTER)
    return bar


class AppRow(Gtk.ListBoxRow):
    """One application (may have several streams) that is playing audio."""

    def __init__(self, key, on_toggle):
        super().__init__()
        self.key = key
        self.set_activatable(False)
        self._on_toggle = on_toggle
        self._updating = False

        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
        self.icon = Gtk.Image()
        self.icon.set_pixel_size(32)
        box.pack_start(self.icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        top = Gtk.Box(spacing=8)
        self.name = _label("", "am-app-name")
        self.name.set_ellipsize(Pango.EllipsizeMode.END)
        top.pack_start(self.name, False, False, 0)
        self.chip = _label("", "am-chip")
        top.pack_start(self.chip, False, False, 0)
        text.pack_start(top, False, False, 0)
        self.title = _label("", "am-hint")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(self.title, False, False, 0)
        self.warn = _label("⚠ Se a call estiver neste app, vai dar eco para os outros.", "am-warn",
                           wrap=True)
        self.warn.set_no_show_all(True)
        text.pack_start(self.warn, False, False, 0)
        box.pack_start(text, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.switch = Gtk.Switch(halign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self._toggled)
        right.pack_start(self.switch, False, False, 0)
        right.pack_start(_label("na call", "am-hint", xalign=0.5), False, False, 0)
        box.pack_start(right, False, False, 0)
        self.add(box)

    def update(self, apps, engine_active):
        first = apps[0]
        self._updating = True
        self.name.set_text(first.name)
        titles = [a.title for a in apps if a.title and a.title.lower() not in (first.name.lower(),)]
        extra = " (+{})".format(len(apps) - 1) if len(apps) > 1 else ""
        self.title.set_text((titles[0] if titles else first.binary or "") + extra)
        self.title.set_tooltip_text("\n".join(titles) or None)
        shared = any(a.shared for a in apps)
        playing = any(not a.corked for a in apps)
        self.switch.set_active(shared)
        self.switch.set_sensitive(engine_active)
        chip_ctx = self.chip.get_style_context()
        if shared and engine_active:
            self.chip.set_text("indo para a call")
            chip_ctx.add_class("am-chip-live")
        else:
            self.chip.set_text("tocando" if playing else "pausado")
            chip_ctx.remove_class("am-chip-live")
        self.warn.set_visible(shared and engine_active and first.key in DEFAULT_CALL_APPS)
        self._set_icon(first)
        self._updating = False

    def _set_icon(self, app):
        theme = Gtk.IconTheme.get_default()
        for name in (app.icon, app.binary, app.key, "audio-x-generic"):
            if name and theme.has_icon(name):
                self.icon.set_from_icon_name(name, Gtk.IconSize.DND)
                return
        self.icon.set_from_icon_name("audio-x-generic", Gtk.IconSize.DND)

    def _toggled(self, switch, _pspec):
        if not self._updating:
            self._on_toggle(self.key, switch.get_active())


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, engine, cfg):
        super().__init__(application=app, title="AudioMic")
        self.engine = engine
        self.cfg = cfg
        self._updating = False
        self._refresh_source = None
        self._save_source = None
        self._known_streams = set()
        self._rows = {}
        self._levels = {"voice": 0.0, "media": 0.0, "call": 0.0}
        self._stop_events = threading.Event()

        self.set_default_size(560, 760)
        self.set_icon_name("audio-input-microphone")

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._build_header()
        self._build_body()

        self.meters = {
            "voice": meter.LevelMeter(lambda v: self._set_level("voice", v)),
            "media": meter.LevelMeter(lambda v: self._set_level("media", v)),
            "call": meter.LevelMeter(lambda v: self._set_level("call", v)),
        }
        GLib.timeout_add(50, self._animate_levels)

        self.connect("delete-event", self._on_close)
        self._listener = threading.Thread(
            target=event_listener, args=(self._on_pulse_event, self._stop_events), daemon=True)
        self._listener.start()
        self.refresh()

    # ------------------------------------------------------------------ layout

    def _build_header(self):
        header = Gtk.HeaderBar(show_close_button=True, title="AudioMic")
        header.set_subtitle("Compartilhe o áudio do PC na call")
        self.power = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.power.set_tooltip_text("Liga/desliga o Microfone Virtual")
        self.power.connect("notify::active", self._on_power)
        header.pack_end(self.power)
        self.set_titlebar(header)

    def _build_body(self):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                        margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        scroller.add(outer)
        self.add(scroller)

        self.infobar = Gtk.InfoBar(message_type=Gtk.MessageType.ERROR, show_close_button=True)
        self.infobar_label = _label("", wrap=True)
        self.infobar.get_content_area().add(self.infobar_label)
        self.infobar.connect("response", lambda bar, _r: bar.hide())
        self.infobar.set_no_show_all(True)
        outer.pack_start(self.infobar, False, False, 0)

        # --- status
        frame, box = _card("Status")
        self.status_label = _label("", wrap=True)
        box.pack_start(self.status_label, False, False, 0)
        self.mic_hint = Gtk.Box(spacing=8)
        self.mic_hint.pack_start(_label("Na call, escolha o microfone:"), False, False, 0)
        self.mic_hint.pack_start(_label(MIC_DESC, "am-mic-name"), False, False, 0)
        box.pack_start(self.mic_hint, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.pack_start(_label("A call está ouvindo", "am-hint"), False, False, 0)
        self.call_bar = _level_bar()
        row.pack_start(self.call_bar, True, True, 0)
        self.call_row = row
        box.pack_start(row, False, False, 0)
        self.preview = Gtk.CheckButton(label="Ouvir no fone exatamente o que a call ouve (teste)")
        self.preview.connect("toggled", self._on_preview)
        box.pack_start(self.preview, False, False, 0)
        outer.pack_start(frame, False, False, 0)

        # --- mix
        frame, box = _card("O que a call ouve")
        modes = Gtk.Box()
        modes.get_style_context().add_class("linked")
        self.mix_buttons = {}
        group = None
        for mode, text in ((MIX_BOTH, "🎤 + 🎵  Voz e mídia"), (MIX_MEDIA, "🎵  Só mídia"),
                           (MIX_VOICE, "🎤  Só voz")):
            btn = Gtk.RadioButton.new_with_label_from_widget(group, text)
            btn.set_mode(False)
            group = group or btn
            btn.connect("toggled", self._on_mix_mode, mode)
            modes.pack_start(btn, True, True, 0)
            self.mix_buttons[mode] = btn
        box.pack_start(modes, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        self.vol_scales, self.vol_bars = {}, {}
        for i, (role, text) in enumerate((("voice", "🎤 Voz"), ("media", "🎵 Mídia"))):
            grid.attach(_label(text), 0, i * 2, 1, 1)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 150, 1)
            scale.set_hexpand(True)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.add_mark(100, Gtk.PositionType.BOTTOM, None)
            scale.connect("format-value", lambda _s, v: "{:.0f}%".format(v))
            scale.connect("value-changed", self._on_volume, role)
            grid.attach(scale, 1, i * 2, 1, 1)
            bar = _level_bar()
            bar.set_margin_end(56)
            grid.attach(bar, 1, i * 2 + 1, 1, 1)
            self.vol_scales[role], self.vol_bars[role] = scale, bar
        box.pack_start(grid, False, False, 0)
        outer.pack_start(frame, False, False, 0)

        # --- apps
        frame, box = _card("Apps tocando áudio",
                           "Ligue o botão dos apps que a call deve ouvir (ex.: o navegador com "
                           "YouTube/TikTok). Você continua ouvindo tudo no fone.")
        share = Gtk.Box()
        share.get_style_context().add_class("linked")
        self.share_buttons = {}
        group = None
        for mode, text in ((SHARE_APPS, "Só os que eu escolher"), (SHARE_ALL, "Tudo, menos a call")):
            btn = Gtk.RadioButton.new_with_label_from_widget(group, text)
            btn.set_mode(False)
            group = group or btn
            btn.connect("toggled", self._on_share_mode, mode)
            share.pack_start(btn, True, True, 0)
            self.share_buttons[mode] = btn
        box.pack_start(share, False, False, 0)
        self.share_hint = _label("", "am-hint", wrap=True)
        box.pack_start(self.share_hint, False, False, 0)

        self.app_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.app_list.get_style_context().add_class("frame")
        placeholder = _label("Nenhum app tocando áudio agora.\nDê play no YouTube, TikTok…",
                             "am-hint", xalign=0.5)
        placeholder.set_justify(Gtk.Justification.CENTER)
        placeholder.set_margin_top(18)
        placeholder.set_margin_bottom(18)
        placeholder.show()
        self.app_list.set_placeholder(placeholder)
        self.app_list.set_header_func(self._list_header, None)
        box.pack_start(self.app_list, False, False, 0)
        outer.pack_start(frame, False, False, 0)

        # --- devices
        frame, box = _card("Dispositivos")
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.attach(_label("Microfone"), 0, 0, 1, 1)
        self.mic_combo = Gtk.ComboBoxText(hexpand=True)
        self.mic_combo.connect("changed", self._on_mic_changed)
        grid.attach(self.mic_combo, 1, 0, 1, 1)
        grid.attach(_label("Fone / saída"), 0, 1, 1, 1)
        self.out_combo = Gtk.ComboBoxText(hexpand=True)
        self.out_combo.connect("changed", self._on_out_changed)
        grid.attach(self.out_combo, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.monitor_check = Gtk.CheckButton(label="Ouvir no fone o áudio que estou compartilhando")
        self.monitor_check.connect("toggled", self._on_monitor)
        box.pack_start(self.monitor_check, False, False, 0)
        self.keep_check = Gtk.CheckButton(label="Manter ligado ao fechar esta janela")
        self.keep_check.connect("toggled", self._on_keep)
        box.pack_start(self.keep_check, False, False, 0)
        self._device_ids = (None, None)
        outer.pack_start(frame, False, False, 0)

        # --- tips
        tips = _label(
            "Dicas: use um navegador para a call e outro para a mídia (ex.: Meet no Chrome, "
            "YouTube no Firefox). Se a música picotar, desligue a redução de ruído da call "
            "(Meet: Configurações › Áudio; Zoom: “Som original para músicos”).",
            "am-hint", wrap=True)
        outer.pack_start(tips, False, False, 0)

        if not meter.AVAILABLE:
            for bar in (self.call_bar, *self.vol_bars.values()):
                bar.set_no_show_all(True)
                bar.hide()
            self.call_row.set_no_show_all(True)
            self.call_row.hide()

    def _list_header(self, row, before, _data):
        if before is not None and row.get_header() is None:
            row.set_header(Gtk.Separator())

    # ------------------------------------------------------------------ helpers

    def _error(self, err):
        self.infobar_label.set_text(str(err))
        self.infobar.show()
        self.infobar_label.show()

    def _safe(self, func, *args):
        """Run an engine call; show errors instead of crashing. Reconnect if needed."""
        try:
            return func(*args)
        except pulsectl.PulseDisconnected:
            self._reconnect()
        except (AudioMicError, pulsectl.PulseError) as err:
            self._error(err)
        return None

    def _reconnect(self):
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.engine = Engine("audio-mic-gui")
            self.infobar.hide()
        except AudioMicError as err:
            self._error(err)

    # ------------------------------------------------------------------ live updates

    def _on_pulse_event(self, _ev):
        # Called from the listener thread: hop to the GTK main loop, coalescing bursts.
        GLib.idle_add(self._schedule_refresh)

    def _schedule_refresh(self):
        if self._refresh_source is None:
            self._refresh_source = GLib.timeout_add(120, self._do_scheduled_refresh)
        return False

    def _do_scheduled_refresh(self):
        self._refresh_source = None
        self.refresh()
        return False

    def refresh(self):
        try:
            self._refresh()
        except pulsectl.PulseDisconnected:
            self._reconnect()
        except pulsectl.PulseError as err:
            self._error(err)

    def _refresh(self):
        self.cfg.load()  # the CLI may have changed something meanwhile
        active = self.engine.is_active()
        self._updating = True
        try:
            self.power.set_active(active)
            mode = self.engine.mix_mode() if active else self.cfg.get("mix_mode")
            self.mix_buttons[mode].set_active(True)
            self.share_buttons[self.cfg.get("share_mode")].set_active(True)
            for role in ("voice", "media"):
                vol = self.engine.volume(role) if active else None
                self.vol_scales[role].set_value(vol if vol is not None else self.cfg.get(role + "_volume"))
            self.preview.set_active(active and self.engine.preview_enabled())
            self.monitor_check.set_active(bool(self.cfg.get("monitor")))
            self.keep_check.set_active(bool(self.cfg.get("keep_on_close")))
            self._refresh_devices()
        finally:
            self._updating = False

        for widget in (self.preview, self.mic_hint, self.call_row):
            widget.set_sensitive(active)
        self.mic_hint.set_visible(active)
        ctx = self.status_label.get_style_context()
        if active:
            self.status_label.set_text("✔ Microfone Virtual ligado")
            ctx.add_class("am-status-on")
            ctx.remove_class("am-status-off")
        else:
            self.status_label.set_text("Desligado. Ligue no botão lá em cima para criar o Microfone Virtual.")
            ctx.add_class("am-status-off")
            ctx.remove_class("am-status-on")
        if self.cfg.get("share_mode") == SHARE_ALL:
            self.share_hint.set_text("Todo app que tocar vai para a call, exceto os que você desligar "
                                     "(navegadores e apps de call já vêm desligados, para não dar eco).")
        else:
            self.share_hint.set_text("Só vão para a call os apps que você ligar. Novos áudios do mesmo "
                                     "app seguem a sua escolha automaticamente.")

        self._refresh_apps(active)
        self._update_meters(active)

    def _refresh_devices(self):
        sources = [(s.name, s.description) for s in self.engine.sources()]
        sinks = [(s.name, s.description) for s in self.engine.sinks()]
        if (sources, sinks) != self._device_ids:
            self._device_ids = (sources, sinks)
            for combo, items in ((self.mic_combo, sources), (self.out_combo, sinks)):
                combo.remove_all()
                combo.append("", "Padrão do sistema")
                for name, desc in items:
                    combo.append(name, desc or name)
        cur = self.engine.current_devices() if self.engine.is_active() else {}
        for combo, key in ((self.mic_combo, "mic"), (self.out_combo, "output")):
            wanted = self.cfg.get(key) or ""
            if not combo.set_active_id(wanted):
                combo.set_active_id("")
            combo.set_tooltip_text("Em uso: {}".format(cur.get(key)) if cur.get(key) else None)

    def _refresh_apps(self, active):
        apps = self.engine.apps()
        indexes = {a.index for a in apps}
        new = indexes - self._known_streams
        self._known_streams = indexes
        if active and new:
            # New streams follow the remembered per-app choice.
            if self.engine.apply_rules(self.cfg, only_indexes=new):
                apps = self.engine.apps()

        groups = {}
        order = []
        for a in apps:
            if a.key not in groups:
                groups[a.key] = []
                order.append(a.key)
            groups[a.key].append(a)

        for key in list(self._rows):
            if key not in groups:
                self.app_list.remove(self._rows.pop(key))
        for key in order:
            row = self._rows.get(key)
            if row is None:
                row = AppRow(key, self._on_app_toggle)
                self._rows[key] = row
                self.app_list.add(row)
                row.show_all()
            row.update(groups[key], active)

    # ------------------------------------------------------------------ meters

    def _update_meters(self, active):
        visible = self.get_mapped()
        if active and visible:
            devices = self.engine.current_devices()
            if devices.get("mic"):
                self.meters["voice"].start(devices["mic"])
            self.meters["media"].start(SHARE_SINK + ".monitor")
            self.meters["call"].start(VIRTUAL_MIC)
        else:
            for m in self.meters.values():
                m.stop()

    def _set_level(self, role, value):
        # Fast attack, slow decay: easier to read.
        self._levels[role] = max(value, self._levels[role] * 0.85)

    def _animate_levels(self):
        mode = self._current_mix_mode()
        voice = self._levels["voice"] if mode != MIX_MEDIA else 0.0
        media = self._levels["media"] if mode != MIX_VOICE else 0.0
        self.vol_bars["voice"].set_value(voice)
        self.vol_bars["media"].set_value(media)
        self.call_bar.set_value(self._levels["call"])
        for role in self._levels:
            self._levels[role] *= 0.85
        return True

    def _current_mix_mode(self):
        for mode, btn in self.mix_buttons.items():
            if btn.get_active():
                return mode
        return MIX_BOTH

    # ------------------------------------------------------------------ handlers

    def _on_power(self, switch, _pspec):
        if self._updating:
            return
        if switch.get_active():
            self._safe(self.engine.activate, self.cfg)
            self._known_streams = {a.index for a in self._safe(self.engine.apps) or []}
        else:
            for m in self.meters.values():
                m.stop()
            self._safe(self.engine.stop)
        self.refresh()

    def _on_mix_mode(self, btn, mode):
        if self._updating or not btn.get_active():
            return
        self.cfg.set("mix_mode", mode)
        if self.engine.is_active():
            self._safe(self.engine.set_mix_mode, mode)

    def _on_share_mode(self, btn, mode):
        if self._updating or not btn.get_active():
            return
        self.cfg.set("share_mode", mode)
        self._safe(self.engine.apply_rules, self.cfg)
        self.refresh()

    def _on_volume(self, scale, role):
        if self._updating:
            return
        value = int(scale.get_value())
        self.cfg.set(role + "_volume", value, save=False)
        if self.engine.is_active():
            self._safe(self.engine.set_volume, role, value)
        self._save_later()

    def _save_later(self):
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(500, self._save_now)

    def _save_now(self):
        self._save_source = None
        self.cfg.save()
        return False

    def _on_app_toggle(self, key, shared):
        self.cfg.remember_app(key, shared)
        for app in self._safe(self.engine.apps) or []:
            if app.key == key and app.shared != shared:
                self._safe(self.engine.share, app.index, shared, self.cfg.get("output"))
        self.refresh()

    def _on_mic_changed(self, combo):
        if self._updating:
            return
        self.cfg.set("mic", combo.get_active_id() or None)
        if self.engine.is_active():
            self._safe(self.engine.set_mic, self.cfg.get("mic"))
            self.meters["voice"].stop()
        self.refresh()

    def _on_out_changed(self, combo):
        if self._updating:
            return
        self.cfg.set("output", combo.get_active_id() or None)
        if self.engine.is_active():
            self._safe(self.engine.set_output, self.cfg.get("output"), self.cfg.get("monitor"))
        self.refresh()

    def _on_monitor(self, check):
        if self._updating:
            return
        self.cfg.set("monitor", check.get_active())
        if self.engine.is_active():
            self._safe(self.engine.set_output, self.cfg.get("output"), check.get_active())

    def _on_keep(self, check):
        if not self._updating:
            self.cfg.set("keep_on_close", check.get_active())

    def _on_preview(self, check):
        if self._updating:
            return
        self._safe(self.engine.set_preview, check.get_active(), self.cfg.get("output"))

    def _on_close(self, *_args):
        self._stop_events.set()
        for m in self.meters.values():
            m.stop()
        if self._save_source is not None:
            self._save_now()
        try:
            if self.engine.is_active():
                self.engine.set_preview(False)
                if not self.cfg.get("keep_on_close"):
                    self.engine.stop()
        except pulsectl.PulseError:
            pass
        self.engine.close()
        return False


class AudioMicApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window = None

    def do_activate(self):
        if self.window is None:
            try:
                engine = Engine("audio-mic-gui")
            except AudioMicError as err:
                dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR,
                                           buttons=Gtk.ButtonsType.CLOSE, text="AudioMic")
                dialog.format_secondary_text(str(err))
                dialog.run()
                dialog.destroy()
                return
            self.window = MainWindow(self, engine, Config())
            self.window.show_all()
            self.window.refresh()
        self.window.present()


def main():
    if GI_ERROR is not None:
        print("A interface gráfica precisa do PyGObject/GTK 3 (pacotes python3-gi e "
              "gir1.2-gtk-3.0, que já vêm no Ubuntu desktop).\nErro: {}\n"
              "Você ainda pode usar a linha de comando: audio-mic --help".format(GI_ERROR),
              file=sys.stderr)
        return 1
    return AudioMicApp().run([sys.argv[0]])
