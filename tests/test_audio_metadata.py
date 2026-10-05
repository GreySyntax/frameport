"""Exercise metadata retirement with synthetic frames, without game files."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_audio_metadata_retirement(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("Native metadata regression test needs a host C compiler")
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / ("audio-metadata-test.exe" if os.name == "nt" else "audio-metadata-test")
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(root / "native/adapter"),
                    str(root / "tests/fixtures/src/audio_metadata_test.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
