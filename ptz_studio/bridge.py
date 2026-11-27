"""Cliente Python da ponte nativa DirectShow (ptzbridge.exe)."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import threading
from pathlib import Path

from .config import LOCAL_DATA, PACKAGE_DIR

NO_WINDOW = 0x08000000
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
SOURCE = PACKAGE_DIR / "native" / "ptzbridge.cs"


def build_bridge() -> Path:
    """Compila a ponte com o csc.exe do Windows (só quando o código muda)."""
    bin_dir = LOCAL_DATA / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "ptzbridge.exe"
    stamp = bin_dir / "ptzbridge.sha256"
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if exe.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        return exe
    if not CSC.exists():
        raise RuntimeError(f"Compilador C# do .NET Framework não encontrado em {CSC}")
    result = subprocess.run(
        [str(CSC), "/nologo", "/optimize+", "/platform:x64", "/target:exe", f"/out:{exe}", str(SOURCE)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError("Falha a compilar a ponte PTZ:\n" + result.stdout + result.stderr)
    stamp.write_text(digest)
    return exe


class BridgeError(RuntimeError):
    pass


class Bridge:
    """Processo persistente que fala DirectShow; pedidos por linhas JSON."""

    def __init__(self) -> None:
        exe = build_bridge()
        self.proc = subprocess.Popen(
            [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW, encoding="utf-8", bufsize=1,
        )
        self._ids = itertools.count(1)
        self._waiters: dict[str, list] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._ready = threading.Event()
        threading.Thread(target=self._reader, name="ptzbridge-reader", daemon=True).start()
        if not self._ready.wait(10):
            raise BridgeError("A ponte PTZ não respondeu")

    def _reader(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            sid, _, payload = line.strip().partition(" ")
            if sid == "0":
                self._ready.set()
                continue
            with self._lock:
                slot = self._waiters.pop(sid, None)
            if slot is not None:
                try:
                    slot[1] = json.loads(payload)
                except ValueError:
                    slot[1] = {"error": f"resposta inválida: {payload[:80]}"}
                slot[0].set()

    def call(self, cmd: str, arg: str = "", wait: bool = True, timeout: float = 5.0) -> dict | None:
        if self.proc.poll() is not None:
            raise BridgeError("A ponte PTZ terminou")
        sid = str(next(self._ids))
        slot = [threading.Event(), None]
        if wait:
            with self._lock:
                self._waiters[sid] = slot
        with self._write_lock:
            assert self.proc.stdin
            self.proc.stdin.write(f"{sid} {cmd} {arg}\n")
            self.proc.stdin.flush()
        if not wait:
            return None
        if not slot[0].wait(timeout):
            with self._lock:
                self._waiters.pop(sid, None)
            raise BridgeError(f"Sem resposta da ponte para '{cmd}'")
        res = slot[1] or {}
        if "error" in res:
            raise BridgeError(res["error"])
        return res

    # --- API de alto nível -------------------------------------------------
    def list_devices(self) -> dict:
        return self.call("list") or {"video": [], "audio": []}

    def open(self, index: int) -> dict:
        return self.call("open", str(index)) or {}

    def info(self) -> dict:
        return self.call("info") or {}

    def set_cam(self, name: str, value: int, auto: bool = False, wait: bool = False) -> None:
        self.call("cam", f"{name} {int(value)} {1 if auto else 2}", wait=wait)

    def set_amp(self, name: str, value: int, auto: bool = False, wait: bool = False) -> None:
        self.call("amp", f"{name} {int(value)} {1 if auto else 2}", wait=wait)

    def close(self) -> None:
        try:
            self.call("close", timeout=2)
        except BridgeError:
            pass

    def shutdown(self) -> None:
        try:
            self.call("quit", timeout=2)
        except (BridgeError, OSError):
            pass
        try:
            self.proc.wait(2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
