"""Teste de fumo da interface (câmara real): percorre separadores, perfis, menus, janelas e botões
seguros, apanha exceções nos slots do Qt e guarda uma captura de cada separador.

Uso: python tests\\ui_smoke_test.py [pasta de saída]     (não move a câmara, não muda a configuração)
"""
import hashlib
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QPushButton, QSlider, QSpinBox  # noqa: E402

from ptz_studio.app import MainWindow  # noqa: E402
from ptz_studio.config import CONFIG_FILE  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
OUT.mkdir(parents=True, exist_ok=True)
fails, errors = [], []
cfg_hash = hashlib.sha256(CONFIG_FILE.read_bytes()).hexdigest() if CONFIG_FILE.exists() else ""


def hook(et, ev, tb):   # exceções dentro de slots do Qt não param o programa: registá-las
    errors.append("".join(traceback.format_exception(et, ev, tb)))


sys.excepthook = hook


def wait(s):
    lp = QEventLoop()
    QTimer.singleShot(int(s * 1000), lp.quit)
    lp.exec()


def until(cond, timeout=15.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        wait(0.1)
    return cond()


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


app = QApplication(sys.argv)
win = MainWindow(test_mode=True)
win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
win.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
win.settings.update(output_dir=str(OUT), project="TesteUI", scene="Fumo", take=1)
win.resize(1600, 950)
win.show()
check("câmara aberta", until(lambda: win.capture is not None and win.capture.latest() is not None, 25))
wait(2)

# 1) cada separador: mostrar, capturar, contar controlos
for i in range(win.tabs.count()):
    name = win.tabs.tabText(i)
    win.tabs.setCurrentIndex(i)
    wait(0.6)
    page = win.tabs.widget(i)
    n = sum(len(page.findChildren(c)) for c in (QPushButton, QCheckBox, QComboBox, QSlider, QSpinBox))
    win.grab().save(str(OUT / f"tab_{i}_{name}.png"))
    check(f"separador «{name}»", n > 0, f"{n} controlos")

# 2) perfis
for key in win.UI_PROFILES:
    win.cmb_profile.setCurrentIndex(win.cmb_profile.findData(key))
    wait(0.3)
    vis = [win.tabs.tabText(i) for i in range(win.tabs.count()) if win.tabs.isTabVisible(i)]
    check(f"perfil {key}", len(vis) >= 5 and win.tabs.isTabVisible(win.tabs.currentIndex()), ", ".join(vis))
win.cmb_profile.setCurrentIndex(win.cmb_profile.findData("completo"))

# 3) menus de foto e gravação (só abrir e ler; não disparar)
for btn in (win.btn_photo_menu, win.btn_rec_menu):
    acts = [a.text() for a in btn.menu().actions() if a.text()]
    check(f"menu {btn.toolTip()[:30]}", len(acts) >= 4, f"{len(acts)} ações")

# 4) alternar caixas de opção em todos os separadores (e repor) — apanha slots com erros
skip = ("Gestos", "Seguir", "Ligar", "Remoto", "telem", "OBS", "virtual", "atualiz", "Câmara virtual", "Gamepad", "comando")
toggled = 0
for i in range(win.tabs.count()):
    for c in win.tabs.widget(i).findChildren(QCheckBox):
        if not c.isEnabled() or any(s.lower() in (c.text() + c.toolTip()).lower() for s in skip):
            continue
        if win.grp_hw.isAncestorOf(c):   # nunca mexer no hardware da câmara (fica gravado na câmara)
            continue
        v = c.isChecked()
        c.setChecked(not v)
        wait(0.05)
        c.setChecked(v)
        toggled += 1
wait(1)
check("caixas de opção alternadas sem erros", not errors, f"{toggled} caixas")

# 5) combos: percorrer todas as opções e repor
cycled = 0
for i in range(win.tabs.count()):
    for cb in win.tabs.widget(i).findChildren(QComboBox):
        if not cb.isEnabled() or cb.count() < 2 or cb is win.cmb_device or win.grp_hw.isAncestorOf(cb):
            continue
        cur = cb.currentIndex()
        for j in range(cb.count()):
            cb.setCurrentIndex(j)
            wait(0.03)
        cb.setCurrentIndex(cur)
        cycled += 1
wait(1.5)
check("listas percorridas sem erros", not errors, f"{cycled} listas")
ok_img = until(lambda: win.capture is not None and win.capture.latest() is not None and win.capture.fps > 5, 20)
check("imagem continua a chegar (câmara reaberta)", ok_img, f"{win.capture.fps:.1f} fps" if win.capture else "sem captura")

# 5b) hardware da câmara: desligar «Auto» do balanço de brancos usa o padrão (não o extremo reportado);
#     «Repor câmara» volta ao automático. A câmara fica SEMPRE reposta no fim.
from PySide6.QtWidgets import QFormLayout, QSlider as _QS  # noqa: E402
wb_chk = wb_sl = reset_all = None
form = win.hw_form
for r in range(form.rowCount()):
    lab = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
    fld = form.itemAt(r, QFormLayout.ItemRole.FieldRole) or form.itemAt(r, QFormLayout.ItemRole.SpanningRole)
    if lab and lab.widget() and "Balanço" in lab.widget().text() and fld and fld.layout():
        ws = [fld.layout().itemAt(i).widget() for i in range(fld.layout().count())]
        wb_chk = next((w for w in ws if isinstance(w, QCheckBox)), None)
        wb_sl = next((w for w in ws if isinstance(w, _QS)), None)
    if fld and fld.widget() and isinstance(fld.widget(), QPushButton) and "Repor câmara" in fld.widget().text():
        reset_all = fld.widget()
check("controlos de hardware presentes (balanço de brancos + repor)", wb_chk is not None and reset_all is not None)
if wb_chk is not None and reset_all is not None and win.bridge:
    try:
        if not wb_chk.isChecked():
            wb_chk.setChecked(True)
            wait(1)
        wb_chk.setChecked(False)          # manual: deve aplicar o valor do deslizador (padrão se o auto reportava extremo)
        wait(1.5)
        r = win.bridge.info().get("amp", {}).get("whitebalance", {})
        check("Auto desligado aplica um valor sensato", r.get("flags") == 2 and r.get("cur") != r.get("max"),
              f"{r.get('cur')} K (deslizador {wb_sl.value()})")
    finally:
        reset_all.click()
        wait(1.5)
        r = win.bridge.info().get("amp", {}).get("whitebalance", {})
        check("«Repor câmara» volta ao automático", r.get("flags") == 1 and wb_chk.isChecked(), str(r))

# 6) chroma, mesa, revisão, ajuda
win.set_chroma(True)
wait(2)
win.set_chroma(False)
win.open_switcher()
wait(3)
sw = getattr(win, "switcher", None)
if sw is not None:
    sw.grab().save(str(OUT / "mesa.png"))
    sw.close()
    wait(1)
check("mesa abre e fecha", sw is not None and not errors)
win.open_review()
wait(1.5)
win._review_win.grab().save(str(OUT / "revisao.png"))
win._review_win.close()
check("revisão abre", not errors)

# 7) foto e gravação rápidas pela interface
saved = []
win.photo_saved.connect(saved.append)
win.take_photo("single")
check("foto pela interface", until(lambda: saved, 15), saved[0] if saved else "")
win.toggle_record(True)
wait(3)
rec = win.recorder
win.toggle_record(False)
check("gravação pela interface", rec is not None and until(lambda: rec.path.exists() and rec.proc.poll() is not None, 20),
      f"{rec.frames} frames, {rec.dropped} descartados" if rec else "")

win.close()
wait(1)
for e in errors:
    print("\nEXCEÇÃO NUM SLOT:\n" + e)
check("nenhuma exceção nos slots", not errors, f"{len(errors)} exceções")
new_hash = hashlib.sha256(CONFIG_FILE.read_bytes()).hexdigest() if CONFIG_FILE.exists() else ""
check("configuração do utilizador intacta", new_hash == cfg_hash)
print("\nRESULTADO:", "TUDO OK" if not fails else f"{len(fails)} falha(s): {fails}")
sys.exit(1 if fails else 0)
