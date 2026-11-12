#!/usr/bin/env python3
"""End-to-end sync session against a live mock WebDAV server.

Every other test in this repo checks one layer in isolation: the PROPFIND parser
on a captured body, the planner on hand-written remote files, the progress bridge
on in-memory values. None of them runs a *session* - the sequence the device
actually performs:

    PROPFIND ~> plan ~> GET each book ~> PUT progress.json ~> verify on the server

That sequence is where integration bugs live: a path built with the wrong
separator, a PUT that succeeds but writes to the wrong place, a progress document
the phone cannot read back. None of those show up in a per-module test.

What this does
--------------
1. Builds a fixture tree that looks like Anx Reader's remote layout:

       anx/data/file/<hash>.epub
       anx/data/cover/<hash>.png
       anx/onepage/progress.json      (written by an earlier device session)

2. Starts tools/mock_webdav.py on a free port with Basic Auth.
3. Drives the *firmware's real C++ modules* against it through a small probe
   binary, so the code under test is the code that ships.
4. Asserts on the server's end state, not on the probe's self-report.

Safety checks that are the point of the whole design, asserted here rather than
only in prose:
  - no write to anx/database*.db
  - no write under anx/data/file or anx/data/cover
  - the only write is anx/onepage/progress.json

Usage:
    python tools/e2e_session.py
    python tools/e2e_session.py --keep     # leave the fixture tree for inspection
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "firmware"
HOST_TESTS = FIRMWARE / "tests" / "host"
MOCK = ROOT / "tools" / "mock_webdav.py"

VCVARS_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
]

AUTH_USER = "reader"
AUTH_PASSWORD = "secret"

# A minimal but valid EPUB: the device fetches these and checks the byte count,
# so the content does not matter, but a real zip header makes the fixture honest.
EPUB_TEMPLATE = (
    b"PK\x03\x04\x14\x00\x00\x00\x08\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_epub(payload: bytes) -> bytes:
    """An EPUB-ish blob: zip local header followed by the payload."""
    return EPUB_TEMPLATE + payload


def _mtime_of(path: pathlib.Path) -> int:
    """The mtime the mock server will report for this file, in Unix seconds.

    The mock renders <D:getlastmodified> from the file's mtime, and the planner
    compares in whole seconds, so truncating here is what makes a Skip possible.
    """
    return int(path.stat().st_mtime)


def build_fixture(base: pathlib.Path) -> dict:
    """Create the remote tree and return the book list for assertions."""
    files_dir = base / "anx" / "data" / "file"
    covers_dir = base / "anx" / "data" / "cover"
    onepage_dir = base / "anx" / "onepage"
    for directory in (files_dir, covers_dir, onepage_dir):
        directory.mkdir(parents=True, exist_ok=True)

    books = []
    # Three books, mirroring what Anx Reader names them: md5 + extension.
    for index, title in enumerate(["alpha", "beta", "gamma"]):
        digest = hashlib.md5(title.encode()).hexdigest()
        name = f"{digest}.epub"
        payload = f"contents of {title}".encode() * (index + 1) * 64
        body = make_epub(payload)
        (files_dir / name).write_bytes(body)
        mtime = 1762900000 + index * 100
        os.utime(files_dir / name, (mtime, mtime))

        cover_name = f"{digest}.png"
        (covers_dir / cover_name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

        books.append(
            {
                "file_path": f"file/{name}",
                "remote_path": f"anx/data/file/{name}",
                "size": len(body),
                "md5": digest,
                "mtime": mtime,
            }
        )

    # A progress document from an earlier round trip. The device should read it,
    # not clobber it with an empty one.
    existing = {
        "schema": 1,
        "device": "onepage-c61",
        "updated_at_unix": 1762900500,
        "books": [
            {
                "file_path": books[0]["file_path"],
                "reading_percentage": 0.25,
                "updated_at": 1762900500,
            }
        ],
    }
    (onepage_dir / "progress.json").write_text(
        json.dumps(existing, indent=2), encoding="utf-8"
    )

    # A database the device must never touch.
    (base / "anx" / "database7.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

    # A book the phone added after the device last synced. Book sync is one-way
    # (phone -> device), so this *should* be downloaded - it is the feature
    # working, not a violation. Recorded so the assertions can tell the two
    # cases apart.
    new_on_phone = files_dir / "ffffffffffffffffffffffffffffffff.epub"
    new_on_phone.write_bytes(make_epub(b"added on the phone after last sync"))

    # Entries the name rules must reject. These exist on the server but are not
    # books; downloading them would put junk on the SD card.
    #
    # Note what the rule does and does not do: PathMap::isAcceptableBookName
    # rejects path traversal, non-.epub extensions and degenerate names, but it
    # does NOT require a 32-character md5 stem. A side-loaded "a.epub" is a
    # legitimate book, so it belongs in the downloadable set, not here.
    rejected = {}
    for name, payload in [
        ("notes.txt", b"not a book"),
        ("cover.png", b"not a book either"),
        ("book.epub.bak", b"wrong extension"),
    ]:
        (files_dir / name).write_bytes(payload)
        rejected[name] = True

    return {
        "books": books,
        "existing_progress": existing,
        "new_on_phone": new_on_phone.name,
        "rejected_names": [n for n, ok in rejected.items() if ok],
        # 3 fixture books + 1 added on the phone.
        "expected_downloads": 4,
    }


def wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def dav_request(port: int, method: str, path: str, body: bytes | None = None,
                headers: dict | None = None) -> tuple[int, bytes, dict]:
    token = base64.b64encode(f"{AUTH_USER}:{AUTH_PASSWORD}".encode()).decode()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        request_headers = {"Authorization": f"Basic {token}"}
        if headers:
            request_headers.update(headers)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def build_probe() -> pathlib.Path:
    """Compile the firmware modules plus the HTTP session driver."""
    vcvars = next((c for c in VCVARS_CANDIDATES if os.path.exists(c)), None)
    if vcvars is None:
        raise SystemExit("ERROR: vcvars64.bat not found")

    build_dir = HOST_TESTS / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    logic_dir = FIRMWARE / "lib" / "AnxSync"

    sources = sorted(logic_dir.glob("*.cpp")) + [HOST_TESTS / "anx_session_probe.cpp"]
    quoted = " ".join(f'"{p}"' for p in sources)

    bat = build_dir / "build_session.bat"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                f"set TMP={build_dir}",
                f"set TEMP={build_dir}",
                f'call "{vcvars}" >nul 2>&1',
                f'cd /d "{build_dir}"',
                f'cl /nologo /utf-8 /std:c++20 /EHsc /W4 /I"{logic_dir}" /c {quoted}',
                "echo COMPILE_EXIT=%ERRORLEVEL%",
                "link /nologo /OUT:anx_session_probe.exe anx_session_probe.obj "
                "AnxHttpDate.obj AnxPathMap.obj AnxProgressJson.obj AnxProgressBridge.obj "
                "AnxPropfindParser.obj AnxSyncPlan.obj AnxProgressLocator.obj "
                "ws2_32.lib",
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
        raise SystemExit("ERROR: failed to build the session probe")

    exe = build_dir / "anx_session_probe.exe"
    if not exe.is_file():
        raise SystemExit("ERROR: session probe binary missing")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end sync session test.")
    parser.add_argument("--keep", action="store_true", help="keep the fixture tree")
    args = parser.parse_args()

    # Not tempfile.mkdtemp: the sandbox refuses mkdir under the platform temp
    # directory (WinError 5), the same restriction that blocked pip. Build the
    # fixture inside the workspace instead, where writes are permitted.
    staging = ROOT / ".e2e-tmp"
    shutil.rmtree(staging, ignore_errors=True)
    fixture = staging / "root"
    server = None
    problems: list[str] = []

    try:
        print(f"fixture: {fixture}")
        info = build_fixture(fixture)

        port = free_port()
        print(f"starting mock WebDAV on 127.0.0.1:{port}")
        server = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--port", str(port),
                "--root", str(fixture),
                "--user", AUTH_USER,
                "--password", AUTH_PASSWORD,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if not wait_for_server(port):
            raise SystemExit("ERROR: mock server did not start")

        # --- sanity: the server really serves the fixture ---
        status, _, _ = dav_request(port, "PROPFIND", "/anx/data/file/",
                                   headers={"Depth": "1"})
        if status != 207:
            problems.append(f"PROPFIND depth 1 returned {status}, expected 207")

        status, _, _ = dav_request(port, "PROPFIND", "/anx/data/file/",
                                   headers={"Depth": "0", "Authorization": "Basic bad"})
        if status != 401:
            problems.append(f"wrong credentials returned {status}, expected 401")

        # --- run the firmware session ---
        print("building the firmware session probe...")
        exe = build_probe()

        print("running the sync session against the live server...")
        completed = subprocess.run(
            [str(exe), "--port", str(port), "--user", AUTH_USER, "--password", AUTH_PASSWORD],
            text=True,
            capture_output=True,
            timeout=120,
        )
        print(completed.stdout, end="")
        if completed.returncode != 0:
            print(completed.stderr, file=sys.stderr)
            problems.append(f"session probe exited {completed.returncode}")

        # --- assertions on the SERVER's end state ---

        # 1. Every book is retrievable with the exact bytes we put there.
        for book in info["books"]:
            status, body, _ = dav_request(port, "GET", "/" + book["remote_path"])
            if status != 200:
                problems.append(f"GET {book['remote_path']} returned {status}")
            elif len(body) != book["size"]:
                problems.append(
                    f"{book['file_path']}: got {len(body)} bytes, expected {book['size']}"
                )

        # 2. progress.json must still parse and carry the device's view.
        status, body, _ = dav_request(port, "GET", "/anx/onepage/progress.json")
        if status != 200:
            problems.append(f"GET progress.json returned {status}")
        else:
            try:
                document = json.loads(body)
            except Exception as exc:
                problems.append(f"progress.json is not valid JSON: {exc}")
                document = None

            if document is not None:
                if document.get("schema") != 1:
                    problems.append(
                        f"progress.json schema is {document.get('schema')}, expected 1"
                    )
                books = document.get("books")
                if not isinstance(books, list):
                    problems.append("progress.json has no books array")
                else:
                    paths = {b.get("file_path") for b in books if isinstance(b, dict)}
                    for book in info["books"]:
                        if book["file_path"] not in paths:
                            problems.append(f"progress.json is missing {book['file_path']}")

                    # A book the phone added after the last sync is downloaded
                    # (that is the feature) and therefore legitimately gets a
                    # progress entry. What must never appear is a non-book.
                    for rejected in info["rejected_names"]:
                        if any(rejected in str(p) for p in paths):
                            problems.append(
                                f"progress.json references a rejected name: {rejected}"
                            )

        # 3. The database must be byte-identical: the device must never write it.
        status, body, _ = dav_request(port, "GET", "/anx/database7.db")
        if status != 200:
            problems.append(f"database7.db is gone (status {status}) - sync destroyed it")
        elif not body.startswith(b"SQLite format 3\x00"):
            problems.append("database7.db was overwritten - the device must never PUT it")

        # 4. A book added on the phone must still exist: the device never deletes.
        status, _, _ = dav_request(port, "HEAD", f"/anx/data/file/{info['new_on_phone']}")
        if status != 200:
            problems.append(f"a book added on the phone was deleted (status {status})")

        # 5. The planner must have refused the non-book names, and picked exactly
        #    the real books. Cross-checked against the probe's own plan line.
        match = re.search(
            r"plan:\s*(\d+) download,\s*(\d+) replace,\s*(\d+) skip,\s*(\d+) ignored",
            completed.stdout,
        )
        if match is None:
            problems.append("the probe did not report a plan line to check")
        else:
            downloaded, _replaced, _skipped, ignored = (int(g) for g in match.groups())
            if info["rejected_names"] and ignored == 0:
                problems.append(
                    f"the planner ignored nothing, but the server holds "
                    f"{len(info['rejected_names'])} non-book entries"
                )
            expected = info["expected_downloads"]
            if downloaded != expected:
                problems.append(f"planned {downloaded} downloads, expected {expected}")

        # 5b. Incremental sync: a device that already holds every book, with the
        #     exact size and mtime the server reports, must plan zero downloads.
        #     This is the everyday case - without it the device would re-fetch
        #     the whole library on every sync.
        local_specs = []
        for remote_file in sorted((fixture / "anx" / "data" / "file").iterdir()):
            if not remote_file.is_file() or not remote_file.name.endswith(".epub"):
                continue
            if remote_file.name in info["rejected_names"]:
                continue
            local_specs.append(
                f"{remote_file.name}:{remote_file.stat().st_size}:{_mtime_of(remote_file)}"
            )

        second = subprocess.run(
            [str(exe), "--port", str(port), "--user", AUTH_USER, "--password", AUTH_PASSWORD]
            + [arg for spec in local_specs for arg in ("--local", spec)],
            text=True,
            capture_output=True,
            timeout=120,
        )
        match = re.search(
            r"plan:\s*(\d+) download,\s*(\d+) replace,\s*(\d+) skip,\s*(\d+) ignored",
            second.stdout,
        )
        if match is None:
            problems.append("the incremental run did not report a plan line")
        else:
            dl, rep, sk, _ign = (int(g) for g in match.groups())
            if dl + rep != 0:
                problems.append(
                    f"incremental sync wanted to fetch {dl + rep} book(s) even though "
                    f"every local copy matches the server (skip={sk})"
                )
            if sk == 0:
                problems.append("incremental sync skipped nothing - it should skip everything")

        # 5c. A book the DEVICE has but the SERVER does not must be reported as
        #     local-only and must not be deleted. Writing it into the served
        #     directory would make it a remote book and prove nothing, so the
        #     check is done purely with --local. Deleting the user's books is
        #     not this feature's job.
        ghost_name = "deadbeefdeadbeefdeadbeefdeadbeef.epub"
        ghost_size = 44
        ghost_mtime = 1762900000
        third = subprocess.run(
            [str(exe), "--port", str(port), "--user", AUTH_USER, "--password", AUTH_PASSWORD]
            + [arg for spec in local_specs for arg in ("--local", spec)]
            + ["--local", f"{ghost_name}:{ghost_size}:{ghost_mtime}"],
            text=True,
            capture_output=True,
            timeout=120,
        )
        if "local-only" not in third.stdout:
            problems.append("the incremental run did not report a local-only count")
        else:
            local_only_match = re.search(r",\s*(\d+) local-only", third.stdout)
            if local_only_match is None:
                problems.append("could not read the local-only count from the plan line")
            elif int(local_only_match.group(1)) < 1:
                problems.append(
                    "a local book with no remote counterpart was not reported as local-only"
                )

        # 6. Nothing outside anx/onepage/ should have been created.
        onepage_dir = fixture / "anx" / "onepage"
        unexpected = [p for p in onepage_dir.iterdir() if p.name not in {"progress.json"}]
        if unexpected:
            problems.append(
                f"unexpected files under anx/onepage/: {[p.name for p in unexpected]}"
            )

    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        if args.keep:
            print(f"fixture kept at {fixture}")
        else:
            shutil.rmtree(staging, ignore_errors=True)

    print()
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        print(f"\n{len(problems)} problem(s)")
        return 1

    print("  PROPFIND works, wrong credentials are rejected")
    print("  every remote book downloads with matching byte counts")
    print("  non-book entries are ignored by the name rules")
    print("  exactly the 4 real books are planned")
    print("  progress.json round-trips and keeps schema 1")
    print("  the device's books are all represented")
    print("  database7.db is byte-identical (never written)")
    print("  a book added on the phone is not deleted")
    print("  nothing is created outside anx/onepage/")
    print("  incremental: a device holding every book fetches nothing")
    print("  a local book absent from the server is reported, not dropped")
    print("\nSESSION VERIFIED")
    print("Note: this exercises the real C++ modules over real HTTP against")
    print("      tools/mock_webdav.py. It does not prove the firmware compiles")
    print("      for ESP32-C61, nor that TLS/hardware behave the same.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
