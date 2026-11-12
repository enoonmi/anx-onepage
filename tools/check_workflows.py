"""Minimal structural validation for the two workflow files.

PyYAML is not installed and `pip install` is blocked on this host, so this checks
the things that actually break a GitHub Actions workflow and can be checked
without a YAML parser:

  - tabs (YAML forbids them for indentation)
  - duplicate top-level keys
  - `on:` triggers present with at least one event
  - every job has `runs-on` and at least one `step` with `run:` or `uses:`
  - no `run: |` block that is empty

It is not a YAML parser and will not catch every syntax error, but it catches
the class of mistake that silently makes a workflow not run at all.

Usage:
    python tools/check_workflows.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The workflows live at ci/ rather than .github/workflows/ because the token
# used to push them has no `workflow` scope (see firmware/ci/README.md). Check
# whichever location holds them, so activating them does not break this script.
_FIRMWARE_WF = ROOT / "firmware" / ".github" / "workflows" / "onepage-build.yml"
if not _FIRMWARE_WF.is_file():
    _FIRMWARE_WF = ROOT / "firmware" / "ci" / "onepage-build.yml"

_READER_WF = ROOT / "anx-reader" / ".github" / "workflows" / "onepage-analyze.yml"
if not _READER_WF.is_file():
    _READER_WF = ROOT / "anx-reader" / "ci" / "onepage-analyze.yml"

WORKFLOWS = [_FIRMWARE_WF, _READER_WF]


def check(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"missing workflow: {path}"]

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- tabs are illegal for indentation ---
    for number, line in enumerate(lines, start=1):
        if "\t" in line:
            problems.append(f"{path.name}:{number}: tab character (YAML requires spaces)")

    # --- top-level keys ---
    top_level = [
        ln.split(":")[0]
        for ln in lines
        if ln and not ln[0].isspace() and not ln.startswith("#") and ":" in ln
    ]
    duplicates = {k for k in top_level if top_level.count(k) > 1}
    if duplicates:
        problems.append(f"{path.name}: duplicate top-level keys {sorted(duplicates)}")

    if "on" not in top_level:
        problems.append(f"{path.name}: no 'on:' trigger")
    if "jobs" not in top_level:
        problems.append(f"{path.name}: no 'jobs:' section")

    # --- jobs ---
    jobs_start = next((i for i, ln in enumerate(lines) if ln.strip() == "jobs:"), None)
    if jobs_start is None:
        return problems + [f"{path.name}: jobs: not found"]

    job_indent = None
    current_job = None
    job_has_runs_on: dict[str, bool] = {}
    job_step_count: dict[str, int] = {}

    for line in lines[jobs_start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if job_indent is None:
            job_indent = indent
        if indent == job_indent and line.rstrip().endswith(":"):
            current_job = line.strip().rstrip(":")
            job_has_runs_on[current_job] = False
            job_step_count[current_job] = 0
            continue
        if current_job is None:
            continue

        stripped = line.strip()
        if stripped.startswith("runs-on:"):
            job_has_runs_on[current_job] = True
        if stripped.startswith(("- name:", "- uses:", "- run:")):
            job_step_count[current_job] += 1

    if not job_has_runs_on:
        problems.append(f"{path.name}: no jobs found under jobs:")

    for job, has_runner in job_has_runs_on.items():
        if not has_runner:
            problems.append(f"{path.name}: job '{job}' has no runs-on")
        if job_step_count.get(job, 0) == 0:
            problems.append(f"{path.name}: job '{job}' has no steps")

    # --- empty run: | blocks ---
    for number, line in enumerate(lines, start=1):
        if re.search(r"run:\s*\|-?\s*$", line):
            indent = len(line) - len(line.lstrip())
            body = []
            for follow in lines[number:]:
                if not follow.strip():
                    body.append("")
                    continue
                if (len(follow) - len(follow.lstrip())) <= indent:
                    break
                body.append(follow)
            if not any(b.strip() for b in body):
                problems.append(f"{path.name}:{number}: 'run: |' block is empty")

    return problems


def main() -> int:
    problems: list[str] = []
    for workflow in WORKFLOWS:
        found = check(workflow)
        status = "OK  " if not found else "BAD "
        print(f"{status}{workflow.parent.parent.parent.name}/{workflow.name}")
        problems.extend(found)

    if problems:
        print()
        for p in problems:
            print(f"  PROBLEM: {p}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("\nboth workflows are structurally valid")
    print("Note: this is not a YAML parser. It catches tabs, duplicate keys,")
    print("      missing runs-on/steps and empty run blocks - the mistakes that")
    print("      stop a workflow from running at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
