# AudioMic

Toque no seu **microfone** o áudio de outros apps (YouTube, TikTok, Spotify, VLC…) durante
uma call no **Meet, Zoom, Gather, Teams, Discord**. Os participantes ouvem a sua voz **e** a
mídia, e você continua ouvindo tudo normalmente no fone.

- **Não precisa de sudo** e não precisa instalar pacotes: usa o PipeWire/PulseAudio que já
  vem no Ubuntu (não precisa nem do `pactl`).
- Tem **interface gráfica**, que se atualiza sozinha quando um app começa ou para de
  tocar, e também **linha de comando**.
- Três modos para o que a call ouve: **voz + mídia**, **só mídia** ou **só voz**.
- Escolha **quais apps** vão para a call, ou mande **tudo, menos a própria call** (sem eco).

## Instalação (sem sudo)

1. Baixe o projeto: no GitHub, clique em **Code → Download ZIP** e extraia. Se tiver o
   `git`, pode clonar o repositório.
2. Abra um terminal na pasta extraída e rode:

   ```sh
   sh install.sh
   ```

Isso instala só para o seu usuário, em `~/.local` (o programa fica em
`~/.local/share/audio-mic`, o comando `audio-mic` fica em `~/.local/bin`, e o atalho
**AudioMic** aparece no menu de aplicativos).

Para usar sem instalar: `./audio-mic` na pasta do projeto.
Para desinstalar: `sh ~/.local/share/audio-mic/uninstall.sh`.

> Requisitos: Ubuntu 22.04 ou mais novo (ou outra distro com PipeWire ou PulseAudio), com
> `python3`, `python3-gi` e `libpulse0`. Todos já vêm no Ubuntu desktop. Os medidores de
> nível usam o GStreamer; se ele faltar, só os medidores somem.

## Como usar

1. Abra o **AudioMic** e ligue o botão lá em cima. Aparece um microfone novo no sistema:
   **Microfone Virtual (AudioMic)**.
2. Na call, selecione esse microfone:
   - **Meet**: ⋮ → Configurações → Áudio → Microfone
   - **Zoom**: Configurações → Áudio → Microfone
   - **Gather**: Configurações → Áudio/Vídeo → Microfone
3. Dê play no YouTube ou TikTok. O app aparece na lista **Apps tocando áudio**: ligue o
   botão **na call** dele.
4. Em **O que a call ouve**, escolha **Voz e mídia**, **Só mídia** ou **Só voz**, e ajuste o
   volume de cada um. Os medidores mostram o que está saindo.

Marque **"Ouvir no fone exatamente o que a call ouve"** para conferir como os outros estão
te ouvindo.

### Dicas importantes

- **Use um navegador para a call e outro para a mídia**: por exemplo, Meet no Chrome e
  YouTube no Firefox. Um navegador costuma juntar o áudio de todas as abas, e aí não dá para
  separar a call da mídia.
- **Não compartilhe o app da própria call**: os outros ouviriam a voz deles de volta (eco).
  A interface avisa quando isso pode acontecer.
- **Se a música picotar ou sumir**, desligue a redução de ruído da call:
  - **Meet**: Configurações → Áudio → Redução de ruído
  - **Zoom**: Configurações → Áudio → "Som original para músicos"
- No modo **"Tudo, menos a call"**, qualquer app que tocar vai para a call. Navegadores e
  apps de call já vêm fora da lista para evitar eco; você pode mudar app por app.
- O AudioMic lembra a sua escolha por app: um vídeo novo no Firefox segue o que você
  escolheu para o Firefox.
- Ao fechar a janela, o microfone virtual é removido e tudo volta ao normal. Para mantê-lo
  ligado, marque **"Manter ligado ao fechar esta janela"**.

## Linha de comando

```
audio-mic                      # abre a interface gráfica
audio-mic start [--mic X] [--out Y]
audio-mic stop                 # remove tudo e volta ao normal
audio-mic status
audio-mic apps                 # lista quem está tocando (com ID)
audio-mic share firefox        # manda um app para a call (por nome ou ID)
audio-mic unshare firefox      # tira da call (continua no fone)
audio-mic mix voz+midia|midia|voz
audio-mic vol voz|midia 0-150
audio-mic mode apps|tudo
audio-mic devices              # nomes de microfones e saídas para --mic/--out
audio-mic watch                # sem a janela: aplica as regras aos apps que começarem a tocar
```

As preferências ficam em `~/.config/audio-mic/config.json`.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Ninguém me ouve | Confira se o microfone da call é **Microfone Virtual (AudioMic)** e se o modo não está em **Só mídia**. |
| Os outros ouvem eco da própria voz | O app da call está marcado como **na call**. Desligue o botão dele. |
| A música chega picotada | Desligue a redução de ruído da call (veja as dicas acima). |
| O app não aparece na lista | Ele só aparece enquanto está tocando. Dê play e espere um segundo. |
| Algo ficou estranho no áudio | `audio-mic stop` remove tudo o que o AudioMic criou. |

## Como funciona

O AudioMic cria dispositivos virtuais no servidor de áudio (via protocolo PulseAudio, que o
PipeWire também entende) e os conecta assim:

```
apps escolhidos ─▶ [Compartilhar] ─┬─▶ loopback ─▶ seu fone      (você continua ouvindo)
                                   └─▶ loopback ─┐
microfone real ──────────────────────▶ loopback ─┴─▶ [Mistura] ─▶ Microfone Virtual ─▶ call
```

O áudio da call nunca entra na mistura, por isso não há eco. Todo o estado fica no próprio
servidor de áudio: a janela e o terminal podem ser abertos e fechados à vontade, e
`audio-mic stop` sempre limpa tudo. O plano original está em [PLANO.md](PLANO.md).

## Desenvolvimento

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_integration.py` cria dispositivos falsos, toca tons fingindo ser o Firefox e o
microfone, grava do Microfone Virtual e confere o que chega em cada modo. Ele precisa de um
servidor de áudio e do `pulseaudio-utils` (`paplay`/`parec`); sem isso, é pulado. A
biblioteca [pulsectl](https://github.com/mk-fg/python-pulse-control) (MIT) vem embutida em
`audio_mic/_vendor`, para não precisar de `pip`.
