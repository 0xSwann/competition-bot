"""A small web server on your own PC (127.0.0.1 only) that the Competition bot Chrome extension talks to.

While Enter comps is open, the extension asks it which comp is up, fills the form with your details,
and sends back what you pressed (entered, skip, later). Nothing outside your PC can reach it.
"""
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import stats

PORT = 47615  # extension/background.js uses the same number
BASE = f"http://127.0.0.1:{PORT}"


class Session:
    """The comps to go through and where you're up to. Shared by the terminal and the extension."""

    def __init__(self, todo, me: dict):
        self.todo = list(todo)
        self.me = me
        self.i = 0
        self.lock = threading.Lock()
        self.actions = queue.Queue()  # (source, choice, hosts, done_event)
        self.extension_seen = 0.0
        self.finished = False
        self.summary = ""
        self.entered_sites = {}  # website -> (node id, title, entered at) of a comp you entered there lately

    @staticmethod
    def site(host: str) -> str:
        return (host or "").lower().removeprefix("www.")

    def note_entered(self, comp, hosts) -> None:
        """Remembers the website a comp was entered on (the page itself, not forms embedded in it)."""
        if hosts:
            with self.lock:
                self.entered_sites.setdefault(self.site(hosts[0]), (comp["node_id"], comp["title"], comp["entered_at"] or ""))

    @property
    def comp(self):
        return self.todo[self.i] if not self.finished and self.i < len(self.todo) else None

    def extension_active(self, within: float = 15) -> bool:
        return time.time() - self.extension_seen < within

    def current(self, host: str = "") -> dict:
        with self.lock:
            c = self.comp
            if c is None:
                return {"done": True, "summary": self.summary, "me": self.me}
            same = self.entered_sites.get(self.site(host)) if host else None
            return {
                "sameSite": {"title": same[1], "on": same[2][:10]} if same and same[0] != c["node_id"] else None,
                "done": False, "id": c["node_id"], "title": c["title"], "goUrl": c["go_url"],
                "prize": stats.prize_hint(c["title"]), "entry": c["categories"] or "",
                "closes": (c["closing"] or "")[:10], "daily": c["status"] == "entered",
                "index": self.i + 1, "total": len(self.todo), "me": self.me,
            }

    def advance(self) -> None:
        with self.lock:
            self.i += 1
            if self.i >= len(self.todo):
                self.finished = True

    def finish(self, summary: str) -> None:
        with self.lock:
            self.finished = True
            self.summary = summary


DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>All done</title>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:64px auto;padding:0 16px">
<h1 style="font-size:24px">All done</h1><p>{summary}</p>
<p style="color:#666">You can close this tab. Double-click <b>Comp stats</b> on your desktop for the full picture.</p>"""


GO_PAGE = """<!doctype html><meta charset="utf-8"><title>Opening comp...</title>
<body style="font:16px system-ui,sans-serif;margin:64px auto;max-width:560px;padding:0 16px;color:#555">
<p>Opening <b>{title}</b>...</p>
<script>
setTimeout(function () {{ if (!document.documentElement.dataset.compbot) location.replace({target}); }}, 700);
</script>"""


class Handler(BaseHTTPRequestHandler):
    session: Session = None  # set by start()

    def log_message(self, *args):  # keep the terminal clean
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _from_extension(self) -> bool:
        # Web pages can't add this header to a request to another site without permission we never give,
        # and their Origin would be a website rather than the extension.
        origin = self.headers.get("Origin", "")
        return self.headers.get("X-Compbot") == "1" and (not origin or origin.startswith("chrome-extension://"))

    def do_OPTIONS(self):  # no cross-site permission for web pages
        self._send(403, "{}")

    def do_GET(self):
        s = self.session
        path = self.path.split("?")[0]
        if path == "/go":
            c = s.current()
            target = "/done" if c["done"] else c["goUrl"]
            if "direct=1" in self.path:
                return self._send(302, "", "text/plain", {"Location": target})
            # With the extension, go.js takes it from here. Without it, this page moves on by itself.
            import html
            self._send(200, GO_PAGE.format(target=json.dumps(target).replace("<", "\\u003c"), title=html.escape(c.get("title", "All done"))),
                       "text/html; charset=utf-8")
        elif path == "/done":
            import html
            self._send(200, DONE_PAGE.format(summary=html.escape(s.summary or "No comps waiting.")), "text/html; charset=utf-8")
        elif path == "/api/current":
            if not self._from_extension():
                return self._send(403, '{"error": "forbidden"}')
            s.extension_seen = time.time()
            from urllib.parse import parse_qs, urlsplit
            host = (parse_qs(urlsplit(self.path).query).get("host") or [""])[0][:100]
            self._send(200, json.dumps(s.current(host)))
        else:
            self._send(404, '{"error": "not found"}')

    def do_POST(self):
        s = self.session
        if self.path != "/api/action" or not self._from_extension():
            return self._send(403, '{"error": "forbidden"}')
        s.extension_seen = time.time()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, '{"error": "bad json"}')
        choice = str(body.get("choice", ""))[:1]
        cur = s.current()
        if choice not in ("e", "s", "l", "q") or cur["done"] or str(body.get("id")) != cur["id"]:
            return self._send(409, json.dumps(cur))  # out of date: tell the extension what's up now
        hosts = [str(h)[:100] for h in body.get("hosts", []) if isinstance(h, str)][:6]
        done = threading.Event()
        s.actions.put(("extension", choice, hosts, done))
        done.wait(15)
        self._send(200, json.dumps(s.current()))


def start(session: Session):
    """Starts the server in the background. Returns None if the port is taken (e.g. Enter comps is already open)."""
    handler = type("BoundHandler", (Handler,), {"session": session})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    except OSError:
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
