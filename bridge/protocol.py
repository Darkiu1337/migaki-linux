"""Newline-delimited JSON framing for the migaki-gui bridge.

stdout carries the protocol (one JSON object per line, UTF-8, flushed per
write, guarded by one lock); stderr is free-form logging that the Rust
frontend appends to ~/.cache/migaki/gui.log.

Requests  {"id": int, "method": str, "params": {...}}
Responses {"id": int, "ok": true, "result": ...} / {"id": int, "ok": false, "error": str}
Events    {"event": str, ...}   (no id)
"""
import json
import sys
import threading


class Writer:
    """Thread-safe NDJSON writer over one stream."""

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()

    def send(self, obj):
        line = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def respond(self, req_id, result=None, error=None):
        if error is None:
            self.send({"id": req_id, "ok": True, "result": result})
        else:
            self.send({"id": req_id, "ok": False, "error": str(error)})

    def event(self, name, **fields):
        payload = {"event": name}
        payload.update(fields)
        self.send(payload)

    def flush(self):
        with self._lock:
            self._stream.flush()
