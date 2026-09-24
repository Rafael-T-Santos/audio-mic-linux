"""Linha de comando do AudioMic. Rodar sem argumentos abre a interface gráfica."""

import argparse
import sys
import threading

from .config import Config
from .core import (
    MIC_DESC, MIX_BOTH, MIX_MEDIA, MIX_VOICE, SHARE_ALL, SHARE_APPS, AudioMicError, Engine,
    event_listener)

MIX_ALIASES = {
    "both": MIX_BOTH, "tudo": MIX_BOTH, "voz+midia": MIX_BOTH, "ambos": MIX_BOTH,
    "media": MIX_MEDIA, "midia": MIX_MEDIA, "mídia": MIX_MEDIA,
    "voice": MIX_VOICE, "voz": MIX_VOICE,
}
MIX_LABELS = {MIX_BOTH: "voz + mídia", MIX_MEDIA: "só mídia", MIX_VOICE: "só voz"}
SHARE_ALIASES = {"apps": SHARE_APPS, "all": SHARE_ALL, "tudo": SHARE_ALL}
ROLE_ALIASES = {"voice": "voice", "voz": "voice", "media": "media", "midia": "media",
                "mídia": "media"}


def _need_active(engine):
    if not engine.is_active():
        raise AudioMicError("AudioMic não está ativo. Rode: audio-mic start")


def cmd_start(engine, cfg, args):
    if args.mic:
        cfg.set("mic", args.mic)
    if args.out:
        cfg.set("output", args.out)
    engine.activate(cfg)
    st = engine.status()
    print("AudioMic ativo.")
    print("  Microfone real : {}".format(st.get("mic")))
    print("  Fone / saída   : {}".format(st.get("output")))
    print("  A call ouve    : {}".format(MIX_LABELS[st["mix_mode"]]))
    print()
    print('Na call (Meet/Zoom/Gather), escolha o microfone "{}".'.format(MIC_DESC))
    print("Para compartilhar um app: audio-mic apps  e  audio-mic share <id|nome>")
    if cfg.get("share_mode") == SHARE_ALL or cfg.get("shared_apps"):
        print("Dica: 'audio-mic watch' (ou a interface gráfica) aplica suas regras aos apps que")
        print("começarem a tocar depois.")


def cmd_stop(engine, cfg, args):
    engine.stop()
    print("AudioMic desligado. Dispositivos virtuais removidos.")


def cmd_status(engine, cfg, args):
    st = engine.status()
    if not st["active"]:
        print("AudioMic: desligado")
        return
    print("AudioMic: ATIVO")
    print("  Microfone real : {}".format(st.get("mic")))
    print("  Fone / saída   : {}{}".format(st.get("output"), "" if st.get("monitor") else " (sem retorno)"))
    print("  A call ouve    : {}".format(MIX_LABELS[st["mix_mode"]]))
    print("  Volume voz     : {}%".format(st.get("voice_volume")))
    print("  Volume mídia   : {}%".format(st.get("media_volume")))
    print("  Modo apps      : {}".format(
        "tudo, exceto a call" if cfg.get("share_mode") == SHARE_ALL else "só os escolhidos"))
    shared = [a for a in engine.apps() if a.shared]
    print("  Compartilhando : {}".format(", ".join(a.name for a in shared) or "nenhum app"))


def cmd_apps(engine, cfg, args):
    apps = engine.apps()
    if not apps:
        print("Nenhum app tocando áudio agora.")
        return
    print("{:>5}  {:<4} {:<20} {}".format("ID", "CALL", "APP", "TÍTULO"))
    for a in apps:
        print("{:>5}  {:<4} {:<20} {}".format(
            a.index, "sim" if a.shared else "-", a.name[:20], a.title[:60]))


def cmd_devices(engine, cfg, args):
    print("Microfones:")
    for s in engine.sources():
        print("  {:<60} {}".format(s.name, s.description))
    print("Saídas (fones/caixas):")
    for s in engine.sinks():
        print("  {:<60} {}".format(s.name, s.description))


def cmd_share(engine, cfg, args, shared=True):
    _need_active(engine)
    apps = engine.find_apps(args.app)
    if not apps:
        raise AudioMicError("Nenhum app tocando encontrado para '{}'. Veja: audio-mic apps".format(args.app))
    for app in apps:
        engine.share(app.index, shared, cfg.get("output"))
        if not args.once:
            cfg.remember_app(app.key, shared)
        print("{} {} ({})".format("Compartilhando" if shared else "Só no fone:", app.name, app.index))


def cmd_mix(engine, cfg, args):
    mode = MIX_ALIASES.get(args.mode.lower())
    if not mode:
        raise AudioMicError("Use: voz+midia, midia ou voz")
    cfg.set("mix_mode", mode)
    if engine.is_active():
        engine.set_mix_mode(mode)
    print("A call ouve: {}".format(MIX_LABELS[mode]))


def cmd_vol(engine, cfg, args):
    role = ROLE_ALIASES.get(args.role.lower())
    if not role:
        raise AudioMicError("Use: voz ou midia")
    pct = max(0, min(150, int(args.percent.rstrip("%"))))
    cfg.set(role + "_volume", pct)
    if engine.is_active():
        engine.set_volume(role, pct)
    print("Volume {}: {}%".format("voz" if role == "voice" else "mídia", pct))


def cmd_mode(engine, cfg, args):
    mode = SHARE_ALIASES.get(args.mode.lower())
    if not mode:
        raise AudioMicError("Use: apps ou tudo")
    cfg.set("share_mode", mode)
    moved = engine.apply_rules(cfg)
    if mode == SHARE_ALL:
        print("Modo TUDO: todo app que tocar vai para a call, exceto: {}".format(
            ", ".join(cfg.get("excluded_apps")) or "nenhum"))
    else:
        print("Modo APPS: só vão para a call: {}".format(", ".join(cfg.get("shared_apps")) or "nenhum"))
    for app in moved:
        print("  movido: {} -> {}".format(app.name, "fone" if app.shared else "call"))


def cmd_watch(engine, cfg, args):
    """Keep applying the per-app rules to new streams until Ctrl+C."""
    _need_active(engine)
    print("Aplicando regras aos apps novos. Ctrl+C para sair (o AudioMic continua ativo).")
    stop = threading.Event()
    pending = threading.Event()
    thread = threading.Thread(target=event_listener, args=(lambda ev: pending.set(), stop), daemon=True)
    thread.start()
    known = {a.index for a in engine.apps()}
    try:
        while True:
            if not pending.wait(1):
                continue
            pending.clear()
            cfg.load()
            current = {a.index for a in engine.apps()}
            new = current - known
            known = current
            if new:
                for app in engine.apply_rules(cfg, only_indexes=new):
                    print("  {} -> {}".format(app.name, "fone" if app.shared else "call"))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


def build_parser():
    p = argparse.ArgumentParser(
        prog="audio-mic",
        description="Toque o áudio de outros apps no seu microfone (Meet, Zoom, Gather...). "
                    "Sem argumentos, abre a interface gráfica.")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="abre a interface gráfica")
    s = sub.add_parser("start", help="liga o microfone virtual")
    s.add_argument("--mic", help="microfone real (veja 'devices')")
    s.add_argument("--out", help="fone/saída (veja 'devices')")
    sub.add_parser("stop", help="desliga e remove os dispositivos virtuais")
    sub.add_parser("status", help="mostra o estado atual")
    sub.add_parser("apps", help="lista os apps tocando áudio")
    sub.add_parser("devices", help="lista microfones e saídas")
    for name, help_ in (("share", "envia um app para a call"),
                        ("unshare", "tira um app da call (continua no fone)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("app", help="ID (de 'apps') ou nome, ex.: firefox")
        s.add_argument("--once", action="store_true", help="não lembrar a escolha para esse app")
    s = sub.add_parser("mix", help="o que a call ouve: voz+midia | midia | voz")
    s.add_argument("mode")
    s = sub.add_parser("vol", help="volume na mistura: vol voz|midia 0-150")
    s.add_argument("role")
    s.add_argument("percent")
    s = sub.add_parser("mode", help="quais apps vão para a call: apps | tudo")
    s.add_argument("mode")
    sub.add_parser("watch", help="aplica as regras aos apps que começarem a tocar")
    return p


COMMANDS = {
    "start": cmd_start, "stop": cmd_stop, "status": cmd_status, "apps": cmd_apps,
    "devices": cmd_devices, "share": cmd_share,
    "unshare": lambda e, c, a: cmd_share(e, c, a, shared=False),
    "mix": cmd_mix, "vol": cmd_vol, "mode": cmd_mode, "watch": cmd_watch,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "gui"):
        from .gui import main as gui_main
        return gui_main()
    try:
        engine = Engine("audio-mic-cli")
    except AudioMicError as err:
        print("Erro: {}".format(err), file=sys.stderr)
        return 1
    try:
        COMMANDS[args.cmd](engine, Config(), args)
        return 0
    except (AudioMicError, ValueError) as err:
        print("Erro: {}".format(err), file=sys.stderr)
        return 1
    finally:
        engine.close()
