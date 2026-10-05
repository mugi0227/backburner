#!/usr/bin/env python3
"""Backburner Wi-Fi tunnel client for Windows/Linux/macOS.

Protocol-compatible port of ios/Backburner/Sidecar/Tunnel.swift.
Uses Noise_NNpsk0_25519_ChaChaPoly_SHA256 and exposes the phone's four
Backburner services on localhost:
  51052 -> 50052 (ggml RPC)
  51060 -> 50060 (split-prefill tail)
  51061 -> 50061 (control)
  51062 -> 50062 (phone-held KV / attention)

Requires: pip install cryptography
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import os
import socket
import struct
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROTOCOL = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"
PROLOGUE = b"backburner-wifi-tunnel v1"
PHONE_TUNNEL_PORT = 50070
MAX_PLAIN = 65535 - 16
TARGETS = {50052, 50060, 50061, 50062}
DEFAULT_FORWARDS = {51052: 50052, 51060: 50060, 51061: 50061, 51062: 50062}


class TunnelError(RuntimeError):
    pass


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _hkdf(chaining_key: bytes, ikm: bytes, n: int) -> list[bytes]:
    temp_key = _hmac(chaining_key, ikm)
    out1 = _hmac(temp_key, b"\x01")
    out2 = _hmac(temp_key, out1 + b"\x02")
    if n == 2:
        return [out1, out2]
    out3 = _hmac(temp_key, out2 + b"\x03")
    return [out1, out2, out3]


@dataclass
class NoiseCipher:
    key: bytes | None = None
    nonce: int = 0

    @staticmethod
    def _nonce(n: int) -> bytes:
        if n < 0 or n >= (1 << 64):
            raise TunnelError("nonce exhausted")
        return b"\x00" * 4 + struct.pack("<Q", n)

    def encrypt(self, ad: bytes, plaintext: bytes) -> bytes:
        if self.key is None:
            return plaintext
        if self.nonce >= (1 << 64) - 1:
            raise TunnelError("nonce exhausted")
        out = ChaCha20Poly1305(self.key).encrypt(self._nonce(self.nonce), plaintext, ad)
        self.nonce += 1
        return out

    def decrypt(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self.key is None:
            return ciphertext
        if len(ciphertext) < 16 or self.nonce >= (1 << 64) - 1:
            raise TunnelError("short or invalid ciphertext")
        try:
            out = ChaCha20Poly1305(self.key).decrypt(self._nonce(self.nonce), ciphertext, ad)
        except Exception as exc:  # InvalidTag and backend errors map to protocol auth failure
            raise TunnelError("authentication failed") from exc
        self.nonce += 1
        return out


class NoiseSymmetric:
    def __init__(self, protocol_name: bytes = PROTOCOL):
        self.h = protocol_name + b"\x00" * (32 - len(protocol_name)) if len(protocol_name) <= 32 else _sha256(protocol_name)
        self.ck = self.h
        self.cipher = NoiseCipher()

    def mix_hash(self, data: bytes) -> None:
        self.h = _sha256(self.h + data)

    def mix_key(self, ikm: bytes) -> None:
        o1, o2 = _hkdf(self.ck, ikm, 2)
        self.ck = o1
        self.cipher = NoiseCipher(o2, 0)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        o1, o2, o3 = _hkdf(self.ck, ikm, 3)
        self.ck = o1
        self.mix_hash(o2)
        self.cipher = NoiseCipher(o3, 0)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ciphertext = self.cipher.encrypt(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        plaintext = self.cipher.decrypt(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[NoiseCipher, NoiseCipher]:
        o1, o2 = _hkdf(self.ck, b"", 2)
        return NoiseCipher(o1, 0), NoiseCipher(o2, 0)


class NoiseNNpsk0:
    def __init__(self, prologue: bytes, psk: bytes, private_key: X25519PrivateKey | None = None):
        if len(psk) != 32:
            raise TunnelError("the pairing key must be 32 bytes")
        self.sym = NoiseSymmetric()
        self.sym.mix_hash(prologue)
        self.psk = psk
        self.e = private_key or X25519PrivateKey.generate()
        self.re = b""

    def _pub(self) -> bytes:
        return self.e.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def _dh(self, peer: bytes) -> bytes:
        if len(peer) != 32:
            raise TunnelError("bad peer key length")
        try:
            out = self.e.exchange(X25519PublicKey.from_public_bytes(peer))
        except Exception as exc:
            raise TunnelError("bad peer key") from exc
        if not any(out):
            raise TunnelError("invalid low-order public key")
        return out

    def _mix_ephemeral(self, pub: bytes) -> None:
        self.sym.mix_hash(pub)
        self.sym.mix_key(pub)

    # initiator
    def write_message1(self, payload: bytes) -> bytes:
        self.sym.mix_key_and_hash(self.psk)
        pub = self._pub()
        self._mix_ephemeral(pub)
        return pub + self.sym.encrypt_and_hash(payload)

    def read_message2(self, msg: bytes) -> tuple[bytes, NoiseCipher, NoiseCipher]:
        if len(msg) < 48:
            raise TunnelError("message too short")
        self.re = msg[:32]
        self._mix_ephemeral(self.re)
        self.sym.mix_key(self._dh(self.re))
        payload = self.sym.decrypt_and_hash(msg[32:])
        send, recv = self.sym.split()
        return payload, send, recv

    # responder methods are useful for tests
    def read_message1(self, msg: bytes) -> bytes:
        if len(msg) < 48:
            raise TunnelError("message too short")
        self.sym.mix_key_and_hash(self.psk)
        self.re = msg[:32]
        self._mix_ephemeral(self.re)
        return self.sym.decrypt_and_hash(msg[32:])

    def write_message2(self, payload: bytes) -> tuple[bytes, NoiseCipher, NoiseCipher]:
        pub = self._pub()
        self._mix_ephemeral(pub)
        self.sym.mix_key(self._dh(self.re))
        ciphertext = self.sym.encrypt_and_hash(payload)
        c1, c2 = self.sym.split()
        return pub + ciphertext, c2, c1

    @property
    def handshake_hash(self) -> bytes:
        return self.sym.h


def _read_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise TunnelError("connection closed")
        out += chunk
    return bytes(out)


def _read_frame(sock: socket.socket, max_size: int) -> bytes:
    (n,) = struct.unpack(">I", _read_exact(sock, 4))
    if n > max_size:
        raise TunnelError(f"frame of {n} bytes is too large")
    return _read_exact(sock, n)


def _write_frame(sock: socket.socket, data: bytes) -> None:
    sock.sendall(struct.pack(">I", len(data)) + data)


def client_handshake(sock: socket.socket, psk: bytes, target: int) -> tuple[NoiseCipher, NoiseCipher]:
    if target not in TARGETS:
        raise TunnelError(f"unsupported target port {target}")
    hs = NoiseNNpsk0(PROLOGUE, psk)
    payload = struct.pack(">HBB", target, 0, 0)
    _write_frame(sock, hs.write_message1(payload))
    payload, send, recv = hs.read_message2(_read_frame(sock, 256))
    if payload:
        raise TunnelError("unexpected handshake response payload")
    _write_frame(sock, send.encrypt(b"", b""))  # authenticated open frame
    return send, recv


def _pump_plain_to_secure(plain: socket.socket, secure: socket.socket, send: NoiseCipher) -> None:
    try:
        while True:
            data = plain.recv(MAX_PLAIN)
            if not data:
                break
            _write_frame(secure, send.encrypt(b"", data))
    finally:
        for s in (plain, secure):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _pump_secure_to_plain(plain: socket.socket, secure: socket.socket, recv: NoiseCipher) -> None:
    try:
        while True:
            ct = _read_frame(secure, MAX_PLAIN + 16)
            plain.sendall(recv.decrypt(b"", ct))
    except (OSError, TunnelError):
        pass
    finally:
        for s in (plain, secure):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle_connection(local: socket.socket, phone_host: str, psk: bytes, target: int,
                      phone_port: int = PHONE_TUNNEL_PORT) -> None:
    secure = None
    try:
        secure = socket.create_connection((phone_host, phone_port), timeout=10)
        secure.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        local.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send, recv = client_handshake(secure, psk, target)
        secure.settimeout(None)
        t = threading.Thread(target=_pump_secure_to_plain, args=(local, secure, recv), daemon=True)
        t.start()
        _pump_plain_to_secure(local, secure, send)
        t.join(timeout=2)
    except Exception as exc:
        print(f"backburner-tunnel: target {target}: {exc}")
    finally:
        for s in (local, secure):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def open_listener(local_port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", local_port))
        srv.listen(16)
        return srv
    except Exception:
        srv.close()
        raise


def serve_forward(srv: socket.socket, phone_host: str, psk: bytes, target: int) -> None:
    while True:
        try:
            client, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=handle_connection, args=(client, phone_host, psk, target), daemon=True).start()


def run_forwards(phone_host: str, psk: bytes) -> None:
    listeners = []
    try:
        # Fail before claiming readiness if the phone or key is unavailable.
        with socket.create_connection((phone_host, PHONE_TUNNEL_PORT), timeout=10) as probe:
            client_handshake(probe, psk, 50061)
        for local_port, target in DEFAULT_FORWARDS.items():
            listeners.append((open_listener(local_port), target))
        threads = []
        for srv, target in listeners:
            t = threading.Thread(target=serve_forward, args=(srv, phone_host, psk, target), daemon=True)
            t.start()
            threads.append(t)
            print(f"backburner-tunnel: 127.0.0.1:{srv.getsockname()[1]} -> phone:{target} (encrypted)")
        print("Tunnel ready. Leave this window open while using Backburner.")
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        pass
    finally:
        for srv, _ in listeners:
            srv.close()


def decode_key(raw: str) -> bytes:
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise TunnelError("invalid pairing key encoding") from exc
    if len(key) != 32:
        raise TunnelError("pairing key must contain 32 bytes")
    return key


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise TunnelError("this key is protected by Windows; pair again on this computer")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    src, dst = Blob(len(data), buf), Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    fn.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise TunnelError("Windows could not protect or unlock the pairing key; pair again")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel32.LocalFree(dst.data)


def save_key(path: Path, key: bytes) -> None:
    if len(key) != 32:
        raise TunnelError("pairing key must contain 32 bytes")
    raw = "dpapi:" + base64.b64encode(_dpapi(key, True)).decode("ascii") if os.name == "nt" else key.hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".wifi-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw + "\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_key(path: Path) -> bytes:
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("dpapi:"):
        try:
            key = _dpapi(base64.b64decode(raw[6:], validate=True), False)
        except ValueError as exc:
            raise TunnelError("invalid protected pairing key") from exc
        if len(key) != 32:
            raise TunnelError("invalid protected pairing key length")
        return key
    return decode_key(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True, help="iPhone Wi-Fi IPv4 address")
    ap.add_argument("--key", required=True, type=Path, help="pairing key file")
    args = ap.parse_args()
    psk = load_key(args.key)
    run_forwards(args.phone, psk)


if __name__ == "__main__":
    main()
