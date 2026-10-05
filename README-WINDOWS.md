# Backburner Windows MVP

Unofficial native-Windows experiment for
[StayLameBro/backburner](https://github.com/StayLameBro/backburner).

The first milestone reuses the **unmodified upstream iPhone app** and moves the
host side to Windows:

```text
Windows 11 (CUDA / Vulkan)
    │
    │ authenticated Noise tunnel over Wi-Fi
    ▼
iPhone Backburner app
    └── split-prefill tail on the iPhone GPU
```

Current implementation work in this branch:

- Windows pairing via `pymobiledevice3` / usbmux
- direct `tail.gguf` copy into Backburner Documents via HouseArrest/AFC
- protocol-compatible Python port of Backburner's encrypted Wi-Fi tunnel
- native Winsock portability layer for Backburner's split-prefill client
- PowerShell CUDA/Vulkan/CPU build + server launch helpers
- Windows GitHub Actions build job
- upstream Noise test-vector compatibility test
- end-to-end tunnel loopback test

Status: experimental. The encrypted tunnel tests pass, but Windows + iPhone
inference and speed have not been measured on real hardware. Upstream Mac
performance numbers do not apply to this port.

The CPU workflow builds and packages the Windows host on each relevant push.
Manual workflow runs can also compile CUDA and Vulkan by enabling `gpu`.

See **[docs/WINDOWS.md](docs/WINDOWS.md)** for setup and current limitations.

This is not an official Backburner release. Backburner and its llama.cpp fork
are MIT-licensed; upstream copyright/license notices must be retained.
