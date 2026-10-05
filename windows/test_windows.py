"""Key storage, framing and USB pairing regression checks."""
import base64
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pair
import wifi_tunnel as wt


class WindowsTests(unittest.TestCase):
    def test_key_roundtrip_and_legacy_encodings(self):
        key = bytes(range(32))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "wifi.key"
            wt.save_key(path, key)
            self.assertEqual(wt.load_key(path), key)
            if os.name == "nt":
                self.assertTrue(path.read_text().startswith("dpapi:"))
            else:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for raw in (key.hex(), base64.b64encode(key).decode()):
                path.write_text(raw)
                self.assertEqual(wt.load_key(path), key)

    def test_tamper_replay_and_nonce_exhaustion(self):
        key = bytes(range(32))
        send, recv = wt.NoiseCipher(key), wt.NoiseCipher(key)
        ciphertext = send.encrypt(b"", b"payload")
        altered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        with self.assertRaises(wt.TunnelError):
            recv.decrypt(b"", altered)
        self.assertEqual(recv.nonce, 0)
        self.assertEqual(recv.decrypt(b"", ciphertext), b"payload")
        with self.assertRaises(wt.TunnelError):
            recv.decrypt(b"", ciphertext)
        with self.assertRaises(wt.TunnelError):
            wt.NoiseCipher(key, (1 << 64) - 1).encrypt(b"", b"")

    def test_oversized_frame_and_unsupported_target(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(struct.pack(">I", wt.MAX_PLAIN + 17))
            with self.assertRaises(wt.TunnelError):
                wt._read_frame(b, wt.MAX_PLAIN + 16)
            with self.assertRaises(wt.TunnelError):
                wt.client_handshake(b, bytes(32), 12345)

    def test_control_reply_requires_complete_line(self):
        a, b = socket.socketpair()
        with a, b:
            a.sendall(b"1234")
            with self.assertRaises(RuntimeError):
                pair.recv_line(b, limit=4)

    def test_pair_timeout_cleans_up_without_waiting_for_stderr(self):
        real_popen = subprocess.Popen
        started = []

        def start(_cmd, **kwargs):
            proc = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            started.append(proc)
            return proc

        with tempfile.TemporaryDirectory() as root:
            with patch.object(sys, "argv", ["pair.py", "--out", root]), patch.object(pair.subprocess, "Popen", side_effect=start), patch.object(pair, "wait_port", side_effect=TimeoutError("test timeout")):
                self.assertEqual(pair.main(), 1)
            self.assertIsNotNone(started[0].poll())
            self.assertFalse((Path(root) / "wifi.key").exists())


if __name__ == "__main__":
    unittest.main()
