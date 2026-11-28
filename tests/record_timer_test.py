"""Testa o botão Gravar: gravar já, temporizador, cancelar e paragem automática (câmara real).

Uso: python tests\\record_timer_test.py [pasta de saída]
Não altera as definições guardadas (modo de teste) e grava na pasta indicada.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ptz_studio.app import MainWindow  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
fails: list[str] = []


def wait(s: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(s * 1000), loop.quit)
    loop.exec()


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


app = QApplication(sys.argv)
win = MainWindow(test_mode=True)
win.settings["output_dir"] = str(OUT)
win.settings["project"], win.settings["scene"] = "TesteREC", "Timer"
win.show()
for _ in range(40):
    wait(0.5)
    if win.capture and win.capture.latest() is not None:
        break
check("imagem da câmara", win.capture is not None and win.capture.latest() is not None)
menu_labels = [a.text() for a in win.btn_rec_menu.menu().actions() if a.text()]
check("menu do botão Gravar tem 'Gravar agora' e temporizadores 3/5/10 s",
      any("agora" in t for t in menu_labels) and all(any(f"{s} s" in t for t in menu_labels) for s in (3, 5, 10)),
      "; ".join(menu_labels[:8]))

# 1) cancelar durante a contagem
win.record_with_timer(3)
wait(1.2)
check("contagem visível (3 → 2)", win.preview.countdown == 2 and "REC" in win.preview.countdown_label, f"{win.preview.countdown}")
win._rec_clicked()
wait(2.5)
check("clicar durante a contagem cancela (não grava)", win.recorder is None and win.preview.countdown == 0)

# 2) temporizador 2 s + paragem automática aos 3 s
win._set_auto_stop(3)
t0 = time.monotonic()
win.record_with_timer(2)
started = None
while time.monotonic() - t0 < 10:
    wait(0.1)
    if win.recorder is not None and started is None:
        started = time.monotonic() - t0
    if started is not None and win.recorder is None:
        break
stopped = time.monotonic() - t0
check("começa a gravar ao fim de ~2 s", started is not None and 1.6 < started < 2.8, f"{started and round(started, 2)} s")
check("para sozinha ~3 s depois", started is not None and 2.6 < stopped - started < 3.8, f"{round(stopped - (started or 0), 2)} s")
wait(3)  # o FFmpeg fecha o ficheiro
vids = sorted(OUT.rglob("TesteREC_Timer_*.mp4"))
check("ficheiro de vídeo criado", bool(vids), str(vids[-1].name) if vids else "")
if vids and win.ffmpeg:
    probe = subprocess.run([win.ffmpeg.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg.EXE", "ffprobe.exe"),
                            "-v", "error", "-show_entries", "format=duration", "-of", "json", str(vids[-1])],
                           capture_output=True, text=True)
    dur = float(json.loads(probe.stdout or "{}").get("format", {}).get("duration", 0))
    check("duração do vídeo ≈ 3 s", 2.5 < dur < 3.8, f"{dur:.2f} s")

# 3) gravar agora (sem limite) e parar à mão
win._set_auto_stop(0)
win._rec_clicked()
wait(1.5)
check("Gravar agora começa imediatamente", win.recorder is not None)
win._rec_clicked()
wait(0.5)
check("segundo clique para", win.recorder is None)

# 4) pedido remoto com temporizador (telemóvel / Stream Deck)
win._remote_command({"a": "record", "delay": "2"})
wait(0.5)
check("API: record&delay=2 inicia contagem", win._rec_cd_timer.isActive() and win.preview.countdown >= 1)
win.cancel_record_timer()

# 5) foto com temporizador (mesmas opções) e cancelar
photo_labels = [a.text() for a in win.btn_photo_menu.menu().actions() if a.text()]
check("menu da Foto tem 'Foto agora' e temporizadores 3/5/10 s",
      any("agora" in t for t in photo_labels) and all(any(f"{s} s" in t for t in photo_labels) for s in (3, 5, 10)))
n0 = len(list(OUT.rglob("TesteREC_*.png")))
win.photo_with_timer(3)
wait(1.2)
check("contagem da foto visível", win.preview.countdown == 2 and "FOTO" in win.preview.countdown_label)
win._photo_clicked()
wait(2.5)
check("clicar durante a contagem da foto cancela", len(list(OUT.rglob("TesteREC_*.png"))) == n0)
t0 = time.monotonic()
win.photo_with_timer(2)
while time.monotonic() - t0 < 6 and len(list(OUT.rglob("TesteREC_*.png"))) == n0:
    wait(0.1)
took = time.monotonic() - t0
check("foto tirada ao fim de ~2 s", len(list(OUT.rglob("TesteREC_*.png"))) == n0 + 1 and 1.8 < took < 3.5, f"{took:.2f} s")
win._remote_command({"a": "photo", "delay": "2"})
wait(0.4)
check("API: photo&delay=2 inicia contagem", win._photo_cd_timer.isActive())
win.cancel_photo_timer()
win.close()
print("RESULTADO:", "TUDO OK" if not fails else f"{len(fails)} FALHA(S): {fails}")
sys.exit(1 if fails else 0)
