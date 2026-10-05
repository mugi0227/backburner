# Backburner on Windows — MVP

This branch is an **unofficial Windows port / experiment** based on
[StayLameBro/backburner](https://github.com/StayLameBro/backburner).
It keeps the upstream iPhone app and split-prefill wire protocol unchanged.

## MVP scope

Target configuration (real-device inference is not yet verified):

- Windows 11 host
- NVIDIA CUDA first, Vulkan/CPU build fallback
- upstream Backburner iPhone app unchanged
- iPhone 15 Pro or newer (iPhone 17 Pro is the primary target)
- Qwen3.8-27B IQ4_XS
- split prefill for contexts up to 64k
- authenticated Wi-Fi tunnel between Windows and iPhone

Not in the first milestone:

- phone-held KV beyond 64k
- iPhone ANE old-key pages
- USB-C 10 Gb/s data path
- Mac-only SME2/Metal/DFlash2 optimizations

The goal is correctness first: prove that Windows runs the head layers while the
iPhone runs the tail layers, then optimize transport and scheduling.

## 1. Prerequisites

Install:

- Windows 11
- Python 3.11+
- CMake + a C++ build toolchain (Visual Studio 2022 Build Tools is fine)
- NVIDIA CUDA Toolkit for the CUDA backend, or Vulkan SDK for Vulkan
- Microsoft Visual C++ 2015-2022 x64 Redistributable for the CI binaries
- Apple Mobile Device Service. `pymobiledevice3` currently recommends the
  standalone Apple iTunes package on Windows for the most complete usbmux behavior.

Then:

```powershell
git clone --recurse-submodules https://github.com/mugi0227/backburner.git
cd backburner
py -m pip install -r windows/requirements.txt
pymobiledevice3 usbmux list
```

Install the normal upstream Backburner app on the iPhone and keep it open in the
foreground while it is being used.

## 2. Build the Windows host

From the repository root:

Clone the fork with `--recurse-submodules`. The engine is pinned to
`1839b78175d4f89f74023a2ced04f4e558abc4d4`; do not update it independently
from the phone app. The patch helper validates its inputs, can be run repeatedly,
and refuses unfamiliar or locally edited source before making changes.

```powershell
powershell -ExecutionPolicy Bypass -File windows/build.ps1 -Backend cuda
```

Fallbacks:

```powershell
powershell -ExecutionPolicy Bypass -File windows/build.ps1 -Backend vulkan
powershell -ExecutionPolicy Bypass -File windows/build.ps1 -Backend cpu
```

The Windows portability patch in this branch enables the split-prefill socket
client on `_WIN32` and links Winsock (`ws2_32`).

## 3. Make and copy the phone tail

Given the same full Qwen3.8-27B IQ4_XS GGUF used by the Windows host:

```powershell
powershell -ExecutionPolicy Bypass -File windows/make_tail.ps1 `
  -Model C:\Models\Qwen3.8-27B-IQ4_XS.gguf

py windows/push_tail.py "$HOME\Models\tail-iq4xs-L40-nohead.gguf"
```

`push_tail.py` auto-detects the installed app by the name `Backburner` and uses
HouseArrest/AFC to copy the tail directly to `Documents/tail.gguf`. If multiple
matches exist, pass `--bundle-id` explicitly.

After the copy, close and reopen Backburner so the tail server loads the file.

## 4. Pair Windows with the iPhone

Pairing is intentionally cable-only. Plug the iPhone into the PC, unlock it,
keep Backburner open, then run:

```powershell
py windows/pair.py
```

This temporarily forwards the phone's control port through usbmux, asks the app
to generate its 256-bit Wi-Fi key, and stores the key under:

```text
%APPDATA%\BackburnerWindows\
```

Pairing does not expose the phone's inference services directly on Wi-Fi. The
phone only accepts the authenticated Noise tunnel on port 50070.

## 5. Start the encrypted tunnel

With PC and iPhone on the same LAN:

```powershell
py windows/start_tunnel.py
```

It creates these localhost forwards:

| Windows localhost | iPhone service |
|---|---|
| 51052 | ggml RPC 50052 |
| 51060 | split-prefill tail 50060 |
| 51061 | control 50061 |
| 51062 | phone-attention 50062 |

The Python client is protocol-compatible with Backburner's
`Noise_NNpsk0_25519_ChaChaPoly_SHA256` tunnel. `test_noise_vectors.py` verifies
its handshake and transport ciphers against the same published Cacophony vector
used by upstream Backburner.

Run the test:

```powershell
py windows/test_noise_vectors.py
py -m unittest discover -s windows -p "test_*.py" -v
```

Expected:

```text
Noise vector: PASS
```

## 6. Run Qwen on Windows + iPhone

In another PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File windows/serve.ps1 `
  -Model C:\Models\Qwen3.8-27B-IQ4_XS.gguf
```

The host sets:

```text
LLAMA_SPLIT_TAIL=127.0.0.1:51060
LLAMA_SPLIT_MIN=512
LLAMA_SPLIT_ONE_BATCH=1
```

and starts the OpenAI-compatible server at:

```text
http://127.0.0.1:8080/v1
```

A sufficiently large prompt should produce a split-prefill message in the host
log and activity in the Backburner iPhone app.

Keep contexts at or below 65,536 tokens for this milestone. `serve.ps1` clears
inherited remote-KV/split-decode settings so they cannot enable unsupported paths.

## CI and packages

The `Windows MVP` workflow runs the protocol, authentication-failure, framing,
key-storage and patch tests on Linux and Windows, then builds and smoke-tests
`llama-server.exe` and `llama-quantize.exe` on Windows 2022. Its
`backburner-windows-cpu` artifact contains a ZIP with the host, Python helpers,
GGUF tools, documentation and both MIT licenses. Models are not included.

For CUDA/Vulkan compile jobs, use **Actions -> Windows MVP -> Run workflow** and
enable `gpu`. These jobs check compilation, not GPU execution or iPhone speed.
The CUDA artifact targets compute capabilities 7.5, 8.6 and 8.9; other GPUs may
need a local build with `-CudaArchitectures` set for that GPU.

CUDA packages also need the CUDA 13.2 Toolkit runtime libraries on `PATH`
(install the toolkit from NVIDIA). `serve.ps1` adds the installed toolkit's
`bin` and `bin/x64` directories to its process search path via `CUDA_PATH`;
for direct executable use, run `windows/cuda_runtime.ps1` in the same PowerShell
session first. The NVIDIA GPU driver is still required for CUDA inference.
Vulkan packages need the Vulkan loader from
the GPU driver. The Visual C++ redistributable listed above is required for all
CI packages. GPU CI checks executable loading, but does not run GPU inference.

CUDA CI first checks DLL loading with a small host executable, before compiling
the kernels. The driver DLL is delay-loaded so `--version` works on a runner
without a GPU. Compilation can exceed an hour; the GPU jobs allow 120 minutes.
Compiled CUDA executables are cached for reruns with the same build inputs,
and the final executable loading check still runs on a cache hit.

Extract a package, install `windows/requirements.txt`, and follow steps 3-6.
For a local build, `windows/package.ps1 -Backend cpu` creates the same ZIP layout.

## Security

This port deliberately preserves upstream's security model. Pairing happens over
USB, the key stays local to the PC and the iPhone Keychain, and Wi-Fi inference
traffic is carried through the authenticated/encrypted Noise tunnel. Do not add
an unauthenticated direct Wi-Fi path to ports 50052/50060/50061/50062.

New pairing keys are protected with Windows DPAPI for the current user. On
Linux/macOS the helper writes a file with mode 0600. Legacy plaintext keys can
still be read; re-pair on Windows to replace them with protected keys. The host
forwarders always bind to 127.0.0.1 and fail at startup if pairing or a port bind
fails. If the phone's Wi-Fi address changes, pass `--phone` to `start_tunnel.py`.

## Attribution and license

Backburner is MIT-licensed. This port retains upstream copyright/license notices
and links to the original project. The llama.cpp fork retains its own MIT
license. This is not an official Backburner release.
