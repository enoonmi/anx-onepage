"""A `pio` shim so PlatformIO can be used without a pip install.

`pip install platformio` cannot complete on this host: the sandbox refuses pip's
cleanup of its own temp directories and the install aborts with

    [Errno 13] Permission denied: '<tmp>\\pip-unpack-...\\platformio-...whl.metadata'

`tools/bootstrap_pio.py` works around that by downloading and unzipping the
wheels into `.pio-pkgs/`. This script is the usable front door to that copy.

Usage (from the integration repo root or anywhere):

    python tools/pio.py --version
    python tools/pio.py run -d firmware -e onepage

It behaves like the `pio` executable: the exit code is PlatformIO's.

Note: installing a *platform* still fails on this host for a related reason -
PlatformIO creates temp directories the sandbox then refuses to touch. See
docs/07-toolchain-bootstrap.md. On a machine with normal file permissions, use
`pip install platformio` instead and ignore this script.
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKGS = ROOT / ".pio-pkgs"
CORE_DIR = ROOT / ".pio-core"


def main() -> int:
    if not (PKGS / "platformio").is_dir():
        print(
            "ERROR: PlatformIO is not bootstrapped.\n"
            "       Run:  python tools/bootstrap_pio.py",
            file=sys.stderr,
        )
        return 2

    # Keep every byte PlatformIO writes inside the workspace.
    os.environ.setdefault("PLATFORMIO_CORE_DIR", str(CORE_DIR))
    os.environ.setdefault("PLATFORMIO_SETTING_ENABLE_TELEMETRY", "false")
    os.environ.setdefault("PLATFORMIO_SETTING_CHECK_PLATFORMIO_INTERVAL", "9999")
    os.environ.setdefault("PLATFORMIO_SETTING_CHECK_PRUNE_SYSTEM_INTERVAL", "9999")

    sys.path.insert(0, str(PKGS))

    from platformio.__main__ import main as pio_main  # noqa: PLC0415

    sys.argv = ["pio", *sys.argv[1:]]
    try:
        pio_main()
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
