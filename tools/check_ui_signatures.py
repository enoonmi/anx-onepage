#!/usr/bin/env python3
"""Signature-level check for the Anx Sync UI activities.

`check_ui_refs.py` already verifies that every method the activities call
*exists* somewhere. That catches a typo, but not a call that passes two arguments
to a function wanting four - the compiler would catch it, and there is no
compiler here: GfxRenderer pulls in EpdFont, which uses GCC's
`__attribute__((packed))` and will not build under MSVC.

So this script reads the real headers and compares *call sites* against them:
argument count, and the shape of each argument (string literal vs number vs
bool). It cannot type-check an expression, but argument count plus literal shape
catches the mistakes that actually happen when writing UI code blind:
a missing parameter, or a bool passed where an int belongs.

What it reads
-------------
  src/components/themes/BaseTheme.h  BaseTheme::draw* signatures (GUI expands to
                                     UITheme::getInstance().getTheme(), so the
                                     declarations live there, not in UITheme.h)
  lib/GfxRenderer/GfxRenderer.h      renderer.draw*/clearScreen/...
  src/MappedInputManager.h           mapLabels / wasPressed

What it cannot see
------------------
Templates, overload selection, conversions, and anything behind a typedef. A
pass here means "the call shapes are plausible", not "this compiles".

Default arguments are honoured: a declaration `clearScreen(uint8_t color = 0xFF)`
accepts the call `clearScreen()`, so the minimum arity is the index of the first
defaulted parameter rather than the full parameter count.

Usage:
    python tools/check_ui_signatures.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"

UI_FILES = sorted(
    (FIRMWARE / "src" / "activities" / "settings").glob("AnxSync*.cpp")
)

HEADERS = {
    "theme": FIRMWARE / "src" / "components" / "themes" / "BaseTheme.h",
    "renderer": FIRMWARE / "lib" / "GfxRenderer" / "GfxRenderer.h",
    "input": FIRMWARE / "src" / "MappedInputManager.h",
    "storage": FIRMWARE / "lib" / "hal" / "HalStorage.h",
}


def strip_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def collect_signatures(source: str, name: str) -> list[list[str]]:
    """Parameter-type lists for every declaration of `name` in `source`."""
    signatures = []
    for match in re.finditer(rf"\b{name}\s*\(", source):
        depth = 1
        index = match.end()
        while index < len(source) and depth > 0:
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
            index += 1
        inner = source[match.end():index - 1].strip()
        if not inner:
            signatures.append([])
            continue
        params, depth, current = [], 0, ""
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
        signatures.append(params)
    return signatures


def split_call_args(call_text: str) -> list[str]:
    """Arguments of a call, given the text inside the parentheses."""
    args, depth, current = [], 0, ""
    for char in call_text:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        args.append(current.strip())
    return args


def call_sites(source: str, receiver: str, method: str) -> list[list[str]]:
    """Argument lists for every `<receiver>.<method>(...)` in the file."""
    sites = []
    for match in re.finditer(rf"\b{receiver}\.{method}\s*\(", source):
        depth = 1
        index = match.end()
        while index < len(source) and depth > 0:
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
            index += 1
        sites.append(split_call_args(source[match.end():index - 1]))
    return sites


def shape_of(arg: str) -> str:
    """A coarse category for an argument, enough to catch a swapped argument."""
    arg = arg.strip()
    if arg.startswith('"') or arg.startswith("tr(") or arg.startswith("STR_"):
        return "text"
    if arg in {"true", "false"}:
        return "bool"
    if re.fullmatch(r"-?\d+", arg):
        return "int"
    if re.fullmatch(r"-?\d*\.\d+", arg):
        return "float"
    if arg.startswith("&") or arg.startswith("*"):
        return "pointer"
    return "expr"


def normalise_param_shape(param: str) -> str:
    """The category a parameter expects, matched against shape_of()."""
    param = param.split("=")[0].strip()
    if re.search(r"\bbool\b", param):
        return "bool"
    if re.search(r"\b(char|uint8_t)\s*\*|\bconst\s+char\s*\*", param):
        return "text"
    if re.search(r"\b(std::string|String)\b", param):
        return "text"
    if re.search(r"\b(int|size_t|uint32_t|uint16_t|int32_t)\b", param):
        return "int"
    if re.search(r"\b(float|double)\b", param):
        return "float"
    return "any"


def check_method(
    label: str,
    method: str,
    signatures: list[list[str]],
    sites: list[list[str]],
) -> list[str]:
    problems: list[str] = []
    if not signatures:
        return [f"{label}.{method}: no declaration found in the headers"]

    # A C++ default argument lets a call omit that parameter and every one after
    # it, so a signature's minimum arity is the index of its first defaulted
    # parameter. Comparing against the full count alone produced false
    # "0 arguments, but the header accepts 1" reports for calls like
    # clearScreen() when the declaration is clearScreen(uint8_t color = 0xFF).
    def arity_range(params: list[str]) -> tuple[int, int]:
        first_default = next(
            (index for index, param in enumerate(params) if "=" in param),
            len(params),
        )
        return first_default, len(params)

    ranges = [arity_range(params) for params in signatures]
    minimum = min(lo for lo, _hi in ranges)
    maximum = max(hi for _lo, hi in ranges)

    for index, args in enumerate(sites, start=1):
        if not (minimum <= len(args) <= maximum):
            problems.append(
                f"{label}.{method} call #{index}: {len(args)} argument(s), "
                f"but the header accepts {minimum}..{maximum} "
                f"(defaults allow the shorter form)"
            )
            continue

        candidates = [
            params for params in signatures
            if arity_range(params)[0] <= len(args) <= arity_range(params)[1]
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda params: abs(len(params) - len(args)))

        for position, (arg, param) in enumerate(zip(args, best)):
            expected = normalise_param_shape(param)
            actual = shape_of(arg)
            if expected == "any" or actual == "expr":
                continue
            if expected != actual:
                problems.append(
                    f"{label}.{method} call #{index}, argument {position + 1}: "
                    f"passed a {actual} where the header expects {expected} "
                    f"({param})"
                )
    return problems


def main() -> int:
    problems: list[str] = []

    if not UI_FILES:
        print("ERROR: no AnxSync*.cpp found under src/activities/settings", file=sys.stderr)
        return 1

    for role, path in HEADERS.items():
        if not path.is_file():
            problems.append(f"header for {role} not found: {path}")
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        return 1

    theme_src = strip_comments(HEADERS["theme"].read_text(encoding="utf-8"))
    renderer_src = strip_comments(HEADERS["renderer"].read_text(encoding="utf-8"))
    input_src = strip_comments(HEADERS["input"].read_text(encoding="utf-8"))

    # (label, receiver used at the call site, header source)
    targets = [
        ("BaseTheme", "GUI", theme_src),
        ("GfxRenderer", "renderer", renderer_src),
        ("MappedInputManager", "mappedInput", input_src),
    ]

    checked = 0
    for ui_file in UI_FILES:
        source = strip_comments(ui_file.read_text(encoding="utf-8"))
        for label, receiver, header_src in targets:
            for method in sorted(set(re.findall(rf"\b{receiver}\.(\w+)\s*\(", source))):
                sites = call_sites(source, receiver, method)
                signatures = collect_signatures(header_src, method)
                problems.extend(check_method(label, method, signatures, sites))
                checked += 1

    print(f"checked {checked} call-site group(s) across {len(UI_FILES)} file(s)")
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("  every GUI/renderer/mappedInput call matches a real signature")
    print("  argument counts are within the declared range")
    print("  literal arguments have the shape the parameter expects")
    print("\nUI SIGNATURES OK")
    print("Note: this is not a compile. GfxRenderer pulls in EpdFont, which uses")
    print("      GCC's __attribute__((packed)) and will not build under MSVC, so")
    print("      types, templates and overload resolution remain unverified")
    print("      until a real cross-compile runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
