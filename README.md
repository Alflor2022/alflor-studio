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
| **Rato** | Clique = centra a câmara nesse ponto · arrastar = mover · roda = zoom · **Ctrl + clique = seguir esse objeto** (contorno automático) · botão direito arrastado ou Shift + arrastar = escolher sujeito · duplo clique = ecrã inteiro. |
| **Teclado** | Setas/WASD, zoom +/−, presets 1–9, foto, gravação, tracking, ferramentas (ver abaixo). |
| **Comando Xbox** | Stick = mover, gatilhos = zoom, A foto, B gravar, X tracking, Y início, LB/RB presets. |
| **Presets** | 9 por câmara, com miniatura e nome, transição suave com curva (Linear, Suave, Cinema, Acelerar, Travar). |
| **Travelling** | Trajetórias ponto a ponto com duração, pausa e curva por ponto; reprodução única ou em ciclo; guardar/abrir. |
| **Movimentos de realizador** | Push-in, pull-out, panorâmica, revelação em tilt, arco, chicote (whip pan), crash zoom. |
| **Tracking com e sem IA** | Rosto (YuNet → Haar), pessoa (IA MediaPipe Pose com cabeça para *headroom*, ou HOG + CSRT sem IA) ou qualquer objeto escolhido (CSRT). **Identidade**: não salta para outra pessoa que passe. **Recuperação**: se o alvo se perde, procura-o em todo o ecrã (forma + cor) e a câmara volta à última posição onde o viu. Composição ao centro ou nos terços, *headroom* correto, plano (grande plano → plano geral) com zoom automático, reatividade ajustável, regresso ao início se perder o sujeito. Funciona com motores reais **ou** em PTZ digital. |
| **Calibração** | Mede automaticamente quantas unidades de pan/tilt correspondem ao ecrã e a ampliação ótica do zoom (correlação de fase + ORB), para cliques e tracking precisos. |
| **Imagem (hardware)** | Brilho, contraste, saturação, nitidez, tonalidade, balanço de brancos (manual/auto) e, se a câmara tiver, foco, exposição, íris, ganho. Gerados automaticamente para cada câmara. |
| **Gestos (IA)** | 👌 OK inicia o tracking **na pessoa que fez o gesto**, ✋ palma aberta para, ✌ vitória tira uma foto; 👍 e ✊ configuráveis. Gesto confirmado após ~0,6 s estável, com pausa de 2,5 s contra repetições. |
| **Chroma key** | Verde/azul/cor personalizada (conta-gotas Alt+clique, calibração automática), semelhança, suavidade, supressão de spill, contração e esbatimento do contorno, garbage matte, light wrap. Modos **Chroma**, **IA sem ecrã verde** e **Híbrido** (chroma nos contornos + IA para retirar objetos). Fundo transparente (PNG com alfa, **ProRes 4444**), cor, imagem, vídeo em ciclo ou desfocado. Acelerado pela GPU Intel (OpenCL) em 2 threads: 25 imagens/s a 1080p. |
| **Mesa de realização** | Até 8 entradas (câmara principal, outras câmaras USB/telemóvel/HDMI, imagens, vídeos, barras). PROGRAMA/PREVIEW, CUT, AUTO, T-bar, transições Mix/Dip/Wipe/Push, FTB, PiP, **cenário virtual** (chroma por cima de outra entrada), logótipo e oráculo. Multiview com tally, controlo PTZ da câmara em preview, gravação e câmara virtual do programa. |
| **Ajuste inteligente** | **Automático**: corrige sozinho exposição, balanço de brancos (white patch em zonas neutras), contraste e ruído conforme a luz do dia, de forma contínua e sem cintilação. **Igualar a uma referência**: dê uma foto editada (ex.: Lightroom) ou congele o frame atual — a app iguala cor (transferência Monge-Kantorovich) e tons (curva de luminância), como o *Color Match* do Premiere. Proteção de brilhos automática. |
| **Elementos no ecrã / gravação / foto** | Cada elemento (grelha, áreas seguras, histograma, zebras, peaking, caixa do tracking, mãos, informação da câmara, ícones de estado, REC, contagem, **timecode**, **data/hora**, **claquete**, **logótipo**) escolhe-se à parte para o ecrã, para a gravação (inclui câmara virtual/OBS) e para a foto. |
| **Imagem (software)** | Redução de ruído temporal (3 níveis), modo noite (levanta sombras), nitidez extra, looks (Vivo, Quente, Frio, Cinema, Suave, P&B, Alto contraste), desfoque de fundo que segue o sujeito, espelho/inversão. |
| **Monitorização** | Grelha de terços, áreas seguras, zebras (sobre-exposição), *focus peaking*, **histograma RGB com aviso de altas luzes/sombras cortadas**, **guias de corte** (1:1, 4:5 Instagram, 9:16 Reels/TikTok, 3:2, 2.39:1 cinema, foto tipo passe 35×45 com oval da cabeça) e **fantasma da última foto** para alinhar a seguinte. |
| **Fotografia** | PNG sem perdas, JPEG 100, TIFF ou **DNG RAW** (16 bits lineares, N frames empilhados — abre no Lightroom/Camera Raw com exposição e balanço de brancos editáveis; também **DNG + JPEG** com o mesmo nome). Todas as fotos levam **perfil sRGB, EXIF e XMP** (autor, direitos, cliente, palavras-chave, descrição, estrelas). Com chroma, guarda também a **máscara** e o **original**; **temporizador 3/5/10/15/30 s ou personalizado** (seta ▾ do botão Foto ou Shift+Espaço; clicar durante a contagem cancela); **burst** (escolhe automaticamente o frame mais nítido); **empilhamento noturno** (média de N frames → muito menos ruído). |
| **Gravação Log** | **S-Log3 em ProRes 422 HQ 10 bits** (.mov) com a imagem neutra (sem o look) para color grading no DaVinci/Premiere; LUT **Log→Rec709 (.cube)** exportável. 25/25 fps a 1080p. |
| **Fluxo de trabalho** | **Revisão de fotos** (Ctrl+Shift+V): estrelas 1–5, escolher/rejeitar, comparar lado a lado, filtrar e copiar as escolhidas; as estrelas ficam no XMP e o Lightroom lê-as. Cópia automática para a pasta de **Importação automática do Lightroom**, botão **Abrir Lightroom**, **exportar o look como LUT 3D (.cube)** para Premiere/DaVinci/Photoshop/OBS. **Perfis da interface**: Completo, Fotógrafo, Vídeo, Live. |
| **Vídeo** | **Gravar já, com temporizador (3/5/10/15/30 s ou personalizado) ou com paragem automática** (10 s … 10 min ou personalizada), contagem grande no ecrã com bip; FFmpeg: H.264 (x264 ou Intel QSV), H.265 QSV, MJPEG intra (edição); MP4/MOV/MKV; áudio de qualquer microfone; nomes `Projeto_Cena_T01_hhmmss` em pastas por data. |
| **OBS** | Liga por WebSocket (lê a password do OBS automaticamente): cenas, gravar, live, gravação sincronizada. |
| **Câmara virtual** | Envia a imagem processada (tracking digital, looks, ruído) para a «OBS Virtual Camera». |
| **Telemóvel / tablet** | Página web com joystick tátil, zoom, presets (toque longo guarda), foto, gravar, tracking, movimentos e imagem ao vivo. Basta ler o QR code. |
| **VLC / Stream Deck** | Stream MJPEG (`/stream.mjpg`) e API HTTP (`/api/cmd?a=photo`) para Bitfocus Companion/Stream Deck. |
| **Atualização automática** | Verifica as *releases* deste repositório e atualiza (git pull ou zip) com um clique ou automaticamente. |

## Instalação

### Noutro PC (recomendado)

Descarregue `AlflorStudio-<versão>-win64.zip` em [Releases](https://github.com/Alflor2022/alflor-studio/releases),
extraia e faça duplo clique em **`Instalar.cmd`** (sem administrador; cria atalhos). Inclui FFmpeg, modelos de IA e a
ponte PTZ — não é preciso Python. As atualizações seguintes instalam-se sozinhas a partir da app.

### A partir do código

Requisitos: Windows 10/11, Python 3.11–3.13, FFmpeg (`winget install Gyan.FFmpeg`).

```powershell
git clone https://github.com/Alflor2022/alflor-studio.git
cd alflor-studio
.\"Alflor Studio.bat"
```

Na primeira execução o `.bat` cria o ambiente `.venv` e instala as dependências. A ponte nativa
DirectShow (`ptzbridge.cs`) é compilada automaticamente com o compilador C# que já vem com o Windows.
Os modelos de IA descarregam-se no separador Tracking («Descarregar modelos de IA em falta»).

Gerar o executável e o zip: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` → `dist\`.

## Atalhos

```
Setas / WASD ...... mover (Shift = rápido, Ctrl = preciso)
+ / E / PgUp ...... zoom in      − / Q / PgDn ...... zoom out
Home / H .......... posição inicial   Esc ...... parar tudo
1 – 9 ............. ir para preset     Ctrl+1 – 9 ...... guardar preset
Espaço ............ foto     R ...... gravar     T ...... tracking
Shift+Espaço ...... foto c/ temporizador   Shift+R ...... gravar c/ temporizador
G grelha · Z zebras · P peaking · N noite · B desfoque · M espelho
F11 / duplo clique . ecrã inteiro     Ctrl+P ...... reproduzir travelling
K ................. chroma on/off      Ctrl+M ...... mesa de realização
Ctrl + clique ..... seguir esse objeto  Alt + clique . conta-gotas do chroma
Ctrl+Shift+V ...... revisão de fotos (1–5 estrelas · P escolher · X rejeitar · C comparar)
Mesa: 1–8 preview · Ctrl+1–8 programa · Espaço CUT · Enter AUTO · F FTB · P PiP
```

## Trabalhar com outras ferramentas

- **OBS Studio** – recomendados os plugins [obs-ptz](https://github.com/glikely/obs-ptz) (painel PTZ, também por UVC) e
  [obs-face-tracker](https://github.com/norihiro/obs-face-tracker). O Alflor Studio controla a câmara **mesmo quando o OBS a está a usar**
  (a ponte DirectShow funciona em paralelo). Para usar o tracking/looks do Alflor Studio dentro do OBS, ative a câmara virtual.
  Para ligar por WebSocket: OBS › Ferramentas › Definições do servidor WebSocket › Ativar.
- **Painel dentro do OBS** – OBS › Docas › Docas de browser personalizadas › URL `http://127.0.0.1:8765/dock?k=TOKEN`
  (tracking, gestos, câmara, presets, foto/REC, chroma e mesa, tudo a partir do OBS).
- **Gatilhos de cena do OBS** – etiquetas no nome da cena comandam o Alflor Studio quando ela vai para o ar:
  `[seguir]` `[parar]` `[preset 2]` `[inicio]` `[gravar]` `[parar gravacao]` `[cam 3]` `[chroma]` `[sem chroma]`.
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
| `photo` | `mode` (`single`, `burst`, `stack`), `delay` (segundos de temporizador) |
| `record` | `delay` (opcional: segundos de temporizador) |
| `grade` | `mode` (`auto`, `reference`, `off`) |
| `track`, `night`, `bgblur`, `look_next` | – (alternam) |
| `motion` | `kind` (`push_in`, `pull_out`, `pan_sweep`, `tilt_reveal`, `orbit_feel`, `whip_pan`, `crash_zoom`), `duration` |
| `zoom` | `value` (0–100 %), `duration` |
| `look` | `name` |
| `track_on`, `track_off`, `gestures`, `chroma`, `chroma_on`, `chroma_off`, `record_on`, `record_off` | – |
| `sw_program`, `sw_preview` | `n` (1–8) — mesa de realização |
| `sw_cut`, `sw_auto`, `sw_ftb` | – |

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
  tracking.py           deteção/seguimento com identidade e recuperação + AutoFramer
  ai.py, gestures.py    IA MediaPipe numa thread própria (mãos, pose, rostos, recorte) e gestos
  chroma.py             chroma key / recorte IA (GPU OpenCL, 2 threads)
  grading.py            ajuste automático e igualar a uma referência
  hud.py                elementos no ecrã / gravação / foto
  switcher.py, switcher_ui.py   mesa de realização multi-câmara
  models.py             modelos de IA (incluídos no .exe ou descarregados)
  media.py              fotos, empilhamento, burst, gravador FFmpeg (incl. Log), câmara virtual
  photo_formats.py      JPEG/PNG/TIFF com ICC+EXIF+XMP, DNG linear, S-Log3, LUTs .cube
  review.py             revisão/triagem de fotos (estrelas, escolher, comparar)
  remote.py, web/       servidor Wi-Fi: página do telemóvel, MJPEG, API
  integrations.py       OBS WebSocket, comando XInput, atualização automática
  app.py, widgets.py    interface PySide6
packaging/              PyInstaller (.exe), instalador e build.ps1
tests/                  unitários, chroma, ao vivo, hardware, gravador, câmara virtual
```

## Testes

```powershell
.venv\Scripts\python.exe tests\hardware_test.py "UVC Camera"   # move a câmara! fps, calibração, fotos, vídeo, remoto, tracking
.venv\Scripts\python.exe tests\recorder_bench.py libx264
.venv\Scripts\python.exe tests\vcam_test.py
.venv\Scripts\python.exe tests\test_units.py                    # gestos, mesa, gatilhos OBS (sem câmara)
.venv\Scripts\python.exe tests\test_chroma.py                   # precisão e desempenho do chroma
.venv\Scripts\python.exe tests\test_grading.py foto.png         # ajuste automático e referência
.venv\Scripts\python.exe tests\record_timer_test.py             # botões Gravar/Foto: temporizador, cancelar, paragem
.venv\Scripts\python.exe tests\hud_test.py                      # elementos no ecrã / gravação / foto
.venv\Scripts\python.exe tests\test_formats.py foto.png         # ICC/EXIF/XMP, DNG (LibRaw), S-Log3, LUTs (pip install -r requirements-dev.txt)
.venv\Scripts\python.exe tests\v03_test.py                      # Log, RAW, metadados, Lightroom, máscara, guias, perfis, revisão
.venv\Scripts\python.exe tests\live_test.py "UVC Camera" "Integrated Webcam"   # fps com chroma/IA e mesa
```

## Licença

MIT. Os plugins do OBS referidos são projetos independentes (GPL-2.0) e não estão incluídos neste repositório.
O pacote Windows inclui o FFmpeg (GPL) e modelos MediaPipe / OpenCV Zoo (Apache 2.0).
