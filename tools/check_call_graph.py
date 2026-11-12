#!/usr/bin/env python3
"""Find public functions that are declared and defined but never called.

Why this exists
---------------
Two rounds in a row, the most serious defect in this project was not a wrong line
of code but a missing edge in the call graph:

  - AnxProgressStore::record / readReaderPosition / save had zero callers, so the
    journal never filled and the upload phase silently sent nothing.
  - AnxWebDavClient had putProgress but no getProgress, mergeFromPhone had no
    production caller, and nothing wrote a merged position back into
    progress.bin, so the phone -> device direction did not exist.

Neither shows up in a unit test of the function itself, and neither shows up in a
compile. Reading the code found both, and reading the code is not a strategy.

What this checks
----------------
For every public member function declared in the AnxSync libraries, count its
call sites outside the header declaration and outside the .cpp definition, with
the host tests excluded. A function with none is reported, because a capability
nothing invokes is either dead weight or a missing wire - and in this project it
has twice been the latter.

Allowed exceptions carry a reason. A function may have no caller when it is an
entry point reached through virtual dispatch, or a tested helper kept for a
contract the production path builds differently.

What this cannot see
--------------------
It is a text search, not a linker. A call through a function pointer or a
std::function is invisible to it. It reports candidates for a human to confirm,
and that is the point: both real defects above would have appeared here.

Usage:
    python tools/check_call_graph.py
    python tools/check_call_graph.py --verbose
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"

# Libraries whose public surface is this feature's own code.
LIB_DIRS = [
    FIRMWARE / "lib" / "AnxSync",
    FIRMWARE / "lib" / "AnxSyncIo",
]

# Where a call is allowed to come from.
SEARCH_DIRS = [
    FIRMWARE / "lib",
    FIRMWARE / "src",
]

# Tests are deliberately excluded: a function only the tests call is exactly the
# defect this check is looking for.
EXCLUDE_PATH_PARTS = {"tests", "build", "stubs"}

# Functions with no production caller on purpose. Each needs a reason, because the
# whole point is that "nobody calls it" is a finding that must be explained.
ALLOWED_UNCALLED = {
    # Entry points: the Activity framework calls these through virtual dispatch,
    # so no textual call site exists anywhere.
    "onEnter": "Activity lifecycle, called by the framework",
    "onExit": "Activity lifecycle, called by the framework",
    "loop": "Activity lifecycle, called by the framework",
    "render": "Activity lifecycle, called by the framework",
    # Singleton accessor, called everywhere by design.
    "getInstance": "singleton accessor",
    # Redundant defence kept deliberately. localBookPath() already rejects a name
    # containing a separator, so a path built from it cannot escape /Books; this
    # second check exists so a future change to the name rules is caught by a
    # test rather than by a user's SD card. It is exercised by
    # tests/host/test_pathmap_plan.cpp.
    "isInsideRoot": "redundant defence, covered by tests, intentionally not wired",
    # Same shape: tested, but no production caller because the server's hrefs are
    # turned into names by PropfindParser::lastSegment() before any path is built.
    "stripBaseUrl": "tested helper, superseded by PropfindParser::lastSegment",
    # Key mapping the unit tests use to prove "file/<name>" round-trips.
    "localPathToAnxFilePath": "tested helper, key built directly in AnxProgressStore",
    # Timestamp-guarded merge, superseded in production by
    # ProgressBridge::mergeFromPhone, which needs the winner list as well.
    "upsert": "tested helper, superseded by ProgressBridge::mergeFromPhone",
}

# Method names so common that a text match is not evidence either way.
TOO_COMMON = {"clear", "load", "save", "parse", "write", "size", "empty", "reset"}


def strip_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def collect_sources() -> list[pathlib.Path]:
    sources = []
    for root in SEARCH_DIRS:
        if not root.is_dir():
            continue
        for pattern in ("*.cpp", "*.h"):
            for path in root.rglob(pattern):
                if any(part in path.parts for part in EXCLUDE_PATH_PARTS):
                    continue
                sources.append(path)
    return sources


def public_declarations(header: pathlib.Path) -> list[str]:
    """Public member function names declared in `header`.

    Only the public section counts. `private:` helpers are implementation detail
    and legitimately may have one caller or none.
    """
    text = strip_comments(header.read_text(encoding="utf-8", errors="replace"))
    names: list[str] = []
    in_public = False

    for line in text.splitlines():
        stripped = line.strip()
        if "public:" in stripped:
            in_public = True
            continue
        if re.match(r"^(private|protected)\s*:", stripped):
            in_public = False
            continue
        if not in_public:
            continue

        match = re.match(
            r"^(?:static\s+|virtual\s+|explicit\s+)*"
            r"(?:const\s+)?[\w:<>,\s\*&]+?\s+(\w+)\s*\(",
            stripped,
        )
        if match is None:
            continue
        name = match.group(1)
        if name in {"if", "for", "while", "return", "sizeof"}:
            continue
        names.append(name)

    return names


def count_call_sites(name: str, sources: list[pathlib.Path], defining_files: set[pathlib.Path]) -> int:
    """Occurrences of `name(` that are calls, not the declaration or definition.

    The header contributes exactly one `name(` - the declaration. The .cpp
    contributes one more - the definition `ReturnType Class::name(`. Everything
    else is a call, including calls from other methods in the same .cpp.

    An earlier version subtracted two from *each* defining file, which erased a
    genuine internal call: `lastSegment` is defined in AnxPropfindParser.cpp and
    called 20 lines later in the same file, and that call was being cancelled out
    against the definition.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    total = 0
    for path in sources:
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        hits = len(pattern.findall(text))
        if hits == 0:
            continue
        if path.suffix == ".h" and path in defining_files:
            hits -= 1  # the declaration
        elif path.suffix == ".cpp" and path in defining_files:
            hits -= 1  # the definition `Class::name(`
        total += max(0, hits)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Find public functions with no callers.")
    parser.add_argument("--verbose", action="store_true", help="list every function checked")
    args = parser.parse_args()

    sources = collect_sources()
    if not sources:
        print("ERROR: no sources found", file=sys.stderr)
        return 1

    findings: list[tuple[str, str]] = []
    checked = 0

    for lib_dir in LIB_DIRS:
        if not lib_dir.is_dir():
            continue
        for header in sorted(lib_dir.glob("*.h")):
            defining = {header, header.with_suffix(".cpp")}
            for name in public_declarations(header):
                checked += 1
                if name in TOO_COMMON:
                    continue
                sites = count_call_sites(name, sources, defining)
                if args.verbose:
                    print(f"  {header.name}: {name} -> {sites} call site(s)")
                if sites > 0:
                    continue
                reason = ALLOWED_UNCALLED.get(name)
                if reason is None:
                    findings.append((header.name, name))
                elif args.verbose:
                    print(f"    allowed: {reason}")

    print(f"checked {checked} public declaration(s) across {len(LIB_DIRS)} libraries")
    if findings:
        print()
        for header_name, name in findings:
            print(f"  UNCALLED: {header_name}: {name}()")
        print(f"\n{len(findings)} public function(s) with no production caller")
        print("Each is either dead code or a missing wire. Check it:")
        print("  - a capability nothing invokes is not a capability that works")
        print("  - if it is an entry point or genuinely deferred, add it to")
        print("    ALLOWED_UNCALLED with a one-line reason")
        return 1

    print("  every public AnxSync function has at least one production caller")
    print("\nCALL GRAPH OK")
    print("Note: text search, not a linker. Calls through function pointers or")
    print("      std::function are invisible to it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
