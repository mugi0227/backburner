"""The pinned source patch must be repeatable and refuse unknown sources."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
FILES = ("tools/split-prefill/tail-server.h", "tools/split-prefill/tail-client.h",
         "src/llama-split.cpp", "src/CMakeLists.txt", "common/infernet-toggles.cpp")


class PatchTests(unittest.TestCase):
    def test_reapply_and_refuse_drift_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            for rel in FILES:
                dest = checkout / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "llama.cpp" / rel, dest)
            cmd = [sys.executable, str(ROOT / "windows/apply_windows_port.py"), str(checkout)]
            subprocess.run(cmd, check=True, capture_output=True)
            first = {rel: (checkout / rel).read_bytes() for rel in FILES}
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual(first, {rel: (checkout / rel).read_bytes() for rel in FILES})
            source = checkout / "src/CMakeLists.txt"
            source.write_bytes(source.read_bytes() + b"\n# local edit\n")
            before = {rel: (checkout / rel).read_bytes() for rel in FILES}
            refused = subprocess.run(cmd, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(before, {rel: (checkout / rel).read_bytes() for rel in FILES})


if __name__ == "__main__":
    unittest.main()
