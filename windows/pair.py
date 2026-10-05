#!/usr/bin/env python3
"""Pair the upstream Backburner iPhone app with a Windows PC for Wi-Fi use.

The iPhone intentionally accepts `pair` only over the USB cable. This helper
uses pymobiledevice3/usbmux to forward a temporary localhost port to the app's
control service (:50061), requests a fresh pairing key, and stores it under the
user profile for wifi_tunnel.py.

Prerequisites (Windows):
  py -m pip install -U pymobiledevice3
  Apple Mobile Device Service (the non-Store iTunes package is the safest path)
  Backburner open in the foreground on the iPhone
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from wifi_tunnel import decode_key, save_key

DEFAULT_LOCAL_PORT = 55161


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "BackburnerWindows"


def wait_port(port: int, proc: subprocess.Popen[bytes], timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"usbmux forward exited early with code {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.15)
    raise TimeoutError("timed out waiting for the USB port forward")


def recv_line(sock: socket.socket, limit: int = 1 << 20) -> bytes:
    out = bytearray()
    while len(out) < limit:
        chunk = sock.recv(min(65536, limit - len(out)))
        if not chunk:
            raise RuntimeError("control connection closed before a complete reply")
        out += chunk
        if b"\n" in chunk:
            return bytes(out).split(b"\n", 1)[0]
    raise RuntimeError("control reply is too large")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udid", help="iPhone UDID when more than one iDevice is connected")
    ap.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    ap.add_argument("--out", type=Path, default=config_dir(), help="directory for wifi.key / wifi.json")
    args = ap.parse_args()

    cmd = [sys.executable, "-m", "pymobiledevice3", "usbmux", "forward"]
    if args.udid:
        cmd += ["--udid", args.udid]
    cmd += [str(args.local_port), "50061"]

    print("Starting temporary USB forward to Backburner control port...")
    errors = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errors)
    except OSError as exc:
        errors.close()
        print(f"pair failed: {exc}", file=sys.stderr)
        return 1
    try:
        wait_port(args.local_port, proc)
        with socket.create_connection(("127.0.0.1", args.local_port), timeout=10) as s:
            s.sendall(b"pair\n")
            raw = recv_line(s)
        reply = json.loads(raw.decode("utf-8"))
        if not isinstance(reply, dict):
            raise RuntimeError("unexpected control reply")
        if reply.get("error") or not reply.get("wifi_key"):
            raise RuntimeError(reply.get("error") or "control reply did not contain a pairing key")
        key = decode_key(reply["wifi_key"])

        args.out.mkdir(parents=True, exist_ok=True)
        key_path = args.out / "wifi.key"
        info_path = args.out / "wifi.json"
        save_key(key_path, key)
        info_path.write_text(json.dumps({
            "wifi_ip": reply.get("wifi_ip", ""),
            "wifi_port": reply.get("wifi_port", 50070),
        }, indent=2) + "\n", encoding="utf-8")

        print(f"Paired. Key: {key_path}")
        print(f"Phone Wi-Fi address: {reply.get('wifi_ip') or '(not reported; make sure Wi-Fi is on)'}")
        print(f"Config: {info_path}")
        return 0
    except Exception as exc:
        print(f"pair failed: {exc}", file=sys.stderr)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        errors.close()


if __name__ == "__main__":
    raise SystemExit(main())
