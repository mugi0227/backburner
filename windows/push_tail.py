#!/usr/bin/env python3
"""Push a Backburner tail.gguf to the iPhone app's Documents from Windows.

Uses pymobiledevice3 HouseArrest/AFC, avoiding macOS-only devicectl and the
upstream link-local HTTP helper.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_json(cmd: list[str]) -> object:
    p = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(p.stdout)


def find_backburner_bundle(udid: str | None) -> str:
    cmd = [sys.executable, "-m", "pymobiledevice3", "apps", "list"]
    if udid:
        cmd += ["--udid", udid]
    apps = run_json(cmd)
    if not isinstance(apps, dict):
        raise RuntimeError("unexpected `apps list` output")
    matches = []
    for bundle, meta in apps.items():
        text = " ".join(str(x) for x in [bundle, meta.get("CFBundleDisplayName", ""), meta.get("CFBundleName", "")]).lower()
        if "backburner" in text:
            matches.append(bundle)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError("Backburner app not found; pass --bundle-id explicitly")
    raise RuntimeError("multiple Backburner-like apps found; pass --bundle-id: " + ", ".join(matches))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tail", type=Path, help="split tail GGUF made by scripts/split-gguf.py")
    ap.add_argument("--bundle-id", help="Backburner bundle id; auto-detected by display/name when omitted")
    ap.add_argument("--udid", help="iPhone UDID when multiple devices are connected")
    ap.add_argument("--remote", default="tail.gguf")
    args = ap.parse_args()

    if not args.tail.is_file():
        print(f"missing tail: {args.tail}", file=sys.stderr)
        return 2
    try:
        bundle = args.bundle_id or find_backburner_bundle(args.udid)
        cmd = [sys.executable, "-m", "pymobiledevice3", "apps", "push"]
        if args.udid:
            cmd += ["--udid", args.udid]
        cmd += [bundle, str(args.tail.resolve()), args.remote, "--documents"]
        print(f"Pushing {args.tail.name} -> {bundle}/Documents/{args.remote}")
        subprocess.run(cmd, check=True)
        print("Done. Close and reopen Backburner on the iPhone so it loads the new tail.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"push failed (exit {exc.returncode})", file=sys.stderr)
        return exc.returncode or 1
    except Exception as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
