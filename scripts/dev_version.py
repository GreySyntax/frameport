#!/usr/bin/env python3
"""Stamp a dev build's version into src/frameport/_version.py (CI only; never committed): the next patch release plus
.dev<build number>, e.g. 0.9.0 -> 0.9.1.dev57. It sorts above the current release and below the next one, so
testers on a dev build get the next release offered as a normal update.

    python scripts/dev_version.py <build number>     # prints the stamped version
"""
import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1] / "src/frameport/_version.py"


def dev_version(current: str, build: int) -> str:
    base = re.match(r"(\d+)\.(\d+)\.(\d+)", current)
    if not base:
        raise ValueError(f"unexpected version {current!r}")
    major, minor, patch = (int(x) for x in base.groups())
    return f"{major}.{minor}.{patch + 1}.dev{int(build)}"


def main() -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    current = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    version = dev_version(current, int(sys.argv[1]))
    VERSION_FILE.write_text(text.replace(f'"{current}"', f'"{version}"', 1), encoding="utf-8")
    print(version)


if __name__ == "__main__":
    main()
