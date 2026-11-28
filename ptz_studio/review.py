"""Revisão e triagem de fotos (estilo Lightroom/Photo Mechanic): estrelas, escolher/rejeitar,
comparar lado a lado, filtrar e entregar as escolhidas.

As classificações ficam em `.alflor_classificacoes.json` na pasta e, para JPEG/PNG/TIFF/DNG,
também no XMP do próprio ficheiro (xmp:Rating; rejeitadas = -1), que o Lightroom lê ao importar.

Teclado: 0–5 estrelas · P escolher · X rejeitar · U limpar · C comparar com a anterior ·
setas navegar · Enter abrir no programa predefinido · Delete enviar para a Reciclagem.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".dng")


def read_qimage(path: str, box: QSize | None = None) -> QImage:
    """Lê a imagem para o ecrã. O Qt não abre DNG nem TIFF comprimido: aí usa o tifffile
    (o DNG linear do Alflor Studio recebe a curva sRGB para se ver como no Lightroom)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if box is not None and size.isValid() and size.width() > 0:
        reader.setScaledSize(size.scaled(box, Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    if not img.isNull() or Path(path).suffix.lower() not in (".dng", ".tif", ".tiff"):
        return img
    try:
        import cv2
        import numpy as np
        import tifffile
        a = tifffile.imread(path)
        if a.ndim == 2:
            a = np.dstack([a] * 3)
        a = a[..., :3]
        if Path(path).suffix.lower() == ".dng":      # RGB linear 16 bits → sRGB 8 bits
            x = a.astype(np.float32) / 65535.0
            x = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
            a = np.clip(x * 255 + 0.5, 0, 255).astype(np.uint8)
        elif a.dtype != np.uint8:
            a = (a / 257).astype(np.uint8)
        if box is not None:
            s = min(box.width() / a.shape[1], box.height() / a.shape[0], 1.0)
            a = cv2.resize(a, (max(1, int(a.shape[1] * s)), max(1, int(a.shape[0] * s))), interpolation=cv2.INTER_AREA)
        a = np.ascontiguousarray(a)
        return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_RGB888).copy()
    except Exception:
        return QImage()
DB_NAME = ".alflor_classificacoes.json"


def _send_to_recycle_bin(path: Path) -> bool:
    """Envia para a Reciclagem (recuperável) — nunca apaga definitivamente."""
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0\0", None, 0x0040 | 0x0010 | 0x0004, False, None, None)  # DELETE, ALLOWUNDO|NOCONFIRMATION|SILENT
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    except Exception:
        return False


def write_xmp_rating(path: Path, rating: int) -> bool:
    """Atualiza xmp:Rating (0–5) dentro do ficheiro gravado pelo Alflor Studio, para o Lightroom o ler.

    Só faz trocas do MESMO tamanho (um dígito por outro): os blocos JPEG/TIFF/DNG têm comprimentos
    e posições fixos e mudar o tamanho estragaria o ficheiro. No PNG recalcula o CRC do bloco.
    Rejeitadas (−1) ficam só no registo da pasta.
    """
    if not 0 <= int(rating) <= 5:
        return False
    try:
        data = bytearray(path.read_bytes())
        m = re.search(rb'xmp:Rating="(\d)"', data)
        if not m:
            return False
        data[m.start(1)] = ord(str(int(rating)))
        if path.suffix.lower() == ".png":   # recalcula o CRC do bloco iTXt que contém o XMP
            import struct
            import zlib
            pos = 8
            while pos + 12 <= len(data):
                length = struct.unpack(">I", data[pos:pos + 4])[0]
                start, end = pos + 8, pos + 8 + length
                if start <= m.start(1) < end:
                    crc = zlib.crc32(bytes(data[pos + 4:end])) & 0xFFFFFFFF
                    data[end:end + 4] = struct.pack(">I", crc)
                    break
                pos = end + 4
        path.write_bytes(bytes(data))
        return True
    except (OSError, ValueError):
        return False


class ReviewWindow(QMainWindow):
    def __init__(self, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revisão de fotos · Alflor Studio")
        self.resize(1400, 860)
        self.folder = Path(folder)
        self.db: dict = {}
        self.compare = False
        central = QWidget()
        root = QVBoxLayout(central)
        top = QHBoxLayout()
        self.lbl_folder = QLabel()
        b_folder = QPushButton("Pasta…")
        b_folder.clicked.connect(self.choose_folder)
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(["Todas", "★ ≥ 1", "★ ≥ 3", "★★★★★", "Escolhidas (P)", "Rejeitadas (X)", "Sem classificação"])
        self.cmb_filter.currentIndexChanged.connect(self.refresh)
        b_export = QPushButton("Copiar escolhidas para…")
        b_export.setToolTip("Escolhidas (P) e fotos com 3 ou mais estrelas — para entregar ao designer/cliente")
        b_export.clicked.connect(self.export_selected)
        b_cmp = QPushButton("Comparar (C)")
        b_cmp.clicked.connect(self.toggle_compare)
        for wdg in (QLabel("Pasta:"), self.lbl_folder, b_folder, QLabel("Mostrar:"), self.cmb_filter, b_cmp, b_export):
            top.addWidget(wdg)
        top.addStretch(1)
        root.addLayout(top)
        split = QSplitter(Qt.Orientation.Vertical)
        viewer = QWidget()
        vl = QHBoxLayout(viewer)
        self.view_a = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.view_b = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        for v in (self.view_a, self.view_b):
            v.setMinimumSize(400, 300)
            v.setStyleSheet("background:#05070a;")
            vl.addWidget(v, 1)
        self.view_b.hide()
        split.addWidget(viewer)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(200, 112))
        self.strip.setMaximumHeight(190)
        self.strip.currentRowChanged.connect(self.show_current)
        split.addWidget(self.strip)
        split.setStretchFactor(0, 1)
        root.addWidget(split, 1)
        self.lbl_info = QLabel("0–5 estrelas · P escolher · X rejeitar · U limpar · C comparar · Enter abrir · Delete → Reciclagem",
                               objectName="hint")
        root.addWidget(self.lbl_info)
        self.setCentralWidget(central)
        self.load_folder(self.folder)

    # --- dados ----------------------------------------------------------------------------
    def load_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.lbl_folder.setText(str(self.folder))
        try:
            self.db = json.loads((self.folder / DB_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.db = {}
        self.refresh()

    def _save_db(self) -> None:
        try:
            (self.folder / DB_NAME).write_text(json.dumps(self.db, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            self.statusBar().showMessage(f"Não foi possível guardar as classificações: {exc}", 5000)

    def files(self) -> list[Path]:
        if not self.folder.exists():
            return []
        return sorted((p for p in self.folder.iterdir() if p.suffix.lower() in EXTS and not p.stem.endswith(("_mascara", "_original"))),
                      key=lambda p: p.stat().st_mtime)

    def _passes(self, p: Path) -> bool:
        e = self.db.get(p.name, {})
        r, flag = e.get("rating", 0), e.get("flag", "")
        f = self.cmb_filter.currentIndex()
        return [True, r >= 1, r >= 3, r == 5, flag == "pick", flag == "reject", not r and not flag][f]

    def _label(self, p: Path) -> str:
        e = self.db.get(p.name, {})
        stars = "★" * e.get("rating", 0)
        flag = {"pick": " ⚑", "reject": " ✕"}.get(e.get("flag", ""), "")
        return f"{p.stem[-14:]}\n{stars}{flag}"

    def refresh(self) -> None:
        cur = self.strip.currentItem().data(Qt.ItemDataRole.UserRole) if self.strip.currentItem() else None
        self.strip.blockSignals(True)
        self.strip.clear()
        for p in self.files():
            if not self._passes(p):
                continue
            it = QListWidgetItem(self._label(p))
            it.setData(Qt.ItemDataRole.UserRole, str(p))
            img = read_qimage(str(p), QSize(320, 180))
            it.setIcon(QIcon(QPixmap.fromImage(img)) if not img.isNull() else QIcon())
            self.strip.addItem(it)
            if cur == str(p):
                self.strip.setCurrentItem(it)
        self.strip.blockSignals(False)
        if self.strip.currentRow() < 0 and self.strip.count():
            self.strip.setCurrentRow(self.strip.count() - 1)
        self.show_current()
        n = len(self.files())
        picks = sum(1 for v in self.db.values() if v.get("flag") == "pick")
        self.statusBar().showMessage(f"{self.strip.count()} de {n} fotos · {picks} escolhidas")

    def _pix(self, path: str, label: QLabel) -> None:
        img = read_qimage(path)
        if img.isNull():
            label.setText(f"(sem pré-visualização: {Path(path).suffix})")
            return
        label.setPixmap(QPixmap.fromImage(img).scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))

    def show_current(self, *_a) -> None:
        it = self.strip.currentItem()
        if not it:
            self.view_a.clear()
            return
        path = it.data(Qt.ItemDataRole.UserRole)
        self._pix(path, self.view_a)
        if self.compare and self.strip.currentRow() > 0:
            self._pix(self.strip.item(self.strip.currentRow() - 1).data(Qt.ItemDataRole.UserRole), self.view_b)
        e = self.db.get(Path(path).name, {})
        self.setWindowTitle(f"Revisão · {Path(path).name} · {'★' * e.get('rating', 0) or 'sem estrelas'}")

    # --- ações ----------------------------------------------------------------------------
    def _set(self, rating: int | None = None, flag: str | None = None) -> None:
        it = self.strip.currentItem()
        if not it:
            return
        p = Path(it.data(Qt.ItemDataRole.UserRole))
        e = self.db.setdefault(p.name, {})
        if rating is not None:
            e["rating"] = rating
        if flag is not None:
            e["flag"] = flag
        self._save_db()
        xmp_rating = -1 if e.get("flag") == "reject" else e.get("rating", 0)
        write_xmp_rating(p, xmp_rating)
        it.setText(self._label(p))
        self.show_current()

    def toggle_compare(self) -> None:
        self.compare = not self.compare
        self.view_b.setVisible(self.compare)
        self.show_current()

    def export_selected(self) -> None:
        chosen = [p for p in self.files() if self.db.get(p.name, {}).get("flag") == "pick" or self.db.get(p.name, {}).get("rating", 0) >= 3]
        if not chosen:
            QMessageBox.information(self, "Revisão", "Ainda não há fotos escolhidas (P) nem com 3 ou mais estrelas.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Copiar escolhidas para", str(self.folder))
        if not dest:
            return
        for p in chosen:
            shutil.copy2(p, Path(dest) / p.name)
            for extra in (p.with_name(p.stem + "_mascara.png"),):
                if extra.exists():
                    shutil.copy2(extra, Path(dest) / extra.name)
        self.statusBar().showMessage(f"{len(chosen)} foto(s) copiadas para {dest}", 6000)

    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta de fotos", str(self.folder))
        if d:
            self.load_folder(Path(d))

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if Qt.Key.Key_0 <= k <= Qt.Key.Key_5:
            self._set(rating=k - Qt.Key.Key_0)
        elif k == Qt.Key.Key_P:
            self._set(flag="pick")
        elif k == Qt.Key.Key_X:
            self._set(flag="reject")
        elif k == Qt.Key.Key_U:
            self._set(rating=0, flag="")
        elif k == Qt.Key.Key_C:
            self.toggle_compare()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.strip.currentItem():
            os.startfile(self.strip.currentItem().data(Qt.ItemDataRole.UserRole))
        elif k == Qt.Key.Key_Delete and self.strip.currentItem():
            p = Path(self.strip.currentItem().data(Qt.ItemDataRole.UserRole))
            if _send_to_recycle_bin(p):
                self.statusBar().showMessage(f"{p.name} enviado para a Reciclagem (recuperável)", 5000)
                self.refresh()
        elif k == Qt.Key.Key_Right:
            self.strip.setCurrentRow(min(self.strip.count() - 1, self.strip.currentRow() + 1))
        elif k == Qt.Key.Key_Left:
            self.strip.setCurrentRow(max(0, self.strip.currentRow() - 1))
        else:
            super().keyPressEvent(ev)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.show_current()
