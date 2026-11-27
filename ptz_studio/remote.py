"""Controlo remoto por Wi-Fi: telemóvel/tablet (página web), VLC (stream MJPEG),
Stream Deck / Bitfocus Companion (API HTTP)."""
from __future__ import annotations

import json
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).resolve().parent / "web"


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # não envia nada; só escolhe a interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class RemoteServer:
    """Servidor HTTP em thread própria. Os comandos vão para uma fila lida pela UI."""

    def __init__(self, port: int, token: str, frame_source, state_source) -> None:
        self.port = port
        self.token = token
        self.commands: queue.Queue = queue.Queue()
        self._frame_source = frame_source   # () -> bytes JPEG
        self._state_source = state_source   # () -> dict
        self.program_source = None          # () -> bytes JPEG do programa da mesa (opcional)
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silencioso
                pass

            def _auth(self, qs) -> bool:
                if qs.get("k", [""])[0] == server.token:
                    return True
                self.send_error(403, "Token inválido")
                return False

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                if not self._auth(qs):
                    return
                if url.path in ("/", "/index.html"):
                    self._send(200, (WEB_DIR / "remote.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/dock":  # painel compacto para o OBS (Custom Browser Dock)
                    self._send(200, (WEB_DIR / "dock.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/snapshot.jpg":
                    self._send(200, server._frame_source(), "image/jpeg")
                elif url.path == "/stream.mjpg":
                    self._stream(server._frame_source)
                elif url.path == "/program.mjpg":   # saída da mesa de realização (VLC, telemóvel)
                    if server.program_source is None:
                        self.send_error(404, "Mesa de realização fechada")
                    else:
                        self._stream(server.program_source)
                elif url.path == "/api/state":
                    self._send(200, json.dumps(server._state_source()).encode(), "application/json")
                elif url.path == "/api/cmd":  # GET simples para Companion/Stream Deck
                    cmd = {k: v[0] for k, v in qs.items() if k != "k"}
                    server.commands.put(cmd)
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self.send_error(404)

            def do_POST(self):
                url = urlparse(self.path)
                qs = parse_qs(url.query)
                if not self._auth(qs):
                    return
                if url.path != "/api/cmd":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    cmd = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    self.send_error(400)
                    return
                server.commands.put(cmd)
                self._send(200, b'{"ok":true}', "application/json")

            def _stream(self, source):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                import time
                try:
                    while server.running:
                        jpg = source() if source else b""
                        if jpg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        time.sleep(1 / 15)
                except (ConnectionError, OSError):
                    pass

        self.running = True
        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="remote-http", daemon=True)
        self._thread.start()

    def url(self, path: str = "/") -> str:
        return f"http://{lan_ip()}:{self.port}{path}?k={self.token}"

    def stop(self) -> None:
        self.running = False
        self.httpd.shutdown()
        self.httpd.server_close()
