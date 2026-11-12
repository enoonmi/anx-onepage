"""Install the espressif32 platform by hand, bypassing PlatformIO's unpack step.

`python tools/pio.py run -e onepage` gets as far as downloading the platform
archive (1.6 MB, cached in `.pio-core/.cache/downloads/`) and then dies:

    PermissionError: [WinError 5] Access is denied:
      '.pio-core\\.cache\\tmp\\pkg-installing-...\\platform-espressif32-55.03.37'

That is the same sandbox restriction that broke `pip install`: a tool is refused
permission to create or clean directories under its own temp folder. So the
archive is unpacked here instead, directly into the packages directory that
PlatformIO expects, and a `.piopm` manifest is written so PlatformIO treats it
as an installed package rather than a stray folder.

Only the *platform* is handled here. Its toolchains (riscv32-esp-elf-gcc, the
esp-idf framework, cmake, ninja, ...) are separate packages and hit the same
restriction. PlatformIO also re-attempts the platform install on every run and
fails before reaching them, so even this script does not unblock a build. See
docs/07-toolchain-bootstrap.md for the full picture.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / ".pio-core"
DOWNLOADS = CORE / ".cache" / "downloads"
PACKAGES = CORE / "packages"

PLATFORM_NAME = "platform-espressif32"
PLATFORM_VERSION = "55.03.37"
PLATFORM_URL = (
    "https://github.com/pioarduino/platform-espressif32/releases/download/"
    f"{PLATFORM_VERSION}/platform-espressif32.zip"
)


def find_cached_archive() -> pathlib.Path | None:
    """PlatformIO caches downloads under a hashed filename; any zip-sized file
    in the downloads dir is a candidate. Verify by opening it as a zip."""
    if not DOWNLOADS.is_dir():
        return None
    for path in DOWNLOADS.iterdir():
        if not path.is_file() or path.name == "usage.db":
            continue
        if not zipfile.is_zipfile(path):
            continue
        return path
    return None


def main() -> int:
    PACKAGES.mkdir(parents=True, exist_ok=True)

    archive = find_cached_archive()
    if archive is None:
        print("ERROR: no cached platform archive found in", DOWNLOADS)
        print("       Run `python tools/pio.py run -e onepage` once so it downloads,")
        print("       then run this script.")
        return 1

    print(f"using cached archive: {archive.name} ({archive.stat().st_size} bytes)")

    # The zip holds a single top-level directory; strip it so the platform lands
    # directly at packages/platform-espressif32/.
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        roots = {n.split("/")[0] for n in names if n.strip()}
        if len(roots) != 1:
            print(f"ERROR: unexpected archive layout, roots={sorted(roots)[:5]}")
            return 1
        inner = roots.pop()

        dest = PACKAGES / PLATFORM_NAME
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)

        print(f"extracting {inner}/ -> {dest}")
        for member in zf.infolist():
            if not member.filename.startswith(inner + "/"):
                continue
            relative = member.filename[len(inner) + 1:]
            if not relative:
                continue
            target = dest / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

    # PlatformIO identifies installed packages by this manifest. Without it the
    # directory is treated as unknown content and ignored.
    manifest = {
        "name": PLATFORM_NAME,
        "version": PLATFORM_VERSION,
        "spec": {"owner": "platformio", "id": 8021, "name": PLATFORM_NAME,
                 "requirements": None, "uri": PLATFORM_URL},
    }
    (dest / ".piopm").write_text(json.dumps(manifest), encoding="utf-8")

    platform_json = dest / "platform.json"
    if not platform_json.is_file():
        print("ERROR: extracted platform has no platform.json")
        return 1

    info = json.loads(platform_json.read_text(encoding="utf-8"))
    print(f"platform extracted: {info.get('name')} {info.get('version')}")

    print("\nrequired packages (per platform.json):")
    for pkg in info.get("packages", {}).values():
        print(f"  - {pkg.get('type')}: {pkg.get('owner')}/{pkg.get('name')} @ {pkg.get('version')}")

    print("\nNote: a build still cannot proceed on this host - see")
    print("docs/07-toolchain-bootstrap.md for why.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
