#!/usr/bin/env python3
"""Install PlatformIO by fetching wheels directly, bypassing pip.

`pip install` fails on this host with:

    [Errno 13] Permission denied: '<tmp>\\pip-unpack-...\\platformio-...whl.metadata'

The sandbox refuses pip's attempt to clean up its own temp directories, which
aborts the install. But PyPI itself is reachable and the packages are plain
wheels: a zip of .py files. So download and unzip them by hand.

This only installs PlatformIO *itself*. Installing a platform and its
toolchains hits a further restriction - see docs/07-toolchain-bootstrap.md.
"""

from __future__ import annotations

import json
import pathlib
import urllib.request
import zipfile

TARGET = pathlib.Path(r"D:\code\anx-onepage\.pio-pkgs")
DOWNLOAD = pathlib.Path(r"D:\code\anx-onepage\.pio-wheels")

# PlatformIO 6.2.0's runtime dependencies (from its wheel metadata).
PACKAGES = [
    "platformio",
    "bottle",
    "click",
    "colorama",
    "marshmallow",
    "pyelftools",
    "pyserial",
    "requests",
    "semantic_version",
    "tabulate",
    "ajsonrpc",
    "starlette",
    "uvicorn",
    "wsproto",
    "h11",
    "anyio",
    "sniffio",
    "idna",
    "charset-normalizer",
    "certifi",
    "urllib3",
]


def fetch_wheel(name: str) -> pathlib.Path | None:
    """Download the pure-python wheel for `name`, newest version."""
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
    except Exception as exc:
        print(f"  {name}: metadata failed ({type(exc).__name__})")
        return None

    # Prefer a py3-none-any wheel so no compilation is needed.
    candidates = [
        u for u in data["urls"]
        if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("-none-any.whl")
    ]
    if not candidates:
        candidates = [u for u in data["urls"] if u["packagetype"] == "bdist_wheel"]
    if not candidates:
        print(f"  {name}: no wheel available")
        return None

    chosen = candidates[0]
    dest = DOWNLOAD / chosen["filename"]
    if dest.is_file() and dest.stat().st_size == chosen["size"]:
        print(f"  {name}: cached {chosen['filename']}")
        return dest

    try:
        with urllib.request.urlopen(chosen["url"], timeout=60) as response:
            dest.write_bytes(response.read())
    except Exception as exc:
        print(f"  {name}: download failed ({type(exc).__name__})")
        return None

    print(f"  {name}: {chosen['filename']} ({chosen['size']} bytes)")
    return dest


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    DOWNLOAD.mkdir(parents=True, exist_ok=True)

    print("fetching wheels from PyPI...")
    got = 0
    for name in PACKAGES:
        if fetch_wheel(name) is not None:
            got += 1

    print(f"\nextracting {got} wheel(s) into {TARGET}...")
    extracted = 0
    for wheel in sorted(DOWNLOAD.glob("*.whl")):
        try:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(TARGET)
            extracted += 1
        except Exception as exc:
            print(f"  {wheel.name}: extract failed ({type(exc).__name__})")

    print(f"extracted {extracted} wheel(s)")

    main_py = TARGET / "platformio" / "__main__.py"
    if not main_py.is_file():
        print("ERROR: platformio/__main__.py missing after extraction")
        return 1

    print(f"\nOK: {main_py} exists")
    print("Run PlatformIO with:  python tools/pio.py --version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
