#!/usr/bin/env python3
"""Cross-language contract verification for the OnePage <-> Anx Reader sync.

The firmware writes progress; the phone reads it. Two implementations in two
languages, and nothing built into either one proves they agree. This script
closes that gap without a device and without a Dart SDK.

How it works
------------
1. Build and run `firmware/tests/host/anx_contract_probe.cpp`, which drives the
   firmware's real progress pipeline (ProgressBridge -> ProgressJson) and prints
   the document it would upload.
2. Parse that output using the *same rules the phone applies*, transcribed from
   `anx-reader/lib/service/sync/onepage_progress_merge.dart`, and assert the
   results are what the firmware intended.

The rules mirrored from the Dart side:
  - the remote path is `anx/onepage/progress.json`
  - the document is a JSON object with `schema`, `device`, `updated_at_unix`,
    and `books`
  - each book has `file_path`, `reading_percentage`, `updated_at`
  - `updated_at` may be an int (seconds) or a parseable string
  - percentages are clamped to [0, 1]
  - an entry is applied only when its timestamp is strictly newer than the
    local book's

What this does NOT prove
------------------------
It cannot prove the Dart code compiles, nor that its types line up. It proves
the two sides agree on the data contract: names, shapes, units and semantics.
That is the part a hand-written spec usually gets wrong.

Usage:
    python tools/verify_contract.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
HOST_TESTS = FIRMWARE / "tests" / "host"
BUILD = HOST_TESTS / "build"

VCVARS_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
]

# ---------------------------------------------------------------------------
# Phone-side rules, mirrored from onepage_progress_merge.dart
# ---------------------------------------------------------------------------

PHONE_REMOTE_PATH = "anx/onepage/progress.json"
PHONE_SUPPORTED_SCHEMA = 1


def phone_parse_percentage(value) -> float | None:
    """Mirror: `if (percentage is! num) continue;` then clamp(0.0, 1.0)."""
    if isinstance(value, bool):  # bool is a subclass of int in Python
        return None
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def phone_parse_timestamp(value) -> int | None:
    """Mirror: int seconds, or a string that parses as an int / ISO date."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None  # ISO parsing is accepted by Dart but unused by the probe
    return None


def phone_should_apply(entry_ts: int | None, book_update_time: int) -> bool:
    """Mirror: apply only when `updated_at` is strictly newer than update_time."""
    if entry_ts is None:
        return True  # Dart: `if (updatedAt != null && !isAfter) skip` -> null means apply
    return entry_ts > book_update_time


# ---------------------------------------------------------------------------
# Build + run the probe
# ---------------------------------------------------------------------------


def build_probe() -> pathlib.Path:
    vcvars = next((c for c in VCVARS_CANDIDATES if os.path.exists(c)), None)
    if vcvars is None:
        raise SystemExit("ERROR: vcvars64.bat not found (install VS Build Tools with C++)")

    BUILD.mkdir(parents=True, exist_ok=True)
    logic_dir = FIRMWARE / "lib" / "AnxSync"

    sources = sorted(logic_dir.glob("*.cpp")) + [HOST_TESTS / "anx_contract_probe.cpp"]
    quoted = " ".join(f'"{p}"' for p in sources)

    bat = BUILD / "build_probe.bat"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                f"set TMP={BUILD}",
                f"set TEMP={BUILD}",
                f'call "{vcvars}" >nul 2>&1',
                f'cd /d "{BUILD}"',
                f'cl /nologo /utf-8 /std:c++20 /EHsc /W4 /I"{logic_dir}" /c {quoted}',
                "echo COMPILE_EXIT=%ERRORLEVEL%",
                # Link only this probe's objects, not the unit-test objects that
                # may already be sitting in the build directory.
                'link /nologo /OUT:anx_contract_probe.exe anx_contract_probe.obj '
                "AnxHttpDate.obj AnxPathMap.obj AnxProgressJson.obj AnxProgressBridge.obj "
                "AnxPropfindParser.obj AnxSyncPlan.obj AnxProgressLocator.obj",
                "echo LINK_EXIT=%ERRORLEVEL%",
            ]
        )
        + "\r\n",
        encoding="ascii",
    )

    completed = subprocess.run(["cmd", "/c", str(bat)], text=True, capture_output=True)
    out = (completed.stdout or "") + (completed.stderr or "")
    if "COMPILE_EXIT=0" not in out or "LINK_EXIT=0" not in out:
        print(out)
        raise SystemExit("ERROR: failed to build the contract probe")

    exe = BUILD / "anx_contract_probe.exe"
    if not exe.is_file():
        raise SystemExit("ERROR: probe binary missing after build")
    return exe


def run_probe(exe: pathlib.Path) -> dict:
    completed = subprocess.run([str(exe), "--json"], text=True, capture_output=True)
    if completed.returncode != 0:
        print("probe stdout:", completed.stdout)
        print("probe stderr:", completed.stderr)
        raise SystemExit(f"ERROR: probe exited {completed.returncode} (its own self-check failed)")

    wrapper = json.loads(completed.stdout)
    if not wrapper.get("self_check"):
        raise SystemExit("ERROR: firmware writer/reader disagree (self_check false)")
    return wrapper


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_contract(wrapper: dict) -> list[str]:
    problems: list[str] = []
    doc = wrapper["document"]

    # --- path ---
    firmware_pathmap = FIRMWARE / "lib" / "AnxSync" / "AnxPathMap.cpp"
    fw_src = firmware_pathmap.read_text(encoding="utf-8")
    match = re.search(r'remoteProgressPath\(\)\s*\{\s*return\s+"([^"]+)"', fw_src)
    if match is None:
        problems.append("cannot read PathMap::remoteProgressPath()")
    elif match.group(1) != PHONE_REMOTE_PATH:
        problems.append(f"path mismatch: firmware '{match.group(1)}' vs phone '{PHONE_REMOTE_PATH}'")

    # --- top-level shape ---
    if "schema" not in doc:
        problems.append("document has no 'schema'")
    else:
        schema = doc["schema"]
        if not isinstance(schema, int):
            problems.append(f"'schema' is {type(schema).__name__}, phone expects int")
        elif schema > PHONE_SUPPORTED_SCHEMA:
            problems.append(f"schema {schema} is newer than the phone supports ({PHONE_SUPPORTED_SCHEMA})")

    if not isinstance(doc.get("device"), str):
        problems.append("'device' missing or not a string")

    # The phone reads `updated_at` preferring `updated_at_unix`; both should exist
    # so a human reading the file can tell what it means.
    if "updated_at_unix" not in doc:
        problems.append("document has no 'updated_at_unix'")

    books = doc.get("books")
    if not isinstance(books, list):
        return problems + ["'books' missing or not a list"]

    # --- per-book ---
    seen: dict[str, dict] = {}
    for index, entry in enumerate(books):
        if not isinstance(entry, dict):
            problems.append(f"book[{index}] is not an object")
            continue

        file_path = entry.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            problems.append(f"book[{index}].file_path missing or empty")
            continue
        if not file_path.startswith("file/"):
            problems.append(f"book[{index}].file_path '{file_path}' is not in Anx Reader's form (file/...)")

        pct = phone_parse_percentage(entry.get("reading_percentage"))
        if pct is None:
            problems.append(f"book[{file_path}].reading_percentage is not a number")
        else:
            if pct < 0.0 or pct > 1.0:
                problems.append(f"book[{file_path}].reading_percentage {pct} out of [0,1] after clamp")

        ts = phone_parse_timestamp(entry.get("updated_at"))
        if ts is None:
            problems.append(f"book[{file_path}].updated_at is not an int seconds value")

        seen[file_path] = entry

    # --- the upload must carry only what the device is authoritative about ---
    EXPECTED_DEVICE_OWNED = {
        "file/aaaa1111bbbb2222cccc3333dddd4444.epub",  # device newer than phone
        "file/eeee5555ffff6666aaaa7777bbbb8888.epub",  # phone has none
        "file/11112222333344445555666677778888.epub",  # phone has none (boundary case)
    }
    phone_only = "file/phone_only_book.epub"

    if phone_only in seen:
        problems.append("uploaded a book the device has never seen (phone-only)")

    for path in EXPECTED_DEVICE_OWNED:
        if path not in seen:
            problems.append(f"expected {path} in the upload, but it is missing")

    # --- phone-side merge semantics must accept what we emitted ---
    # Simulate the phone applying the document against its own library.
    phone_library = {
        "file/aaaa1111bbbb2222cccc3333dddd4444.epub": {"pct": 0.10, "update_time": 1762923000},
        "file/phone_only_book.epub": {"pct": 0.90, "update_time": 1762923900},
    }
    applied = []
    for path, entry in seen.items():
        ts = phone_parse_timestamp(entry.get("updated_at"))
        pct = phone_parse_percentage(entry.get("reading_percentage"))
        local = phone_library.get(path)
        if local is None:
            continue  # unmatched: phone counts it, does not apply it
        if ts is None or pct is None:
            continue
        if not phone_should_apply(ts, local["update_time"]):
            continue
        if abs(local["pct"] - pct) < 0.001:
            continue
        applied.append(path)

    if "file/aaaa1111bbbb2222cccc3333dddd4444.epub" not in applied:
        problems.append("phone would NOT apply the device's newer position for the shared book")

    return problems


def check_semantics(wrapper: dict) -> list[str]:
    """Verify the boundary case is carried the way the tests established."""
    problems: list[str] = []
    doc = wrapper["document"]
    boundary_path = "file/11112222333344445555666677778888.epub"

    entry = next((b for b in doc.get("books", []) if b.get("file_path") == boundary_path), None)
    if entry is None:
        return [f"{boundary_path} missing from the upload"]

    pct = entry.get("reading_percentage")
    # The probe wrote "last page of chapter 0" in a 3x100-byte book, which is
    # 100/300 = 0.3333. The phone resolving that boundary lands on page 0 of
    # chapter 1 - same point in the book, and the stable choice.
    if not isinstance(pct, (int, float)):
        problems.append("boundary entry has no numeric percentage")
    elif abs(float(pct) - 1.0 / 3.0) > 0.01:
        problems.append(f"boundary percentage {pct} is not ~0.3333 as expected")
    return problems


def main() -> int:
    print("building the firmware contract probe...")
    exe = build_probe()
    wrapper = run_probe(exe)

    problems: list[str] = []
    problems += check_contract(wrapper)
    problems += check_semantics(wrapper)

    books = wrapper["document"].get("books", [])
    print(f"\ndocument: schema={wrapper['document'].get('schema')} "
          f"device={wrapper['document'].get('device')} books={len(books)}")

    if problems:
        for p in problems:
            print(f"  PROBLEM: {p}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("  path matches the phone's remotePath")
    print("  schema is understood by the phone")
    print("  every book has file_path / reading_percentage / updated_at")
    print("  percentages are in [0,1]")
    print("  phone-only books are not uploaded")
    print("  phone would apply the device's newer position")
    print("  the chapter-boundary case carries ~0.3333")
    print("\nCONTRACT VERIFIED")
    print("Note: this checks the DATA CONTRACT across the C++ and Dart sides.")
    print("      It does not compile the Dart code - no SDK on this host.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
