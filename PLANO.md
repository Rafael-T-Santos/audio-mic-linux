# Plano: áudio do sistema como microfone (Linux)

Objetivo: numa call (Meet, Zoom, Gather…), os participantes ouvem **minha voz + o áudio
que eu escolher** (YouTube, TikTok etc. tocando em outro navegador), enquanto eu continuo
ouvindo tudo normalmente no fone.

## 1. Ideia central

Não dá para "injetar" áudio no microfone físico, mas o PipeWire/PulseAudio permite criar
**dispositivos virtuais**. Criamos um microfone virtual que é a mistura do mic real com o
áudio dos apps escolhidos, e selecionamos esse microfone virtual no Meet/Zoom/Gather.

```
 YouTube / TikTok (Firefox)                     Meet / Zoom (Chrome)
          │                                         ▲          │
          ▼                                         │          ▼
 ┌─────────────────────┐   loopback   ┌──────────┐  │   Fone (saída real)
 │ sink "Compartilhar" │─────────────▶│   Fone   │  │          ▲
 │  (null sink)        │              └──────────┘  │          │ (áudio da call
 └─────────┬───────────┘                            │          │  vai direto p/ fone,
           │ .monitor ── loopback ──┐               │          │  NUNCA para a mistura)
           ▼                        ▼               │
                          ┌─────────────────────┐   │
 Mic real ── loopback ───▶│ sink "MicMix"       │   │
                          │  (null sink)        │   │
                          └─────────┬───────────┘   │
                                    │ .monitor      │
                                    ▼               │
                          ┌─────────────────────┐   │
                          │ source "Mic Virtual"│───┘  ← escolhido como microfone na call
                          │  (remap-source)     │
                          └─────────────────────┘
```

Peças (todas criadas com `pactl load-module`, funciona tanto em PulseAudio quanto em
PipeWire via `pipewire-pulse`):

| Peça | Módulo | Função |
|---|---|---|
| `audiomic_share` | `module-null-sink` | Saída para onde movemos os apps que queremos compartilhar |
| loopback share → fone | `module-loopback` | Para eu continuar ouvindo o que é compartilhado |
| `audiomic_mix` | `module-null-sink` | Onde mic real + áudio compartilhado são somados |
| loopback mic → mix | `module-loopback` | Minha voz entra na mistura |
| loopback share → mix | `module-loopback` | Áudio dos apps entra na mistura |
| `audiomic_mic` | `module-remap-source` | Expõe `audiomic_mix.monitor` como um microfone "normal" (navegadores costumam esconder fontes `.monitor`) |

## 2. Armadilha principal: eco / feedback

Se eu simplesmente mandar **tudo** que toca no fone para o microfone, a voz dos outros
participantes volta para a call (eles se ouvem com atraso). Por isso **o áudio da própria
call nunca pode ir para o sink "Compartilhar"**. Dois modos resolvem isso:

- **Modo "apps"** (padrão): só os streams que eu escolher (ex.: Firefox com YouTube) são
  movidos para "Compartilhar". Todo o resto continua indo direto para o fone.
- **Modo "tudo"**: "Compartilhar" vira a saída padrão do sistema (tudo que tocar é
  compartilhado) e o app da call (Chrome/Zoom) é fixado na saída real do fone.
  Atende o "tudo que ouço no fone" sem criar eco.

Recomendação de uso: **navegador diferente** para a call e para a mídia (ex.: Meet no
Chrome, YouTube/TikTok no Firefox). Um mesmo navegador tende a juntar o áudio de várias
abas num único stream, o que impede separar a call da mídia.

## 3. Outras armadilhas conhecidas

- **Supressão de ruído / cancelamento de eco da call**: Meet/Zoom tratam música como
  ruído e podem cortar ou "picotar" o áudio. Mitigações: desligar supressão de ruído no
  Meet; no Zoom usar "Som original para músicos". Documentar no README.
- **Troca de fone** (plugar/desplugar Bluetooth): o loopback "share → fone" deve seguir a
  saída padrão, não um dispositivo fixo; o modo "tudo" precisa reaplicar a regra do app
  da call.
- **Latência**: loopbacks com `latency_msec` baixo (~20–30 ms) para a voz não atrasar em
  relação à mídia.
- **Limpeza**: guardar os IDs dos módulos carregados em `$XDG_RUNTIME_DIR/audio-mic/` para
  que `stop` remova exatamente o que foi criado (e nada do usuário), mesmo após crash.
- **Idempotência**: `start` duas vezes não deve duplicar dispositivos.

## 4. Interface proposta (CLI `audio-mic`, em Bash + `pactl`)

```
audio-mic start [--mic <source>] [--out <sink>]   # cria os dispositivos virtuais
audio-mic stop                                    # remove tudo e restaura padrões
audio-mic status                                  # mostra o que está ativo e o que é compartilhado
audio-mic apps                                    # lista streams tocando (id, app, título)
audio-mic share <id|nome-do-app>                  # passa a enviar esse app para a call
audio-mic unshare <id|nome-do-app>                # volta a tocar só no fone
audio-mic mode apps|tudo [--call <app>]           # alterna os modos da seção 2
audio-mic vol mic|media <0-150>%                  # volume da voz / da mídia na mistura
audio-mic mute mic|media [on|off|toggle]          # mutar voz ou mídia separadamente
```

Dependências: `pactl` (pacote `pulseaudio-utils`/`libpulse`), opcional `pavucontrol`
para ajuste visual.

## 5. Fases de implementação

1. **MVP** — `start`/`stop`/`status`: cria o grafo da seção 1, salva estado, remove com
   segurança. Teste manual: tocar YouTube no Firefox, mover via `pavucontrol`, ouvir o
   "Mic Virtual" com `parecord`/gravador e confirmar voz + mídia.
2. **Roteamento** — `apps`, `share`, `unshare`, busca por nome do app
   (`application.name` / `application.process.binary`).
3. **Modo "tudo"** — troca da saída padrão + fixação do app da call no fone; restaurar
   a saída original no `stop`.
4. **Controles de mixagem** — `vol` e `mute` para voz e mídia de forma independente.
5. **Robustez** — seguir troca de fone, `trap` para limpeza, mensagens de erro claras
   (sem PipeWire/Pulse, mic inexistente etc.), `shellcheck` + CI no GitHub Actions.
6. **Opcional** — instalador (`make install` → `~/.local/bin`), serviço `systemd --user`,
   atalho de teclado para mute da mídia, e talvez uma pequena GUI/tray.

## 6. Em aberto (definir antes de implementar)

- Distro e servidor de áudio (`pactl info | grep "Server Name"` → PipeWire ou PulseAudio?).
- Só CLI, ou também GUI/tray?
- Na mistura, sempre voz + mídia, ou também um modo "só mídia"? (hoje resolvido com
  `audio-mic mute mic`).
