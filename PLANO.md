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

## 4. Decisões (após as respostas)

- **Ubuntu, sem sudo, sem `pactl`**: em vez de Bash + `pactl`, o motor é em Python e fala
  direto com o servidor de áudio pela `libpulse` (biblioteca `pulsectl` embutida em
  `audio_mic/_vendor`). Funciona igual no PipeWire (Ubuntu 22.10+) e no PulseAudio (22.04),
  sem instalar nada.
- **Interface gráfica** em GTK 3 (PyGObject), que já vem no Ubuntu desktop. É dinâmica: um
  thread escuta os eventos do servidor de áudio e a janela se atualiza sozinha.
- **Três modos de mistura**: voz + mídia, só mídia, só voz (mute nos loopbacks).
- **Instalação** em `~/.local` com `install.sh`.

## 5. Estrutura

```
audio-mic               lançador (sem argumentos abre a janela)
audio_mic/core.py       motor: cria/remove o grafo, move apps, mute/volume, eventos
audio_mic/config.py     preferências em ~/.config/audio-mic/config.json
audio_mic/cli.py        linha de comando
audio_mic/gui.py        interface GTK 3
audio_mic/meter.py      medidores de nível (GStreamer, opcional)
install.sh / uninstall.sh
tests/                  testes de configuração + teste de ponta a ponta com áudio real
```

## 6. Fases

1. ✅ **MVP**: `start`/`stop`/`status`, idempotente, sem arquivos de estado (tudo é lido
   do servidor).
2. ✅ **Roteamento**: `apps`, `share`, `unshare`, por nome ou ID, com a escolha lembrada
   por app.
3. ✅ **Modo "tudo"**: tudo vai para a call, exceto os apps de call (lista editável). Não
   mexe na saída padrão do sistema.
4. ✅ **Controles de mixagem**: modos voz/mídia/ambos, volume de cada um, "ouvir o que a
   call ouve".
5. ✅ **Robustez**: reconexão se o servidor reiniciar, apps voltam para o fone sozinhos no
   `stop`, cada loopback tem sua própria chave de "restore" (senão o PipeWire podia devolver
   um mute antigo), e a config é mesclada entre GUI e CLI abertas ao mesmo tempo.
6. ✅ **Interface gráfica + instalador sem sudo**.
7. ⏭ Próximos passos possíveis: ícone na bandeja, atalho global de teclado para mutar a
   mídia, ajuste de latência na interface.
