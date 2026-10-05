#!/usr/bin/env python3
"""Exercise the production forwarder against a local Noise responder."""
from __future__ import annotations

import socket
import struct
import threading
import unittest

import wifi_tunnel as wt


class TunnelLoopbackTests(unittest.TestCase):
    def exchange(self, payload: bytes, bad_key: bool = False) -> bytes:
        psk = bytes(range(32))
        errors = []
        phone = wt.open_listener(0)
        local = wt.open_listener(0)
        phone.settimeout(5)
        local.settimeout(5)

        def responder():
            try:
                conn, _ = phone.accept()
                with conn:
                    conn.settimeout(5)
                    hs = wt.NoiseNNpsk0(wt.PROLOGUE, psk)
                    try:
                        request = hs.read_message1(wt._read_frame(conn, 256))
                    except wt.TunnelError:
                        if bad_key:
                            return
                        raise
                    self.assertEqual(request, struct.pack(">HBB", 50060, 0, 0))
                    reply, send, recv = hs.write_message2(b"")
                    wt._write_frame(conn, reply)
                    self.assertEqual(recv.decrypt(b"", wt._read_frame(conn, 16)), b"")
                    received = bytearray()
                    while len(received) < len(payload):
                        received += recv.decrypt(b"", wt._read_frame(conn, wt.MAX_PLAIN + 16))
                    self.assertEqual(bytes(received), payload)
                    answer = bytes(received).upper()
                    for offset in range(0, len(answer), 1024):
                        wt._write_frame(conn, send.encrypt(b"", answer[offset:offset + 1024]))
            except Exception as exc:
                errors.append(exc)

        def forward():
            try:
                conn, _ = local.accept()
                wt.handle_connection(conn, "127.0.0.1", bytes(reversed(psk)) if bad_key else psk,
                                     50060, phone.getsockname()[1])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=fn, daemon=True) for fn in (responder, forward)]
        for thread in threads:
            thread.start()
        try:
            with socket.create_connection(local.getsockname(), timeout=5) as conn:
                conn.sendall(payload)
                answer = bytearray()
                while len(answer) < len(payload):
                    try:
                        chunk = conn.recv(65536)
                    except ConnectionResetError:
                        break
                    if not chunk:
                        break
                    answer += chunk
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), "forwarder did not stop")
            if errors:
                raise errors[0]
            return bytes(answer)
        finally:
            phone.close()
            local.close()

    def test_roundtrip_multiple_frames(self):
        payload = b"hello backburner " * 10000
        self.assertEqual(self.exchange(payload), payload.upper())

    def test_wrong_key_is_refused(self):
        self.assertEqual(self.exchange(b"hello", bad_key=True), b"")


if __name__ == "__main__":
    unittest.main()
