# Alflor Studio — Documento do projeto

> Comando virtual profissional para câmaras PTZ USB, DSLR e telemóveis no Windows, com tracking por IA,
> gestos, chroma key, ajuste inteligente de imagem, mesa de realização multi-câmara e integração com OBS.

| | |
|---|---|
| **Versão** | 0.2.0 |
| **Repositório** | [github.com/Alflor2022/alflor-studio](https://github.com/Alflor2022/alflor-studio) (público, MIT) |
| **Pasta local** | `E:\Projetos\alflor-studio` |
| **Instalador** | `AlflorStudio-0.2.0-win64.zip` (241,9 MB · instalado 603 MB) |
| **Tecnologia** | Python 3.13 · PySide6 (Qt) · OpenCV 4.14 contrib · MediaPipe 1.2.3 · FFmpeg · C# (DirectShow) · PyInstaller |
| **Câmara de referência** | PTZ USB UVC (VID 27C2 / PID 052F), 1080p30, pan ±2000 · tilt ±900 · zoom 0–99 |

---

## 1. Objetivo

Dar a um estúdio pequeno (fotografia, vídeo, lives, formação) o controlo que normalmente exige
hardware caro: comando PTZ completo pelo teclado, rato, comando de jogo e telemóvel; tracking
automático do apresentador; chroma; correção automática da imagem; e realização com várias
câmaras, tudo num único programa instalável noutro PC sem conhecimentos técnicos.

## 2. Histórico

| Versão | O que trouxe |
|---|---|
| 0.1.0 | Ponte DirectShow nativa, PTZ por teclado/rato/joystick/Xbox/telemóvel, presets, travellings, tracking clássico, foto/vídeo, OBS WebSocket, câmara virtual, atualização automática |
| 0.1.1 | Nome «Alflor Studio», migração automática de definições |
| 0.1.2 | Correções da demo ao vivo: tracking físico estável, Ctrl+clique para seguir, roda do rato, teclas «presas» ao perder o foco |
| **0.2.0** | IA MediaPipe e gestos, recuperação do alvo, chroma key (GPU), mesa de realização multi-câmara, painel e gatilhos do OBS, temporizadores de foto e vídeo, ajuste inteligente (auto e referência), elementos ecrã/gravação/foto, executável e instalador |

## 3. Funções

### 3.1 Controlo da câmara
- **Teclado**: setas/WASD (Shift rápido, Ctrl preciso), zoom +/− ou E/Q, Home, presets 1–9 (Ctrl guarda).
- **Rato**: clique centra a câmara nesse ponto · arrastar move · roda faz zoom · Ctrl+clique segue o objeto ·
  botão direito (ou Shift+arrastar) escolhe o sujeito · Alt+clique é o conta-gotas do chroma.
- **Joystick no ecrã, comando Xbox (XInput) e telemóvel** (página web por QR code).
- **Presets** com miniatura e transições com curvas (Linear, Suave, Cinema, Acelerar, Travar).
- **Travellings** ponto a ponto e **movimentos de realizador**: push-in, pull-out, panorâmica, revelação, arco, chicote, crash zoom.
- **PTZ digital** (recorte inteligente) para câmaras sem motores: telemóveis, Canon via HDMI, webcams.
- **Calibração automática** do campo de visão (correlação de fase) para cliques e tracking precisos.

### 3.2 Tracking
- **Rosto** (YuNet → Haar), **pessoa** (MediaPipe Pose com cabeça para *headroom*, ou HOG+CSRT sem IA), **qualquer objeto** (CSRT).
- **Identidade**: não salta para outra pessoa que passe perto (posição prevista + histograma de cor).
- **Recuperação**: se o alvo se perde, procura-o em todo o ecrã (forma em 5 escalas + cor no miolo da caixa) e
  a câmara volta à última posição onde o viu.
- **Enquadramento**: centro ou terços, planos (grande plano → plano geral), zoom automático, reatividade ajustável.
- **Gestos (IA)**: 👌 OK começa a seguir **quem fez o gesto**, ✋ palma para, ✌ tira foto, 👍/✊ configuráveis;
  confirmação após ~0,6 s estável e pausa anti-repetição de 2,5 s.

### 3.3 Imagem
- **Hardware** (gerado para cada câmara): brilho, contraste, tonalidade, saturação, nitidez, balanço de brancos manual/auto,
  e foco/exposição/íris quando a câmara os expõe.
- **Software**: redução de ruído temporal, modo noite, nitidez, 8 looks, desfoque de fundo que segue o sujeito, espelho/inversão.
- **Ajuste inteligente** — *Automático*: exposição, balanço de brancos (white patch em zonas neutras), contraste, saturação e
  ruído, adaptado continuamente à luz do dia sem cintilação. *Igualar a uma referência*: dá-se uma foto editada
  (ex.: Lightroom) ou congela-se o frame atual e a app iguala cor (transferência de Monge-Kantorovich) e tons (curva de
  luminância), como o *Color Match* do Premiere. Proteção de gama: nunca queima mais do que a imagem original.
- **Chroma key**: verde/azul/personalizado, calibração automática, semelhança, suavidade, spill, contração, esbatimento,
  garbage matte, light wrap; modos Chroma, IA sem ecrã e Híbrido; fundo transparente (PNG alfa, ProRes 4444), cor,
  imagem, vídeo em ciclo ou desfocado; vistas Composto/Matte/Original.
- **Elementos no ecrã / gravação / foto**: 16 elementos (grelha, áreas seguras, histograma, zebras, peaking, tracking,
  mãos, info, estado, REC, contagem, timecode, data/hora, claquete, logótipo), cada um escolhido à parte por destino.

### 3.4 Captura
- **Foto**: PNG/JPEG 100/TIFF; agora ou com temporizador (3/5/10/15/30 s ou personalizado; simples, burst ou noite);
  burst escolhe o frame mais nítido; empilhamento noturno reduz ruído.
- **Vídeo**: agora, com temporizador ou com paragem automática (10 s … 10 min); H.264 (x264/QSV), H.265 QSV, MJPEG intra,
  ProRes 4444 com alfa; áudio de qualquer microfone; nomes `Projeto_Cena_T01_hhmmss` em pastas por data.

### 3.5 Mesa de realização (estilo ATEM)
8 entradas (câmara principal, outras câmaras USB/telemóvel/HDMI, imagens, vídeos, barras), barramentos PROGRAMA/PREVIEW,
CUT, AUTO, T-bar, transições Mix/Dip/Wipe/Push, FTB, PiP, cenário virtual (chroma por cima de outra entrada), logótipo,
oráculo, multiview com tally, PTZ da câmara em preview, gravação e câmara virtual do programa, atalhos de teclado.

### 3.6 Ligações
- **OBS**: WebSocket (cenas, gravar, live), **painel dentro do OBS** (`/dock`), **gatilhos por etiquetas no nome das cenas**
  (`[seguir]`, `[parar]`, `[preset 2]`, `[cam 3]`, `[chroma]`, `[gravar]`…), plugins obs-ptz e obs-face-tracker.
- **Câmara virtual** (Zoom, Teams, Meet, OBS), **VLC** (MJPEG), **Stream Deck / Bitfocus Companion** (API HTTP),
  **telemóvel** (QR code com token), **cloud** (pasta OneDrive), **atualização automática** pelas releases do GitHub.

## 4. Arquitetura

```
                ┌──────────────── interface PySide6 (app.py, widgets.py, switcher_ui.py) ────────────────┐
teclado/rato ──►│  PTZController (ptz.py) ── PhysicalBackend ──► ponte C# (ptzbridge.exe, DirectShow)   │
Xbox/telemóvel  │                         └─ DigitalBackend (recorte)                                    │
OBS/Companion ─►│  AutoFramer ◄── SubjectTracker (tracking.py) ◄── AIWorker (ai.py, MediaPipe, thread)  │
                └───────────────────────────────▲─────────────────────────────────────────────────────────┘
                                                │ frames + metadados
câmara ──► CaptureThread (capture.py) ──► Pipeline (pipeline.py):
              PTZ digital → espelho → noite → ruído → [chroma: 2 threads, GPU] → nitidez →
              [ajuste inteligente] → look → desfoque
          ├─► pré-visualização (elementos «ecrã»)
          ├─► HudBurner (hud.py: elementos «gravação») ─► Recorder (FFmpeg) · VirtualCam
          ├─► fotos (elementos «foto») · servidor remoto (remote.py: telemóvel, VLC, API, /dock)
          └─► SwitcherEngine (switcher.py) ◄── outras câmaras / imagens / vídeos ─► programa
```

| Módulo | Função |
|---|---|
| `native/ptzbridge.cs`, `bridge.py` | Ponte DirectShow (IAMCameraControl / IAMVideoProcAmp) em processo próprio; funciona com o OBS a usar a câmara |
| `ptz.py` | Velocidade, posição absoluta, easing, travellings, PTZ digital |
| `capture.py` | Captura, processamento, pré-visualização, destinos (gravação, câmara virtual, alfa) |
| `pipeline.py` | Ruído, noite, chroma assíncrono, ajuste, looks, desfoque, zebras, peaking, histograma |
| `tracking.py`, `ai.py`, `gestures.py` | Deteção, identidade, recuperação, enquadramento, IA e gestos |
| `chroma.py`, `grading.py`, `hud.py` | Chroma key, ajuste inteligente, elementos sobrepostos |
| `switcher.py`, `switcher_ui.py` | Mesa de realização |
| `media.py` | Fotos, burst, empilhamento, gravador FFmpeg, câmara virtual |
| `remote.py`, `web/` | Servidor Wi-Fi, página do telemóvel, painel do OBS, API |
| `integrations.py` | OBS (pedidos e eventos), comando Xbox, atualização automática |
| `models.py`, `config.py` | Modelos de IA, definições persistentes (`%APPDATA%\AlflorStudio`) |
| `packaging/` | PyInstaller, instalador por utilizador, `build.ps1` |

## 5. Decisões técnicas (com medições)

| Decisão | Porquê | Medido |
|---|---|---|
| Ponte DirectShow em C# compilada com o `csc.exe` do Windows | Controla a PTZ mesmo com o OBS a usar a câmara; sem dependências | Pan/tilt/zoom e imagem a funcionar em paralelo com outra app |
| Posição mantida pela app | A câmara lê sempre 0 e só obedece com vídeo ativo | Reenvio da posição quando o vídeo arranca |
| Tracking físico com espera de estabilização | A imagem chega atrasada face ao motor: correções acumulavam e o alvo escorregava | Almofada centrada (y 0,68 → 0,53); 7 perdas e 7 recuperações com a PTZ a mexer |
| Cor de referência só do miolo da caixa | Seleções à mão incluem fundo; a procura rejeitava o objeto certo | Procura offline: falhava → encontra; não inventa objetos em frame vazio |
| IA numa thread própria | Não baixa os fps da câmara | Mãos 14 ms · pose 20 ms · rostos 2 ms · recorte 8 ms (sem carga) |
| Chroma em GPU (OpenCL) com 2 threads; light wrap desligado por omissão | Medir imagens **novas** por segundo, não fps de captura | CPU 11 img/s · GPU 22 · GPU sem light wrap **25** (máximo da câmara) |
| Chroma: calibração limitada pela distância ao cinzento neutro | Uma semelhança larga recortava paredes brancas | Sintético: IoU 0,988, 0 % de fundo restante |
| Ajuste: níveis em RGB, gama só em luminância, proteção de gama | Curva por canal queimava o verde (43 %); ajuste em luminância «lavava» | Referência: distância 23,1 → 0,4; outra cena: 0,67 % no limite |
| Gravação x264 *veryfast* por omissão | O QSV deste iGPU perdia ~6 % dos frames | 0 frames perdidos a 1080p30 |
| Teste do codificador em segundo plano | A 1.ª gravação atrasava ~1,5 s | Temporizador de 2 s → gravação aos 2,08 s |
| Elementos queimados com textos em cache | Texto Qt redesenhado a cada frame era caro | 8,6 ms por frame com a app inteira a correr |
| Definições só de leitura em modo de teste | Os testes gravavam valores de teste na configuração real | Ficheiro de definições idêntico antes/depois dos testes |

## 6. Testes

| Teste | Câmara | Cobre | Resultado |
|---|---|---|---|
| `tests/test_units.py` | não | gestos (rodados, longe), porta de gestos, gatilhos do OBS, mesa (CUT, AUTO, T-bar, FTB, PiP, transições, fps) | **Tudo OK** |
| `tests/test_chroma.py` | não | precisão (cena sintética), desempenho, foto real | **OK** (IoU 0,988) |
| `tests/test_grading.py` | não | auto (escura, amarelada, sem contraste, imagem boa), referência (mesmo plano / outra cena), desempenho, suavidade | **Tudo OK** |
| `tests/record_timer_test.py` | sim | botões Gravar/Foto: agora, temporizador, cancelar, paragem automática, API | **Tudo OK** |
| `tests/hud_test.py` | sim | elementos por destino na gravação e na foto, custo | **Tudo OK** |
| `tests/hardware_test.py` | sim (move a PTZ) | fps, calibração, fotos, vídeo, remoto, tracking com recuperação | **OK** |
| `tests/live_test.py` | sim (2 câmaras) | fps com chroma/IA, mesa com duas câmaras | mesa 26,8 fps; 2.ª câmara 30 fps |
| `tests/recorder_bench.py`, `tests/vcam_test.py` | não | gravador por codec, câmara virtual | OK |

## 7. Instalação e distribuição

- **Noutro PC**: extrair `AlflorStudio-0.2.0-win64.zip` → `Instalar.cmd` (sem administrador; atalhos no Ambiente de Trabalho e
  Menu Iniciar; desinstala-se nas Definições do Windows; definições e gravações mantidas). Inclui FFmpeg, modelos de IA e ponte PTZ.
- **A partir do código**: `git clone` → `Alflor Studio.bat` (cria o `.venv` e instala as dependências).
- **Gerar o instalador**: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` → `dist\`.
- **Atualizações**: a app consulta as releases; a versão instalada descarrega o `…-win64.zip` e troca os ficheiros ao fechar.

## 8. Guia rápido

| Ação | Como |
|---|---|
| Mover / zoom | Setas ou WASD · E/Q · roda do rato · joystick |
| Ir / guardar preset | 1–9 / Ctrl+1–9 |
| Foto / com temporizador | Espaço / Shift+Espaço ou seta ▾ do botão Foto |
| Gravar / com temporizador | R / Shift+R ou seta ▾ do botão Gravar |
| Seguir / parar | T, botão Seguir, Ctrl+clique num objeto, gesto 👌 / ✋ |
| Chroma | K · Alt+clique = conta-gotas |
| Mesa de realização | Ctrl+M · 1–8 preview · Ctrl+1–8 programa · Espaço CUT · Enter AUTO · F FTB |
| Painel no OBS | Docas › Docas de browser personalizadas › `http://127.0.0.1:8765/dock?k=TOKEN` |
| Comandos pelas cenas do OBS | Etiquetas no nome da cena: `[seguir]` `[parar]` `[preset 2]` `[cam 3]` `[chroma]` `[gravar]` |

## 9. Limites conhecidos

- **PTZ USB**: 1080p30; sem foco, exposição, íris, infravermelhos nem microfone; não informa a posição; só obedece com vídeo
  ativo; motor de zoom lento; a exposição automática baixa os fps com pouca luz.
- **Gestos**: validados em testes sintéticos e na deteção de pessoa real (81 % das amostras), mas **ainda não testados ao vivo
  com alguém a fazer o gesto** em frente à câmara.
- **Tracking**: um desvio brusco de meio ecrã faz sair o alvo; a recuperação volta à última posição e procura, mas pode falhar
  se o objeto mudar muito de aspeto.
- **Chroma Híbrido**: a IA recorta só pessoas (objetos à frente do ecrã ficam translúcidos — intencional).
- **Ajuste automático**: com um ecrã verde muito claro, a proteção de gama limita a correção (ex.: dominante amarela só
  parcialmente corrigida para não queimar o verde).
- **OBS**: o servidor WebSocket tem de ser ativado no OBS; o controlo pelo telemóvel pede autorização na Firewall do Windows.
- **Pacote**: 603 MB instalado (FFmpeg completo 217 MB, IA, Qt).

## 10. Próximos passos sugeridos

1. Teste ao vivo dos gestos 👌/✋/✌ com uma pessoa em frente à câmara.
2. Controlo completo da Canon EOS 5D (Canon EDSDK: disparo RAW, ISO, obturador, abertura, foco motorizado).
3. PTZ de rede (VISCA-over-IP / ONVIF / NDI) e várias PTZ com motores na mesa.
4. Reduzir o pacote (FFmpeg «essentials», exclusões no PyInstaller) e assinar o executável.
5. Áudio na mesa de realização (mistura por entrada) e gravação ISO de cada câmara.
