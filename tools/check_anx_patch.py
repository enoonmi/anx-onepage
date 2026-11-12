#!/usr/bin/env python3
"""Static reference check for the Anx Reader progress-merge patch.

The anx-reader fork is Flutter/Dart, and this machine has no Dart SDK: `dart`
and `flutter` are both absent, and there is no `.dart_tool`, so even `dart
analyze` is unavailable. That means the patch cannot be compiled or analysed
here.

What *can* be checked, and catches the errors that actually happen when writing
Dart blind, is that every symbol the patch reaches for exists:

  1. every `import 'package:anx_reader/...'` resolves to a real file
  2. every `AnxLog.x(...)` call names a static that exists
  3. every `bookDao.x()` call names a method that exists
  4. every `client.x()` call names a method on SyncClientBase
  5. every `book.<field> =` assignment names a real Book field
  6. the CFI is never assigned
  7. the merge is actually wired into the sync flow
  8. the remote path matches the firmware's PathMap

This is not the Dart analyser. It does not type-check, it does not check null
safety, and it cannot catch a wrong argument count. It catches typos, renamed
helpers, missing imports and stale method names - the mistakes that would
otherwise only appear on a developer machine with Flutter installed.

Usage, from the integration repo root:
    python tools/check_anx_patch.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
READER = ROOT / "anx-reader"
LIB = READER / "lib"

PATCH_FILE = LIB / "service" / "sync" / "onepage_progress_merge.dart"
SYNC_FILE = LIB / "providers" / "sync.dart"

# Files whose declarations the patch is allowed to rely on.
DECL_SOURCES = {
    "AnxLog": LIB / "utils" / "log" / "common.dart",
    "BookDao": LIB / "dao" / "book.dart",
    "Book": LIB / "models" / "book.dart",
    "SyncClientBase": LIB / "service" / "sync" / "sync_client_base.dart",
}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def check_imports(text: str, name: str) -> list[str]:
    problems = []
    for imp in re.findall(r"import\s+'package:anx_reader/([^']+)'", text):
        if not (LIB / imp).is_file():
            problems.append(f"{name}: import package:anx_reader/{imp} does not exist")
    for imp in re.findall(r"import\s+'([^':]+\.dart)'", text):
        if imp.startswith("package:"):
            continue
        resolved = (LIB / "service" / "sync" / imp).resolve()
        if not resolved.is_file():
            problems.append(f"{name}: relative import '{imp}' does not resolve")
    return problems


def check_anxlog(text: str, name: str) -> list[str]:
    log_src = read(DECL_SOURCES["AnxLog"])
    # AnxLog's helpers are declared without a return type:
    #   static info(String message, [Object? error, StackTrace? stackTrace]) {
    declared = set(re.findall(r"static\s+(?:[\w<>?]+\s+)?(\w+)\s*\(", log_src))
    problems = []
    for method in sorted(set(re.findall(r"AnxLog\.(\w+)\s*\(", text))):
        if method not in declared:
            problems.append(f"{name}: AnxLog.{method}() is not declared in common.dart")
    return problems


def _declared_methods(src: str) -> set[str]:
    """Method names declared on a class in `src`.

    Matches any `name(` that follows a Dart return type, including nested
    generics such as `Future<List<Book>> selectBooks({...}) {`. A naive
    `Future<[^>]*>` stops at the first `>` and misses those.
    """
    pattern = r"(?:Future|Stream|void|int|double|String|bool|List|Map)[\s\w<>?,]*?\s(\w+)\s*\("
    return set(re.findall(pattern, src))


def check_bookdao(text: str, name: str) -> list[str]:
    dao_src = read(DECL_SOURCES["BookDao"])
    declared = _declared_methods(dao_src)
    problems = []
    for method in sorted(set(re.findall(r"\bbookDao\.(\w+)\s*\(", text))):
        if method not in declared:
            problems.append(f"{name}: bookDao.{method}() is not declared in dao/book.dart")
    return problems


def check_client_methods(text: str, name: str) -> list[str]:
    base_src = read(DECL_SOURCES["SyncClientBase"])
    declared = _declared_methods(base_src)
    problems = []
    for method in sorted(set(re.findall(r"\bclient\.(\w+)\s*\(", text))):
        if method not in declared:
            problems.append(f"{name}: client.{method}() is not declared in SyncClientBase")
    return problems


def check_book_fields(text: str, name: str) -> list[str]:
    model_src = read(DECL_SOURCES["Book"])
    declared = set(re.findall(r"^\s+(?:final\s+)?[\w<>?]+\s+(\w+);", model_src, re.MULTILINE))
    problems = []

    # Every `book.<field> = ` assignment must name a real field. This is what
    # catches a renamed or invented field.
    for field in sorted(set(re.findall(r"\bbook\.(\w+)\s*=", text))):
        if field not in declared:
            problems.append(f"{name}: Book.{field} does not exist, but the patch assigns it")

    # The CFI must never be assigned - that is the whole contract with the phone.
    if re.search(r"\.lastReadPosition\s*=", text):
        problems.append(f"{name}: the patch must NOT assign lastReadPosition (CFI is phone-only)")

    return problems


def check_wiring() -> list[str]:
    """The merge must be reachable from the sync flow, or it is dead code."""
    problems = []
    if not SYNC_FILE.is_file():
        return ["providers/sync.dart is missing"]

    sync_src = read(SYNC_FILE)
    if "onepage_progress_merge.dart" not in sync_src:
        problems.append("providers/sync.dart does not import the merge service")
    if "OnepageProgressMerge" not in sync_src:
        problems.append("providers/sync.dart never calls OnepageProgressMerge")
    return problems


def check_remote_path_matches_firmware() -> list[str]:
    """The path the phone reads must equal the path the device writes."""
    problems = []
    if not PATCH_FILE.is_file():
        return [f"patch file missing: {PATCH_FILE}"]
    patch_src = read(PATCH_FILE)
    if "'anx/onepage/progress.json'" not in patch_src:
        problems.append("merge service does not use anx/onepage/progress.json")

    # Cross-check against the firmware's PathMap::remoteProgressPath().
    firmware_pathmap = ROOT / "firmware" / "lib" / "AnxSync" / "AnxPathMap.cpp"
    if firmware_pathmap.is_file():
        fw_src = read(firmware_pathmap)
        if '"anx/onepage/progress.json"' not in fw_src:
            problems.append("firmware PathMap::remoteProgressPath() does not match")
    return problems


def main() -> int:
    problems: list[str] = []

    if not PATCH_FILE.is_file():
        print(f"ERROR: patch not found at {PATCH_FILE}", file=sys.stderr)
        return 1

    patch = read(PATCH_FILE)
    problems += check_imports(patch, "onepage_progress_merge.dart")
    problems += check_anxlog(patch, "onepage_progress_merge.dart")
    problems += check_bookdao(patch, "onepage_progress_merge.dart")
    problems += check_client_methods(patch, "onepage_progress_merge.dart")
    problems += check_book_fields(patch, "onepage_progress_merge.dart")
    problems += check_wiring()
    problems += check_remote_path_matches_firmware()

    print(f"checked {PATCH_FILE.name} against {READER.name}")
    if problems:
        for p in problems:
            print(f"  PROBLEM: {p}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("  package:anx_reader imports resolve")
    print("  AnxLog methods exist")
    print("  bookDao methods exist")
    print("  SyncClientBase methods exist")
    print("  Book fields exist, lastReadPosition is never assigned")
    print("  merge is wired into providers/sync.dart")
    print("  remote path matches the firmware's PathMap")
    print("\nANX PATCH REFERENCE CHECK OK")
    print("Note: no Dart SDK on this host, so this is NOT dart analyze. Type")
    print("      errors, null-safety issues and wrong argument counts remain")
    print("      unverified until Flutter runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
