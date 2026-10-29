# -*- coding: utf-8 -*-
"""
Kepler73 - minimal Hamlib rigctld client.

Speaks the plain-text protocol of hamlib's ``rigctld`` daemon over TCP. We only
need a handful of commands (set/get frequency, PTT, split downlink), so there is
no dependency on libhamlib or its Python bindings - the user runs

    rigctld -m <model> -r <port>          (default port 4532, model 1 = dummy)

and Kepler73 talks to it. Every method is best-effort: on any socket or
protocol error it returns None/False and stores the reason in ``last_error``;
nothing here raises, so the 1 Hz broadcast loop can call it freely.
"""

import socket
import threading


class RigctldClient:
    def __init__(self, host="127.0.0.1", port=4532, timeout=1.0):
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
    def _recv_reply(self):
        """Read one reply. Returns as soon as a newline arrives; if bytes come
        but then the socket goes quiet (a reply with no trailing newline), use
        what we have instead of hanging until the full timeout."""
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
                raise OSError("connection closed by rigctld")
            buf += chunk
            if b"\n" in buf:
                return buf

    def _cmd(self, line):
        """Send one command line, return its first reply line (str) or None.
        Reconnects once transparently if the socket has gone away."""
        with self._lock:
            for _attempt in (1, 2):
                if self._sock is None and not self._connect():
                    return None
                try:
                    self._sock.sendall((line + "\n").encode("ascii"))
                    raw = self._recv_reply()
                    self.last_error = ""
                    first = raw.split(b"\n", 1)[0] if b"\n" in raw else raw
                    return first.decode("ascii", "replace").strip()
                except OSError as e:
                    self.last_error = f"{line!r}: {e}"
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                    # fall through to one retry with a fresh connection
            return None

    @staticmethod
    def _ok(reply):
        # rigctld answers a 'set' command with "RPRT 0" on success.
        return reply is not None and reply.replace(" ", "").upper().startswith("RPRT0")

    # ── high level ────────────────────────────────────────────
    def set_freq(self, hz):
        return self._ok(self._cmd(f"F {int(round(hz))}"))

    def get_freq(self):
        r = self._cmd("f")
        if r and not r.upper().startswith("RPRT"):
            try:
                return int(float(r.split()[0]))
            except (ValueError, IndexError):
                pass
        # Fall back to the extended/verbose form: "+f" -> "Frequency: 145000000"
        r = self._cmd("+f")
        if r:
            for tok in r.replace(":", " ").split():
                if tok.isdigit():
                    return int(tok)
        return None

    def get_mode(self):
        """(mode_str, passband_hz | None) or None. Plain 'm' answers with two
        lines: the mode name (USB / LSB / CW / FM / WFM / AM / PKTUSB ...) then
        the passband in Hz."""
        with self._lock:
            for _attempt in (1, 2):
                if self._sock is None and not self._connect():
                    return None
                try:
                    self._sock.sendall(b"m\n")
                    raw = self._recv_reply()
                    if raw.count(b"\n") < 2:      # second line may lag one packet
                        try:
                            raw += self._sock.recv(128)
                        except (socket.timeout, TimeoutError):
                            pass
                    self.last_error = ""
                    parts = [p.strip() for p in
                             raw.decode("ascii", "replace").split("\n") if p.strip()]
                    if not parts or parts[0].upper().startswith("RPRT"):
                        return None
                    pb = None
                    if len(parts) > 1:
                        try:
                            pb = int(float(parts[1].split()[0]))
                        except (ValueError, IndexError):
                            pass
                    return (parts[0], pb)
                except OSError as e:
                    self.last_error = f"'m': {e}"
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
            return None

    def set_split_freq(self, hz):
        # 'I' sets the TX (split) frequency; the rig must have split enabled.
        return self._ok(self._cmd(f"I {int(round(hz))}"))

    def set_ptt(self, on):
        return self._ok(self._cmd(f"T {1 if on else 0}"))

    def ping(self):
        """(ok, detail) - drives the Settings 'Test connection' button."""
        f = self.get_freq()
        if f is not None:
            return True, f"rigctld OK - VFO at {f / 1e6:.4f} MHz"
        return False, self.last_error or "connected, but no readable reply to 'f'"

    def probe(self):
        """Diagnostic for the 'Test connection' button: connect and show the
        raw bytes rigctld returns for a few commands."""
        out = {"connected": False, "freq_hz": None, "attempts": []}
        if self._sock is None and not self._connect():
            out["error"] = self.last_error
            return out
        out["connected"] = True
        for cmd in ("f", "+f", "\\dump_state"):
            try:
                self._sock.sendall((cmd + "\n").encode("ascii"))
                raw = self._recv_reply()
                out["attempts"].append({"cmd": cmd,
                                        "raw": raw[:200].decode("ascii", "replace")})
                if b"\n" not in raw:      # unterminated -> reconnect for next cmd
                    self.close()
                    self._connect()
            except OSError as e:
                out["attempts"].append({"cmd": cmd, "error": str(e)})
                self.close()
                if not self._connect():
                    break
        out["freq_hz"] = self.get_freq()
        return out
