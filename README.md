# Alflor Studio

**Comando virtual profissional para câmaras PTZ USB, DSLR e telemóveis, no Windows.**
Controlo por teclado, rato, joystick no ecrã, comando Xbox e telemóvel (Wi-Fi), com tracking de sujeito,
presets com miniatura, travellings, fotografia e vídeo de alta qualidade, e integração com OBS, VLC,
Premiere/CapCut, Stream Deck e câmara virtual (Zoom, Teams, Meet…).

> Testado com uma PTZ USB UVC genérica (VID 27C2 / PID 052F, 1080p30, pan ±2000, tilt ±900, zoom 0–99).
> Funciona com qualquer câmara que o Windows veja: sem motores usa **PTZ digital** (recorte inteligente).

---

## Funções

| Área | O que faz |
|---|---|
| **Controlo PTZ** | Movimento contínuo com curva de precisão, velocidade ajustável, zoom absoluto e contínuo, posição inicial. |
| **Rato** | Clique = centra a câmara nesse ponto · arrastar = mover · roda = zoom · botão direito arrastado = escolher sujeito a seguir · duplo clique = ecrã inteiro. |
| **Teclado** | Setas/WASD, zoom +/−, presets 1–9, foto, gravação, tracking, ferramentas (ver abaixo). |
| **Comando Xbox** | Stick = mover, gatilhos = zoom, A foto, B gravar, X tracking, Y início, LB/RB presets. |
| **Presets** | 9 por câmara, com miniatura e nome, transição suave com curva (Linear, Suave, Cinema, Acelerar, Travar). |
| **Travelling** | Trajetórias ponto a ponto com duração, pausa e curva por ponto; reprodução única ou em ciclo; guardar/abrir. |
| **Movimentos de realizador** | Push-in, pull-out, panorâmica, revelação em tilt, arco, chicote (whip pan), crash zoom. |
| **Tracking** | Rosto (Haar ou IA YuNet), pessoa (HOG + CSRT) ou qualquer objeto escolhido (CSRT). Composição ao centro ou nos terços, *headroom* correto, plano (grande plano → plano geral) com zoom automático, reatividade ajustável, regresso ao início se perder o sujeito. Funciona com motores reais **ou** em PTZ digital. |
| **Calibração** | Mede automaticamente quantas unidades de pan/tilt correspondem ao ecrã e a ampliação ótica do zoom (correlação de fase + ORB), para cliques e tracking precisos. |
| **Imagem (hardware)** | Brilho, contraste, saturação, nitidez, tonalidade, balanço de brancos (manual/auto) e, se a câmara tiver, foco, exposição, íris, ganho. Gerados automaticamente para cada câmara. |
| **Imagem (software)** | Redução de ruído temporal (3 níveis), modo noite (levanta sombras), nitidez extra, looks (Vivo, Quente, Frio, Cinema, Suave, P&B, Alto contraste), desfoque de fundo que segue o sujeito, espelho/inversão. |
| **Monitorização** | Grelha de terços, áreas seguras, zebras (sobre-exposição), *focus peaking*, histograma. |
| **Fotografia** | PNG sem perdas, JPEG 100 ou TIFF; temporizador 3/10 s; **burst** (escolhe automaticamente o frame mais nítido); **empilhamento noturno** (média de N frames → muito menos ruído). |
| **Vídeo** | FFmpeg: H.264 (x264 ou Intel QSV), H.265 QSV, MJPEG intra (edição); MP4/MOV/MKV; áudio de qualquer microfone; nomes `Projeto_Cena_T01_hhmmss` em pastas por data. |
| **OBS** | Liga por WebSocket (lê a password do OBS automaticamente): cenas, gravar, live, gravação sincronizada. |
| **Câmara virtual** | Envia a imagem processada (tracking digital, looks, ruído) para a «OBS Virtual Camera». |
| **Telemóvel / tablet** | Página web com joystick tátil, zoom, presets (toque longo guarda), foto, gravar, tracking, movimentos e imagem ao vivo. Basta ler o QR code. |
| **VLC / Stream Deck** | Stream MJPEG (`/stream.mjpg`) e API HTTP (`/api/cmd?a=photo`) para Bitfocus Companion/Stream Deck. |
| **Atualização automática** | Verifica as *releases* deste repositório e atualiza (git pull ou zip) com um clique ou automaticamente. |

## Instalação

Requisitos: Windows 10/11, Python 3.11–3.13, FFmpeg (`winget install Gyan.FFmpeg`).

```powershell
git clone https://github.com/Alflor2022/alflor-studio.git
cd alflor-studio
.\"Alflor Studio.bat"
```

Na primeira execução o `.bat` cria o ambiente `.venv` e instala as dependências. A ponte nativa
DirectShow (`ptzbridge.cs`) é compilada automaticamente com o compilador C# que já vem com o Windows.

## Atalhos

```
Setas / WASD ...... mover (Shift = rápido, Ctrl = preciso)
+ / E / PgUp ...... zoom in      − / Q / PgDn ...... zoom out
Home / H .......... posição inicial   Esc ...... parar tudo
1 – 9 ............. ir para preset     Ctrl+1 – 9 ...... guardar preset
Espaço ............ foto     R ...... gravar     T ...... tracking
G grelha · Z zebras · P peaking · N noite · B desfoque · M espelho
F11 / duplo clique . ecrã inteiro     Ctrl+P ...... reproduzir travelling
```

## Trabalhar com outras ferramentas

- **OBS Studio** – recomendados os plugins [obs-ptz](https://github.com/glikely/obs-ptz) (painel PTZ, também por UVC) e
  [obs-face-tracker](https://github.com/norihiro/obs-face-tracker). O Alflor Studio controla a câmara **mesmo quando o OBS a está a usar**
  (a ponte DirectShow funciona em paralelo). Para usar o tracking/looks do Alflor Studio dentro do OBS, ative a câmara virtual.
  Para ligar por WebSocket: OBS › Ferramentas › Definições do servidor WebSocket › Ativar.
- **Premiere Pro / CapCut / DaVinci** – os ficheiros ficam em `Vídeos\Alflor Studio\AAAA-MM-DD\{Fotos,Videos}` com nomes de projeto, cena e take.
  Para edição pesada use o codec *MJPEG intra*.
- **VLC** – Abrir fluxo de rede: `http://IP:8765/stream.mjpg?k=TOKEN` (mostrado no separador Ligações).
- **Cloud** – escolha a pasta do OneDrive/Google Drive como pasta de saída (botão OneDrive incluído).
- **Stream Deck (Bitfocus Companion)** – módulo *Generic HTTP*, ex.: `GET http://IP:8765/api/cmd?k=TOKEN&a=preset&n=2`.

### API HTTP (`/api/cmd`)

| Ação | Parâmetros |
|---|---|
| `vel` | `pan`, `tilt`, `zoom` (−1…1, velocidade) |
| `stop`, `home` | – |
| `preset`, `preset_save` | `n` (1–9) |
| `photo` | `mode` (`single`, `burst`, `stack`, `timer3`, `timer10`) |
| `record`, `track`, `night`, `bgblur`, `look_next` | – (alternam) |
| `motion` | `kind` (`push_in`, `pull_out`, `pan_sweep`, `tilt_reveal`, `orbit_feel`, `whip_pan`, `crash_zoom`), `duration` |
| `zoom` | `value` (0–100 %), `duration` |
| `look` | `name` |

## Outras câmaras

| Câmara | Hoje | Como |
|---|---|---|
| **PTZ USB (UVC)** | ✅ completo | Motores reais via DirectShow. |
| **Telemóvel Android / iPhone** | ✅ como fonte + PTZ digital + tracking | Iriun Webcam, iVCam, DroidCam, Camo ou «Ligação ao Telemóvel» do Windows: aparecem como câmaras e o Alflor Studio aplica PTZ digital, tracking, looks e gravação. |
| **Canon EOS 5D (Mark III/IV)** | ✅ como fonte + PTZ digital + tracking | Saída HDMI limpa + placa de captura USB, ou *EOS Webcam Utility*. Focus peaking e medidor de nitidez ajudam a focar. |
| **Canon (controlo total)** | 🛠 em preparação | Disparo RAW, ISO, obturador, abertura e foco motorizado via Canon EDSDK (requer registo gratuito no programa de programadores Canon). |
| **PTZ de rede (VISCA/ONVIF/NDI)** | 🛠 planeado | Use o obs-ptz entretanto. |

## Limites honestos desta PTZ USB

- Resolução máxima **1080p30** (fotos também 1920×1080).
- **Sem foco, exposição nem íris** por USB e **sem infravermelhos** (não há controlo IR exposto).
  À noite: luz LED contínua suave + modo noite + empilhamento de fotos dão os melhores resultados.
- **Sem microfone**: use um microfone USB, de lapela sem fios (com recetor USB) ou o do telemóvel.
- **Não informa a posição** (lê sempre 0): o Alflor Studio guarda a posição que comanda.
- **Só obedece com vídeo ativo**: o Alflor Studio reenvia a posição quando o vídeo arranca.
- **Sem fios**: não é nativa. Opções: mini-PC ou Raspberry Pi junto da câmara a correr o Alflor Studio
  (controlo pelo telemóvel e stream pela rede), extensor USB-sobre-IP, ou power bank + telemóvel Android OTG.

## Arquitetura

```
ptz_studio/
  native/ptzbridge.cs   ponte DirectShow (IAMCameraControl, IAMVideoProcAmp) - processo próprio
  bridge.py             cliente da ponte (pedidos por linhas JSON)
  ptz.py                controlador: velocidade, absoluto, easing, travellings, PTZ digital
  capture.py            thread de captura → processamento → pré-visualização/gravação/vcam/remoto
  pipeline.py           ruído, noite, nitidez, looks, desfoque, zebras, peaking, histograma
  tracking.py           deteção/seguimento + AutoFramer (composição, plano, zoom automático)
  media.py              fotos, empilhamento, burst, gravador FFmpeg, câmara virtual
  remote.py, web/       servidor Wi-Fi: página do telemóvel, MJPEG, API
  integrations.py       OBS WebSocket, comando XInput, atualização automática
  app.py, widgets.py    interface PySide6
tests/                  teste de hardware, benchmark do gravador, teste da câmara virtual
```

## Testes

```powershell
.venv\Scripts\python.exe tests\hardware_test.py "UVC Camera"   # move a câmara! fps, calibração, fotos, vídeo, remoto, tracking
.venv\Scripts\python.exe tests\recorder_bench.py libx264
.venv\Scripts\python.exe tests\vcam_test.py
```

## Licença

MIT. Os plugins do OBS referidos são projetos independentes (GPL-2.0) e não estão incluídos neste repositório.
