#!/usr/bin/env python3
"""Minimal WebDAV server that mimics Anx Reader's remote layout.

Purpose: exercise the AnxSync network path (PROPFIND -> parse -> plan ->
download) from a developer machine, without a real Nextcloud and without
flashing a reader.

Serves, under the configured base URL:

    anx/data/file/<name>.epub     books (served as application/epub+zip)
    anx/data/cover/<name>.png     covers
    anx/onepage/progress.json     progress pushed by the device

Supported methods: PROPFIND (Depth 0 and 1), GET, HEAD, PUT, DELETE, OPTIONS.
Basic Auth is enforced when --user/--password are given.

Only the Python standard library is used, so this runs anywhere.

Usage:
    python tools/mock_webdav.py --port 8080 --root ./_fixtures
    python tools/mock_webdav.py --port 8080 --root ./_fixtures --user reader --password secret

Then point the firmware (or curl) at:
    http://127.0.0.1:8080/anx/data/file/
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit
import email.utils

MULTISTATUS_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">\n'
MULTISTATUS_FOOTER = "</D:multistatus>\n"


def _http_date(timestamp: float) -> str:
    return email.utils.formatdate(timestamp, usegmt=True)


class MockWebDavHandler(BaseHTTPRequestHandler):
    server_version = "MockWebDAV/1.0"
    protocol_version = "HTTP/1.1"

    # Injected by the factory below.
    root_dir: str = "."
    username: str = ""
    password: str = ""
    verbose: bool = False

    def log_message(self, fmt, *args):
        if self.verbose:
            sys.stderr.write("[mock-webdav] %s - %s\n" % (self.address_string(), fmt % args))

    # ---------- auth ----------

    def _check_auth(self) -> bool:
        if not self.username and not self.password:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
        except Exception:
            return False
        user, _, secret = decoded.partition(":")
        return user == self.username and secret == self.password

    def _require_auth(self) -> bool:
        if self._check_auth():
            return True
        body = b"Unauthorized"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="mock-webdav"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    # ---------- path mapping ----------

    def _local_path(self, url_path: str) -> str:
        """Map a URL path to a filesystem path, refusing traversal."""
        relative = unquote(urlsplit(url_path).path).lstrip("/")
        candidate = os.path.normpath(os.path.join(self.root_dir, relative))
        root_abs = os.path.abspath(self.root_dir)
        candidate_abs = os.path.abspath(candidate)
        if candidate_abs != root_abs and not candidate_abs.startswith(root_abs + os.sep):
            raise ValueError("path escapes root")
        return candidate_abs

    def _href_for(self, local_abs: str, is_dir: bool) -> str:
        relative = os.path.relpath(local_abs, os.path.abspath(self.root_dir))
        if relative == ".":
            relative = ""
        segments = [quote(part) for part in relative.split(os.sep) if part]
        path = "/" + "/".join(segments)
        if is_dir and not path.endswith("/"):
            path += "/"
        return path or "/"

    # ---------- response helpers ----------

    def _send_bytes(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_text(self, code: int, message: str) -> None:
        self._send_bytes(code, message.encode("utf-8"), "text/plain; charset=utf-8")

    # ---------- DAV ----------

    def do_OPTIONS(self):  # noqa: N802
        if not self._require_auth():
            return
        self.send_response(200)
        self.send_header("DAV", "1,2")
        self.send_header("Allow", "OPTIONS,GET,HEAD,PUT,DELETE,PROPFIND,MKCOL")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):  # noqa: N802
        if not self._require_auth():
            return
        try:
            local = self._local_path(self.path)
        except ValueError:
            self._send_error_text(400, "bad path")
            return

        if not os.path.exists(local):
            self._send_error_text(404, "not found")
            return

        depth = self.headers.get("Depth", "1")
        entries = []
        if os.path.isdir(local):
            entries.append(local)
            if depth != "0":
                for name in sorted(os.listdir(local)):
                    entries.append(os.path.join(local, name))
        else:
            entries.append(local)

        parts = [MULTISTATUS_HEADER]
        for entry in entries:
            is_dir = os.path.isdir(entry)
            try:
                stat = os.stat(entry)
                modified = _http_date(stat.st_mtime)
                size = 0 if is_dir else stat.st_size
            except OSError:
                modified = _http_date(0)
                size = 0
            parts.append(" <D:response>\n")
            parts.append("  <D:href>%s</D:href>\n" % self._href_for(entry, is_dir))
            parts.append("  <D:propstat>\n   <D:prop>\n")
            parts.append("    <D:getlastmodified>%s</D:getlastmodified>\n" % modified)
            if is_dir:
                parts.append("    <D:resourcetype><D:collection/></D:resourcetype>\n")
            else:
                parts.append("    <D:getcontentlength>%d</D:getcontentlength>\n" % size)
                parts.append("    <D:resourcetype/>\n")
            parts.append("   </D:prop>\n   <D:status>HTTP/1.1 200 OK</D:status>\n")
            parts.append("  </D:propstat>\n </D:response>\n")
        parts.append(MULTISTATUS_FOOTER)

        body = "".join(parts).encode("utf-8")
        self._send_bytes(207, body, 'application/xml; charset="utf-8"')

    def do_GET(self):  # noqa: N802
        if not self._require_auth():
            return
        try:
            local = self._local_path(self.path)
        except ValueError:
            self._send_error_text(400, "bad path")
            return
        if not os.path.isfile(local):
            self._send_error_text(404, "not found")
            return
        with open(local, "rb") as handle:
            body = handle.read()
        content_type = (
            "application/epub+zip" if local.lower().endswith(".epub") else "application/octet-stream"
        )
        self._send_bytes(200, body, content_type)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_PUT(self):  # noqa: N802
        if not self._require_auth():
            return
        try:
            local = self._local_path(self.path)
        except ValueError:
            self._send_error_text(400, "bad path")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as handle:
            handle.write(body)
        if self.verbose:
            sys.stderr.write("[mock-webdav] PUT %s (%d bytes)\n" % (self.path, len(body)))
        self.send_response(201 if body else 204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):  # noqa: N802
        if not self._require_auth():
            return
        try:
            local = self._local_path(self.path)
        except ValueError:
            self._send_error_text(400, "bad path")
            return
        if not os.path.exists(local):
            self._send_error_text(404, "not found")
            return
        if os.path.isdir(local):
            try:
                os.rmdir(local)
            except OSError:
                self._send_error_text(409, "directory not empty")
                return
        else:
            os.remove(local)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self):  # noqa: N802
        if not self._require_auth():
            return
        try:
            local = self._local_path(self.path)
        except ValueError:
            self._send_error_text(400, "bad path")
            return
        if os.path.exists(local):
            self._send_error_text(405, "already exists")
            return
        os.makedirs(local, exist_ok=True)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


def build_server(
    host: str, port: int, root: str, username: str, password: str, verbose: bool
) -> ThreadingHTTPServer:
    os.makedirs(root, exist_ok=True)

    handler = type(
        "BoundMockWebDavHandler",
        (MockWebDavHandler,),
        {"root_dir": root, "username": username, "password": password, "verbose": verbose},
    )
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mock WebDAV server mimicking Anx Reader's layout.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", default="./_fixtures", help="directory served as the WebDAV root")
    parser.add_argument("--user", default="", help="require this username (Basic Auth)")
    parser.add_argument("--password", default="", help="require this password (Basic Auth)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port, args.root, args.user, args.password, args.verbose)
    host, port = server.server_address[0], server.server_address[1]
    sys.stdout.write("mock WebDAV listening on http://%s:%d\n" % (host, port))
    sys.stdout.write("  root: %s\n" % os.path.abspath(args.root))
    sys.stdout.write("  books: %s/anx/data/file/\n" % ("http://%s:%d" % (host, port)))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\nstopping\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
