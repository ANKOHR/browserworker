"""A tiny local-only fixture site served by the Python standard library."""

from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class _FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, FixtureHandler)
        self.flaky_clicks = 0
        self.state_lock = threading.Lock()


class FixtureHandler(BaseHTTPRequestHandler):
    server: _FixtureHTTPServer

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in {"/", "/login"}:
            self._html(_LOGIN_PAGE)
        elif path == "/welcome":
            values = parse_qs(parsed.query)
            name = html.escape(values.get("username", [""])[0])
            self._html(_welcome_page(name))
        elif path == "/search":
            self._html(_SEARCH_PAGE)
        elif path == "/results":
            query = html.escape(parse_qs(parsed.query).get("q", [""])[0])
            self._html(_results_page(query))
        elif path == "/flaky":
            with self.server.state_lock:
                self.server.flaky_clicks = 0
            self._html(_FLAKY_PAGE)
        elif path == "/api/flaky-click":
            with self.server.state_lock:
                self.server.flaky_clicks += 1
                count = self.server.flaky_clicks
            if count == 1:
                self._json(503, {"ok": False, "message": "Temporary fixture failure"})
            else:
                self._json(200, {"ok": True, "message": "Flaky action complete"})
        elif path == "/health":
            self._json(200, {"ok": True})
        else:
            self._html("<!doctype html><title>Not found</title><h1>Not found</h1>", 404)

    def _html(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, value):
        data = json.dumps(value, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


_LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>BrowserWorker Fixture Login</title></head><body>
<main><h1>Fixture sign in</h1><form id="login-form" action="/welcome" method="get">
<label>Username <input name="username" id="username" required></label>
<label>Password <input name="password" id="password" type="password" required></label>
<button id="login-submit" type="submit">Sign in</button></form></main></body></html>"""


def _welcome_page(name: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Welcome</title></head><body>
<h1 id="welcome">Welcome, {name}</h1><p id="account-status">Signed in</p>
<a href="/search">Open search</a></body></html>"""


_SEARCH_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Fixture Search</title></head><body>
<h1>Fixture search</h1><form action="/results" method="get"><label>Search <input id="query" name="q"></label>
<button id="search-submit" type="submit">Search</button></form><a href="/flaky">Flaky control</a></body></html>"""


def _results_page(query: str) -> str:
    title = "Deterministic Browser Tasks" if query.lower() == "browser" else "Fixture Result"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Fixture Results</title></head><body>
<h1>Results for <span id="query-value">{query}</span></h1>
<table id="results"><thead><tr><th>title</th><th>category</th></tr></thead><tbody>
<tr><td class="result-title">{title}</td><td class="result-category">fixture</td></tr>
</tbody></table></body></html>"""


_FLAKY_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Flaky Fixture</title></head><body>
<h1>Flaky button</h1><button id="flaky-button" type="button">Run flaky action</button>
<p id="flaky-status" aria-live="polite">Waiting</p><script>
document.querySelector('#flaky-button').addEventListener('click', async () => {
  const response = await fetch('/api/flaky-click', {cache: 'no-store'});
  const result = await response.json();
  document.querySelector('#flaky-status').textContent = result.ok ? result.message : 'Please try again';
});
</script></body></html>"""


class FixtureServer:
    """Start and stop the local fixture HTTP server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.httpd = _FixtureHTTPServer((host, port))
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
        return f"http://{display_host}:{port}"

    def start(self) -> "FixtureServer":
        if self.thread is None:
            self.thread = threading.Thread(target=self.httpd.serve_forever, name="browserworker-fixtures", daemon=True)
            self.thread.start()
        return self

    def close(self) -> None:
        if self.thread is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(timeout=3)
            self.thread = None

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve fixtures until interrupted."""
    httpd = _FixtureHTTPServer((host, port))
    actual_host, actual_port = httpd.server_address[:2]
    print(f"BrowserWorker fixture site: http://{actual_host}:{actual_port}", flush=True)
    print("Press Ctrl+C to stop the local fixture server.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Fixture server stopped.", flush=True)
    finally:
        httpd.server_close()
