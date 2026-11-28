"""Testes unitários sem câmara: gestos, porta de gestos, mesa de realização, gatilhos do OBS.

Uso: python tests\\test_units.py   (sai com código ≠ 0 se algum falhar)
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio.gestures import GestureGate, classify  # noqa: E402
from ptz_studio.integrations import scene_commands  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


# --- mãos sintéticas (21 pontos MediaPipe), palma virada para a câmara, mão vertical ---------
def hand(fingers: dict, thumb_tip=None, rot: float = 0.0, scale: float = 1.0, cx=0.5, cy=0.6) -> np.ndarray:
    """fingers: {'index': True/False, ...} esticado ou dobrado. Gera 21 pontos plausíveis."""
    lm = np.zeros((21, 3), np.float32)
    lm[0] = (0, 0, 0)                                       # pulso
    bases = {"index": -0.06, "middle": -0.02, "ring": 0.02, "pinky": 0.055}
    ids = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for f, bx in bases.items():
        i = ids[f]
        ext = fingers.get(f, False)
        lm[i] = (bx, -0.09, 0)                              # MCP
        if ext:
            lm[i + 1] = (bx, -0.13, 0); lm[i + 2] = (bx, -0.16, 0); lm[i + 3] = (bx, -0.19, 0)
        else:                                               # dobrado para a palma
            lm[i + 1] = (bx, -0.11, 0); lm[i + 2] = (bx, -0.085, 0); lm[i + 3] = (bx, -0.07, 0)
    # polegar
    lm[1] = (-0.04, -0.03, 0); lm[2] = (-0.07, -0.05, 0); lm[3] = (-0.09, -0.07, 0)
    lm[4] = thumb_tip if thumb_tip is not None else ((-0.11, -0.09, 0) if fingers.get("thumb") else (-0.04, -0.08, 0))
    c, s = np.cos(rot), np.sin(rot)
    R = np.array([[c, -s], [s, c]], np.float32)
    lm[:, :2] = (lm[:, :2] @ R.T) * scale + (cx, cy)
    return lm


print("Gestos")
all5 = dict(index=True, middle=True, ring=True, pinky=True, thumb=True)
check("palma aberta", classify(hand(all5)) == "palm", classify(hand(all5)))
ok_hand = hand(dict(middle=True, ring=True, pinky=True), thumb_tip=(-0.058, -0.185, 0))
ok_hand[8] = (-0.06, -0.18, 0) + np.array([0.5, 0.6, 0])  # ponta do indicador encostada ao polegar
ok_hand[4] = ok_hand[8] + (0.004, 0.003, 0)
ok_hand[6] = (-0.075, -0.14, 0) + np.array([0.5, 0.6, 0]); ok_hand[7] = (-0.07, -0.165, 0) + np.array([0.5, 0.6, 0])
check("OK 👌", classify(ok_hand) == "ok", classify(ok_hand))
check("vitória ✌", classify(hand(dict(index=True, middle=True))) == "victory")
check("punho ✊", classify(hand({})) == "fist", classify(hand({})))
thumbs = hand(dict(thumb=True), thumb_tip=(-0.02, -0.2, 0))
check("polegar para cima 👍", classify(thumbs) == "thumbs_up", classify(thumbs))
for deg in (-35, 25):
    r = np.radians(deg)
    check(f"palma rodada {deg}°", classify(hand(all5, rot=r)) == "palm")
check("palma pequena (longe da câmara)", classify(hand(all5, scale=0.3)) == "palm")
check("indicador só (sem gesto)", classify(hand(dict(index=True))) is None, classify(hand(dict(index=True))))

print("Porta de gestos (estabilidade + pausa)")
g = GestureGate(hold=0.6, cooldown=2.5)
t0 = 100.0
ev = None
for i in range(10):              # 0,9 s de OK estável a ~11 Hz
    ev = g.update([("ok", (0.5, 0.5), (0.4, 0.4, 0.1, 0.1))], now=t0 + i * 0.09) or ev
check("OK mantido 0,6 s dispara", ev is not None and ev.gesture == "ok")
ev2 = None
for i in range(10):
    ev2 = g.update([("ok", (0.5, 0.5), (0.4, 0.4, 0.1, 0.1))], now=t0 + 1.0 + i * 0.09) or ev2
check("não repete durante a pausa de 2,5 s", ev2 is None)
g2 = GestureGate()
flick = None
for i, ges in enumerate(["ok", None, "palm", None, "ok", None, "victory", None]):
    flick = g2.update([(ges, (0.5, 0.5), (0, 0, 0.1, 0.1))], now=200 + i * 0.1) or flick
check("gestos instáveis não disparam", flick is None)

print("Gatilhos de cena do OBS")
check("[seguir] [preset 2]", scene_commands("Entrevista [seguir] [preset 2]") == [{"a": "track_on"}, {"a": "preset", "n": 2}])
check("[Parar] com maiúsculas", scene_commands("Fim [Parar]") == [{"a": "track_off"}])
check("[cam 3] e [chroma]", scene_commands("Virtual [cam 3] [chroma]") == [{"a": "sw_program", "n": 3}, {"a": "chroma_on"}])
check("acentos: [início]", scene_commands("Abertura [início]") == [{"a": "home"}])
check("cena sem etiquetas", scene_commands("Cena normal") == [])

print("Mesa de realização")
from ptz_studio.switcher import OUT_H, OUT_W, ColorSource, SwitcherEngine  # noqa: E402

eng = SwitcherEngine()
eng.set_source(0, ColorSource("Vermelho", (0, 0, 255)))
eng.set_source(1, ColorSource("Verde", (0, 255, 0)))
eng.set_source(2, ColorSource("Barras", bars=True))
mv = []
eng.on_multiview = mv.append
time.sleep(0.4)
px = lambda: tuple(int(v) for v in eng.program_frame[OUT_H // 2, OUT_W // 2])


def until(cond, timeout=1.5):
    """Espera pela condição (o motor desenha a 30 fps; com o PC ocupado pode atrasar)."""
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if cond():
            return True
        time.sleep(0.03)
    return cond()


check("programa = entrada 1 (vermelho)", px() == (0, 0, 255), str(px()))
eng.cut()
check("CUT → entrada 2 (verde)", until(lambda: px() == (0, 255, 0)), str(px()))
check("após CUT a pré-visualização é a anterior", eng.st.preview == 0)
eng.st.duration = 0.6
eng.auto(); time.sleep(0.3)
mid = px()
check("AUTO a meio mistura as duas", 30 < mid[1] < 230 and 30 < mid[2] < 230, str(mid))
# esperar o FIM da transição (barramentos trocados), não só o píxel: a 99,9 % já é vermelho puro
check("AUTO termina no vermelho", until(lambda: not eng.st.running and eng.st.program == 0 and px() == (0, 0, 255)), str(px()))
eng.set_tbar(0.5); time.sleep(0.12)
check("T-bar a 50 % mistura", 60 < px()[1] < 200, str(px()))
eng.set_tbar(1.0)
check("T-bar a 100 % troca os barramentos", until(lambda: eng.st.program == 1 and px() == (0, 255, 0)), str(px()))
eng.toggle_ftb()
check("FTB escurece para preto", until(lambda: max(px()) < 10, 3.0), str(px()))
eng.toggle_ftb(); time.sleep(1.6)
eng.st.pip_on, eng.st.pip_source, eng.st.pip_corner = True, 2, "tr"; time.sleep(0.15)
corner = tuple(int(v) for v in eng.program_frame[int(OUT_H * 0.1), int(OUT_W * 0.9)])
check("PiP aparece no canto superior direito", corner != (0, 255, 0), str(corner))
eng.st.pip_on = False
for kind in ("dip", "wipe_lr", "wipe_tb", "push"):
    eng.st.transition = kind
    eng.st.duration = 0.3
    eng.auto(); time.sleep(0.6)
check("todas as transições terminam", not eng.st.running)
check("multiview gerado (1280×720)", bool(mv) and mv[-1].shape == (720, 1280, 3))
time.sleep(1.1)
check(f"mesa a ~30 fps ({eng.stats['fps']:.0f} fps, {eng.stats['ms']:.1f} ms/frame)", eng.stats["fps"] > 25)
eng.stop()

print()
print("RESULTADO:", "TUDO OK" if not failures else f"{len(failures)} FALHA(S): {failures}")
sys.exit(1 if failures else 0)
