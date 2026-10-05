#!/usr/bin/env python3
"""Start the paired Backburner Wi-Fi tunnel using saved Windows config."""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from wifi_tunnel import load_key, run_forwards


def default_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home() / ".config")) / "BackburnerWindows"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", type=Path, default=default_dir())
    ap.add_argument("--phone", help="override saved iPhone Wi-Fi IPv4 address")
    args = ap.parse_args()
    info_path = args.config_dir / "wifi.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    phone = args.phone or info.get("wifi_ip")
    if not phone:
        raise SystemExit("no Wi-Fi address saved; pass --phone A.B.C.D")
    psk = load_key(args.config_dir / "wifi.key")
    run_forwards(phone, psk)

if __name__ == "__main__": main()
