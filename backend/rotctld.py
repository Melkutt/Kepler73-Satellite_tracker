# -*- coding: utf-8 -*-
"""
Kepler73 - minimal Hamlib rotctld client.

Talks to hamlib's ``rotctld`` daemon (the rotator sibling of rigctld) over TCP.
The user runs

    rotctld -m <model> -r <COM/device>       (default port 4533, model 1 = dummy)

and Kepler73 sends it azimuth / elevation. Every method is best-effort: on any
socket or protocol error it returns None/False and records ``last_error``;
nothing here raises, so the 1 Hz loop can call it freely.
"""

import socket
import threading


class RotctldClient:
    def __init__(self, host="127.0.0.1", port=4533, timeout=1.0):
        self.host = str(host)
        self.port = int(port)
        self.timeout = float(timeout)
        self.last_error = ""
        self._sock = None
        self._lock = threading.Lock()

    # ── connection ──────────────────────────────────────────────
    def _connect(self):
        try:
            s = socket.create_connection((self.host, self.port), self.timeout)
            s.settimeout(self.timeout)
            self._sock = s
            return True
        except OSError as e:
            self.last_error = f"connect {self.host}:{self.port}: {e}"
            self._sock = None
            return False

    def close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None

    # ── raw command ────────────────────────────────────────────
    def _recv_reply(self, want_lines=1):
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(512)
            except (socket.timeout, TimeoutError):
                if buf:
                    return buf
                raise
            if not chunk:
                if buf:
                    return buf
                raise OSError("connection closed by rotctld")
            buf += chunk
            if buf.count(b"\n") >= want_lines:
                return buf

    def _cmd(self, line, want_lines=1):
        with self._lock:
            for _attempt in (1, 2):
                if self._sock is None and not self._connect():
                    return None
                try:
                    self._sock.sendall((line + "\n").encode("ascii"))
                    raw = self._recv_reply(want_lines)
                    self.last_error = ""
                    return raw.decode("ascii", "replace")
                except OSError as e:
                    self.last_error = f"{line!r}: {e}"
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
            return None

    @staticmethod
    def _ok(reply):
        if not reply:
            return False
        for ln in reply.splitlines():
            ln = ln.strip()
            if ln.upper().startswith("RPRT"):
                try:
                    return int(ln.split()[1]) == 0
                except (IndexError, ValueError):
                    return False
        return False

    # ── high level ────────────────────────────────────────────
    def set_pos(self, az, el):
        return self._ok(self._cmd(f"P {float(az):.2f} {float(el):.2f}"))

    def get_pos(self):
        r = self._cmd("p", want_lines=2)
        if not r:
            return None
        parts = [p.strip() for p in r.split("\n") if p.strip()]
        if len(parts) < 2 or parts[0].upper().startswith("RPRT"):
            return None
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError:
            return None

    def stop(self):
        return self._ok(self._cmd("S"))

    def ping(self):
        """(ok, detail) - drives the 'Test connection' button."""
        p = self.get_pos()
        if p is not None:
            return True, f"rotctld OK - at Az {p[0]:.1f} El {p[1]:.1f}"
        return False, self.last_error or "connected, but no readable reply to 'p'"

    def probe(self):
        out = {"connected": False, "pos": None, "attempts": []}
        if self._sock is None and not self._connect():
            out["error"] = self.last_error
            return out
        out["connected"] = True
        for cmd, wl in (("p", 2), ("\\dump_state", 1)):
            try:
                self._sock.sendall((cmd + "\n").encode("ascii"))
                raw = self._recv_reply(wl)
                out["attempts"].append({"cmd": cmd,
                                        "raw": raw[:200].decode("ascii", "replace")})
            except OSError as e:
                out["attempts"].append({"cmd": cmd, "error": str(e)})
                self.close()
                if not self._connect():
                    break
        out["pos"] = self.get_pos()
        return out
