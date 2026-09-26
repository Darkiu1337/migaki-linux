#!/usr/bin/env python3
"""ws_bridge.py — tiny, dependency-free WebSocket server (RFC 6455).

Only what the translation bridge needs: accept clients, push text frames to
all of them, answer pings, drop the dead. It exists so the CDP Tyrano hook can
serve the SAME ws://127.0.0.1:6677 contract the Textractor fork uses, with no
extra Python dependency (websocket-client is client-only).
"""
import base64
import hashlib
import socket
import struct
import threading

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _accept_key(key):
    return base64.b64encode(
        hashlib.sha1((key + _GUID).encode()).digest()).decode()


def _len_bytes(n):
    if n < 126:
        return bytes([n])
    if n < 65536:
        return bytes([126]) + struct.pack(">H", n)
    return bytes([127]) + struct.pack(">Q", n)


class _Client:
    def __init__(self, conn, server):
        self.conn = conn
        self.server = server
        self.alive = True
        self.lock = threading.Lock()

    def send_text(self, text):
        data = text.encode("utf-8")
        frame = bytes([0x81]) + _len_bytes(len(data)) + data
        with self.lock:
            try:
                self.conn.sendall(frame)
            except OSError:
                self.alive = False

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.conn.recv(n - len(buf))
            if not chunk:
                raise OSError("eof")
            buf += chunk
        return buf

    def reader(self):
        """Consume client frames until close/EOF; answer pings. A text frame is
        an uplink line (the injected RPGMaker hook): the server relays it to the
        other clients (the textbox)."""
        try:
            while True:
                b1, b2 = self._recv_exact(2)
                opcode = b1 & 0x0F
                masked = b2 & 0x80
                ln = b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._recv_exact(2))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._recv_exact(8))[0]
                mask = self._recv_exact(4) if masked else b""
                payload = self._recv_exact(ln) if ln else b""
                if masked and payload:
                    payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping -> pong (echo, capped)
                    payload = payload[:125]
                    with self.lock:
                        self.conn.sendall(bytes([0x8A]) + _len_bytes(len(payload)) + payload)
                elif opcode == 0x1:  # text: uplink line
                    try:
                        self.server._on_client_text(self, payload.decode("utf-8", "replace"))
                    except Exception:
                        pass
        except (OSError, ValueError):
            pass
        self.alive = False
        try:
            self.conn.close()
        except OSError:
            pass


class WSBridgeServer:
    """Broadcast/relay server on host:port. start() is non-blocking. Text frames
    received from a client are relayed to the other clients when relay=True
    (the injected RPGMaker hook sends its tagged lines this way)."""

    def __init__(self, host="127.0.0.1", port=6677, relay=True, on_text=None):
        self.host, self.port = host, port
        self.relay = relay
        self.on_text = on_text
        self.clients = set()
        self.lock = threading.Lock()
        self._sock = None

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(16)
        self._sock = s
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handshake, args=(conn,),
                             daemon=True).start()

    def _handshake(self, conn):
        try:
            conn.settimeout(5)
            req = b""
            while b"\r\n\r\n" not in req and len(req) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                req += chunk
            headers = {}
            for line in req.decode("latin-1").split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            key = headers.get("sec-websocket-key")
            if not key:
                conn.close()
                return
            resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Accept: " + _accept_key(key) + "\r\n\r\n")
            conn.sendall(resp.encode())
            conn.settimeout(None)
        except OSError:
            try:
                conn.close()
            except OSError:
                pass
            return
        c = _Client(conn, self)
        with self.lock:
            self.clients.add(c)
        c.reader()
        with self.lock:
            self.clients.discard(c)

    def _on_client_text(self, sender, text):
        """A client (the injected page hook) sent a line: relay it to the rest."""
        if self.on_text is not None:
            try:
                self.on_text(text)
            except Exception:
                pass
        if self.relay and text:
            self.broadcast(text, exclude=sender)

    def broadcast(self, text, exclude=None):
        with self.lock:
            clients = [c for c in self.clients if c is not exclude]
        dead = []
        for c in clients:
            if c.alive:
                c.send_text(text)
            if not c.alive:
                dead.append(c)
        if dead:
            with self.lock:
                for c in dead:
                    self.clients.discard(c)

    def count(self):
        with self.lock:
            return len(self.clients)
