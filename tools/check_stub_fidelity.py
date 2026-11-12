#!/usr/bin/env python3
"""Check that the host stubs actually match the real firmware headers.

The IO layer (`lib/AnxSyncIo/`) is compiled on this machine against hand-written
stubs in `tests/host/stubs/`, because the real headers pull in SdFat, FreeRTOS and
ESP-IDF. That makes the compile a weaker guarantee than it looks: a stub declares
whatever signature the author *believed* the real API has. If the belief is
wrong, the stub build passes and the real build fails.

That gap is invisible until CI runs, and CI cannot run here (see
docs/07-toolchain-bootstrap.md). So this script closes it from the other side: it
reads the real headers and compares every declaration the stubs stand in for.

What it compares
----------------
For each (class, method) pair:
  - the method exists in the real header
  - every real overload has a counterpart in the stub, and vice versa
  - parameter types match after normalising away names and default arguments
  - the const qualifier matches

It also flags fallible stubs that always report success, because a stub which
never fails makes a host test look like it exercises a failure path when it does
not.

Reads, does not compile. A stub that passes here can still be wrong in a way this
script cannot see (templates, default arguments, overload resolution).

Usage:
    python tools/check_stub_fidelity.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
STUBS = FIRMWARE / "tests" / "host" / "stubs"
REAL = FIRMWARE / "lib" / "hal"

# (class, method, header that declares it)
CHECKS = [
    ("HalStorage", "ready", REAL / "HalStorage.h"),
    ("HalStorage", "exists", REAL / "HalStorage.h"),
    ("HalStorage", "remove", REAL / "HalStorage.h"),
    ("HalStorage", "rename", REAL / "HalStorage.h"),
    ("HalStorage", "ensureDirectoryExists", REAL / "HalStorage.h"),
    ("HalStorage", "writeFile", REAL / "HalStorage.h"),
    ("HalStorage", "readFile", REAL / "HalStorage.h"),
    ("HalStorage", "openFileForRead", REAL / "HalStorage.h"),
    ("HalStorage", "openFileForWrite", REAL / "HalStorage.h"),
    ("HalFile", "read", REAL / "HalStorage.h"),
    ("HalFile", "write", REAL / "HalStorage.h"),
    ("HalFile", "flush", REAL / "HalStorage.h"),
    ("HalFile", "close", REAL / "HalStorage.h"),
    ("HalFile", "fileSize", REAL / "HalStorage.h"),
]


def strip_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def class_body(source: str, class_name: str) -> str | None:
    """Text between `class <name> {` and its matching brace.

    Must skip forward declarations (`class HalFile;`) and base-clause forms
    (`class HalFile : public Print {`), which is why the opening brace is part of
    the match rather than assumed to follow.
    """
    for match in re.finditer(rf"\bclass\s+{class_name}\b[^{{;]*{{", source):
        start = match.end()
        depth = 1
        index = start
        while index < len(source) and depth > 0:
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
            index += 1
        return source[start:index - 1]
    return None


def declarations(body: str, method: str) -> list[str]:
    """All declarations of `method`, as raw signature strings.

    Only the declaration is captured: return type through the matching close
    paren, plus a trailing `const`. The body of an inline definition is skipped
    entirely, because `return f(a, b.c_str(), c);` inside it looks like another
    declaration to a naive regex and produced invented-overload false positives.
    """
    found = []
    search_from = 0
    while True:
        match = re.search(rf"\b{method}\s*\(", body[search_from:])
        if match is None:
            break
        match_start = search_from + match.start()
        match_paren = search_from + match.end()
        line_start = body.rfind("\n", 0, match_start) + 1
        prefix = body[line_start:match_start]
        depth = 1
        index = match_paren
        while index < len(body) and depth > 0:
            if body[index] == "(":
                depth += 1
            elif body[index] == ")":
                depth -= 1
            index += 1
        signature = (prefix + body[match_start:index]).strip()
        tail = body[index:index + 40].lstrip()
        if tail.startswith("const"):
            signature += " const"
        found.append(signature)

        brace = body.find("{", index)
        if brace != -1 and body[index:brace].strip() in {"", "const", "const noexcept"}:
            depth = 1
            scan = brace + 1
            while scan < len(body) and depth > 0:
                if body[scan] == "{":
                    depth += 1
                elif body[scan] == "}":
                    depth -= 1
                scan += 1
            search_from = scan
        else:
            search_from = index
    return found


def split_params(signature: str) -> list[str]:
    open_paren = signature.find("(")
    depth = 1
    index = open_paren + 1
    while index < len(signature) and depth > 0:
        if signature[index] == "(":
            depth += 1
        elif signature[index] == ")":
            depth -= 1
        index += 1
    inner = signature[open_paren + 1:index - 1].strip()
    if not inner or inner == "void":
        return []
    params = []
    depth = 0
    current = ""
    for char in inner:
        if char in "(<":
            depth += 1
        elif char in ")>":
            depth -= 1
        if char == "," and depth == 0:
            params.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current.strip())
    return params


def normalise_type(param: str) -> str:
    """Reduce a parameter to its comparable core: type plus const-ness.

    `const char* path` and `const char*` both become `const char*`. Default
    arguments are dropped because the stub has no reason to repeat them.

    A fragment containing an expression (`path.c_str()`) is not a declaration;
    returning it unchanged keeps the mismatch visible instead of normalising it
    into a false agreement.
    """
    param = param.split("=")[0].strip()
    if not param:
        return ""
    if "(" in param or "." in param:
        return param
    tokens = param.split()
    if len(tokens) > 1 and not tokens[-1].endswith(("*", "&")):
        if tokens[-1] not in {"const", "override"}:
            tokens = tokens[:-1]
    return " ".join(tokens).replace("  ", " ").strip()


def check_pair(class_name: str, method: str, real_path: pathlib.Path) -> list[str]:
    problems: list[str] = []

    if not real_path.is_file():
        return [f"{class_name}::{method}: real header {real_path.name} not found"]

    real_src = strip_comments(real_path.read_text(encoding="utf-8"))
    stub_path = STUBS / "Arduino.h"
    if not stub_path.is_file():
        return [f"stub Arduino.h not found at {stub_path}"]
    stub_src = strip_comments(stub_path.read_text(encoding="utf-8"))

    real_body = class_body(real_src, class_name)
    stub_body = class_body(stub_src, class_name)
    if real_body is None:
        return [f"{class_name}: not declared in {real_path.name}"]
    if stub_body is None:
        return [f"{class_name}: stub does not define this class"]

    real_decls = declarations(real_body, method)
    stub_decls = declarations(stub_body, method)
    if not real_decls:
        return [f"{class_name}::{method}: not declared in the real header"]
    if not stub_decls:
        return [f"{class_name}::{method}: stub does not declare it (the IO layer would not compile)"]

    real_params = {tuple(normalise_type(p) for p in split_params(d)) for d in real_decls}
    stub_params = {tuple(normalise_type(p) for p in split_params(d)) for d in stub_decls}

    # An overload set: the stub must cover every overload the real header has,
    # because a call that resolves in one but not the other is exactly the bug
    # this script exists to find.
    for signature in sorted(real_params - stub_params):
        problems.append(
            f"{class_name}::{method}: real overload ({', '.join(signature) or 'no params'}) "
            f"is missing from the stub"
        )
    for signature in sorted(stub_params - real_params):
        problems.append(
            f"{class_name}::{method}: stub invents overload "
            f"({', '.join(signature) or 'no params'}) that the real header does not have"
        )

    real_const = any(" const" in d for d in real_decls)
    stub_const = any(" const" in d for d in stub_decls)
    if real_const != stub_const:
        problems.append(
            f"{class_name}::{method}: const qualifier differs "
            f"(real {'const' if real_const else 'non-const'}, "
            f"stub {'const' if stub_const else 'non-const'})"
        )

    return problems


def check_permissive_stubs() -> list[str]:
    """Flag fallible stubs that always report success.

    Not a compile error - it is why a host test can look like it covers a failure
    path when the stub never takes it.
    """
    problems: list[str] = []
    stub_path = STUBS / "Arduino.h"
    if not stub_path.is_file():
        return problems
    source = strip_comments(stub_path.read_text(encoding="utf-8"))

    fallible = [
        ("HalStorage", "exists"),
        ("HalStorage", "remove"),
        ("HalStorage", "rename"),
        ("HalStorage", "writeFile"),
        ("HalStorage", "ensureDirectoryExists"),
        ("HalStorage", "openFileForRead"),
        ("HalStorage", "openFileForWrite"),
    ]
    for class_name, method in fallible:
        body = class_body(source, class_name)
        if body is None:
            continue
        for _ in re.finditer(rf"\b{method}\s*\([^)]*\)\s*(?:const\s*)?{{\s*return\s+true\s*;", body):
            problems.append(
                f"{class_name}::{method} stub always returns true; a host test cannot "
                f"then exercise the failure path it claims to cover"
            )
    return problems


def main() -> int:
    problems: list[str] = []
    checked = 0

    for class_name, method, real_path in CHECKS:
        problems.extend(check_pair(class_name, method, real_path))
        checked += 1

    problems.extend(check_permissive_stubs())

    print(f"checked {checked} declaration(s) against the real headers")
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("  every stubbed method exists in the real header")
    print("  every overload set matches")
    print("  const qualifiers match")
    print("  no fallible stub silently always succeeds")
    print("\nSTUB FIDELITY OK")
    print("Note: this compares declarations only. Templates, default arguments")
    print("      and overload resolution are still unverified without a real")
    print("      cross-compile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
