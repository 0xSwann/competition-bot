"""Entry helper: opens each new comp in a browser, pre-fills your details, and you submit."""
import json
import os
import queue
import subprocess
import threading
import time
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from . import db, stats
from .config import DATA_DIR
from .feed import _closing_date, is_gleam

PREFILL_JS = (Path(__file__).parent / "prefill.js").read_text(encoding="utf-8")

PROMPT = ("\n  [e] I entered it   [s] skip for good   [l] later   [r] re-fill\n"
          "  [o] open in my normal browser   [q] quit\n  > ")


def uses_gleam(comp, page=None) -> bool:
    """Gleam's fraud checks reject entries from a browser a program is controlling, so those go to your own browser."""
    if "gleam" in (comp["categories"] or "").lower():
        return True
    return page is not None and any("gleam.io" in (f.url or "") for f in page.frames)


def _entered_before_today(row, today) -> bool:
    try:
        return datetime.fromisoformat(row["entered_at"]).astimezone().date() < today
    except (TypeError, ValueError):
        return True


CLOSING_SOON_DAYS = 2


def queue_order(row, today=None):
    """Comps closing in the next couple of days first, then the biggest prizes, then the rest by closing date."""
    today = today or date.today()
    closes = _closing_date(row["closing"])
    soon = closes is not None and (closes - today).days <= CLOSING_SOON_DAYS
    value = stats.prize_hint(row["title"]) or 0
    return (0 if soon else 1, row["closing"] or "9999" if soon else "", -value, row["closing"] or "9999")


def pending(conn, skip_gleam: bool = True) -> list:
    """New comps, plus daily-entry comps you entered on an earlier day.

    Comps closing within two days come first, then the rest from the biggest prize down, so a short session
    covers the best ones. With skip_gleam, Gleam comps already waiting (seen before Gleam was filtered) are left out."""
    today = date.today()
    rows = conn.execute("SELECT * FROM comps WHERE status = 'new' OR (status = 'entered' AND daily = 1)").fetchall()
    rows = [r for r in rows if r["status"] == "new" or _entered_before_today(r, today)]
    open_rows = [r for r in rows if not (_closing_date(r["closing"]) and _closing_date(r["closing"]) < today)]
    if skip_gleam:
        open_rows = [r for r in open_rows if not is_gleam(r)]
    return sorted(open_rows, key=lambda r: queue_order(r, today))


def describe(comp) -> str:
    """The lines shown for each comp in Enter comps."""
    value = stats.prize_hint(comp["title"])
    lines = [f"  Prize: about ${value:,.0f}   " if value else "  ",
             f"Entry: {comp['categories'] or 'unknown'}   Closes: {(comp['closing'] or 'unknown')[:10]}"]
    out = "".join(lines)
    if comp["status"] == "entered":
        out += "\n  Daily entry: you entered this on an earlier day, so it's back for today's entry."
    return out + f"\n  OzBargain: {comp['node_url']}"


def prefill_all_frames(page, me: dict) -> list[str]:
    filled = []
    for frame in page.frames:
        try:
            filled += frame.evaluate(PREFILL_JS, me)
        except Exception:
            pass  # frames can detach or block scripts; skip them
    return filled


BOOKMARK_PAGE = DATA_DIR / "fill-my-details.html"

CHROME_PATHS = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]


def chrome_profile_dir(name: str, local_state: Path | None = None) -> str | None:
    """Maps a Chrome profile's display name (e.g. "Louis") to its folder name (e.g. "Profile 1")."""
    if local_state is None:
        local_state = Path(os.path.expandvars(r"%LocalAppData%\Google\Chrome\User Data\Local State"))
    try:
        cache = json.loads(local_state.read_text(encoding="utf-8"))["profile"]["info_cache"]
    except Exception:
        return None
    for folder, info in cache.items():
        if name.lower() in (folder.lower(), str(info.get("name", "")).lower()):
            return folder
    return None


def open_url(cfg, url: str) -> None:
    """Opens a link in the configured Chrome profile, or the default browser if none is set or found."""
    if cfg.chrome_profile:
        chrome = next((p for p in map(os.path.expandvars, CHROME_PATHS) if Path(p).exists()), None)
        folder = chrome_profile_dir(cfg.chrome_profile)
        if chrome and folder:
            subprocess.Popen([chrome, f"--profile-directory={folder}", url])
            return
        print(f"  Couldn't find Chrome's \"{cfg.chrome_profile}\" profile, so using your default browser.")
    webbrowser.open(url)

TOAST_JS = """
// Forms from another site (in an iframe) are off-limits to the button, so offer to open them on their own.
const boxes = [...document.querySelectorAll('iframe')].filter((f) => {
  const r = f.getBoundingClientRect();
  if (r.width < 200 || r.height < 120 || !/^https?:/.test(f.src || '')) return false;
  try { return !f.contentDocument; } catch (e) { return true; }
});
const t = document.createElement('div');
t.style.cssText = 'position:fixed;z-index:2147483647;top:16px;right:16px;max-width:360px;padding:10px 14px;' +
  'border-radius:8px;background:#1f7a4d;color:#fff;font:14px/1.4 system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.3)';
if (filled.length) {
  t.textContent = 'Filled: ' + [...new Set(filled)].join(', ');
} else if (boxes.length) {
  t.style.background = '#8a5a00';
  t.textContent = "The form is inside a box from another site, which this button can't reach. " +
    'Open it on its own page, then click Fill my details there. ';
  boxes.slice(0, 3).forEach((f) => {
    const a = document.createElement('a');
    a.href = f.src; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = 'Open form (' + new URL(f.src).hostname + ')';
    a.style.cssText = 'display:block;margin-top:6px;color:#fff;font-weight:600;text-decoration:underline';
    t.appendChild(a);
  });
} else {
  t.textContent = 'No empty fields found to fill';
}
document.body.appendChild(t);
setTimeout(() => t.remove(), boxes.length && !filled.length ? 15000 : 3500);
"""


def bookmarklet_js(me: dict) -> str:
    return "(() => {\nconst filled = (" + PREFILL_JS.strip() + ")(" + json.dumps(me) + ");\n" + TOAST_JS + "})();"


def write_bookmark_page(me: dict) -> Path:
    """Writes a local page with a "Fill my details" button to drag onto the bookmarks bar."""
    href = "javascript:" + quote(bookmarklet_js(me), safe="")
    BOOKMARK_PAGE.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARK_PAGE.write_text(f"""<!doctype html><meta charset="utf-8"><title>Fill my details</title>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px">
<h1 style="font-size:24px">Add the "Fill my details" button</h1>
<ol>
<li>Press <b>Ctrl+Shift+B</b> if your bookmarks bar isn't showing.</li>
<li>Drag this green button onto your bookmarks bar:
<p><a href="{href}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#1f7a4d;color:#fff;
text-decoration:none;font-weight:600">Fill my details</a></p></li>
<li>On any entry form, click it. It fills empty fields with your details and never ticks boxes or submits.</li>
</ol>
<p style="color:#666">If you change your details in config.toml, run <code>.venv\\Scripts\\python -m compbot bookmark</code>
and drag the new button over the old one.</p>
</body>""", encoding="utf-8")
    return BOOKMARK_PAGE


def close_active_chrome_tab() -> bool:
    """Closes the tab showing in the most recently used Chrome window (Ctrl+W), then returns focus here.

    Windows only. Does nothing unless Chrome really comes to the front, so keys never go to another window.
    """
    if os.name != "nt":
        return False
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    here = user32.GetForegroundWindow()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if cls.value == "Chrome_WidgetWin_1" and "Google Chrome" in title.value:
            found.append(hwnd)
            return False  # windows come front-to-back, so the first match is the latest Chrome window
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return False
    chrome = found[0]

    def bring_forward(hwnd):
        user32.keybd_event(0x12, 0, 0, 0)  # tapping Alt lets this window take focus
        user32.keybd_event(0x12, 0, 2, 0)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd

    closed = False
    if bring_forward(chrome):
        for vk, up in ((0x11, 0), (0x57, 0), (0x57, 2), (0x11, 2)):  # Ctrl+W
            user32.keybd_event(vk, 0, up, 0)
        time.sleep(0.2)
        closed = True
    if here:
        bring_forward(here)
    return closed


ACTIONS = {"e": "entered", "s": "skipped", "l": "later", "q": "quit"}


def _record(conn, comp, choice, host="", started=None):
    if started is not None:
        stats.log(conn, comp, ACTIONS[choice], time.monotonic() - started)
    if choice == "e":
        conn.execute(
            "UPDATE comps SET status = 'entered', entered_at = ?, entry_domain = ? WHERE node_id = ?",
            (db.now(), host, comp["node_id"]),
        )
    elif choice == "s":
        conn.execute("UPDATE comps SET status = 'skipped' WHERE node_id = ?", (comp["node_id"],))
    conn.commit()


def _read_keys(session, keys=None) -> None:
    """Passes what you type in the terminal to the main loop, alongside the extension's buttons."""
    import sys
    for line in keys or sys.stdin:
        session.actions.put(("terminal", line.strip().lower()[:1], [], None))
    session.actions.put(("terminal", "q", [], None))  # the window was closed


EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"


def extension_prefill_js() -> str:
    return ("// Generated from compbot/prefill.js when Enter comps starts. Edit that file, not this one.\n"
            "self.compbotPrefill = " + PREFILL_JS.rstrip() + ";\n")


def sync_extension() -> None:
    """Keeps the extension's copy of the form filler the same as compbot/prefill.js."""
    target = EXTENSION_DIR / "prefill.js"
    try:
        if target.parent.exists() and (not target.exists() or target.read_text(encoding="utf-8") != extension_prefill_js()):
            target.write_text(extension_prefill_js(), encoding="utf-8")
    except OSError:
        pass


def run_normal(cfg, conn, todo, keys=None) -> None:
    """Opens each comp in your own Chrome. The Competition bot extension fills the form and puts
    Entered / Skip / Later buttons on the page; the keys here work too."""
    from . import helper

    sync_extension()

    session = helper.Session(todo, cfg.me)
    if helper.start(session) is None:
        print("Enter comps already seems to be open in another window. Use that one, or close it and try again.")
        return
    session_start = db.now()
    print(f"{len(todo)} comp(s) to go: closing soon first, then biggest prizes.\n"
          "Each one opens in Chrome. Fill in your details (the extension's Fill button, Alt+Shift+F), check them,\n"
          "do any CAPTCHA or written answer, tick the boxes you agree to, and submit it yourself. Then press\n"
          "Entered, Skip or Later on the page (or Alt+Shift+E / S / L), or type e, s, l here.")
    prompt = "\n  [e] entered   [s] skip for good   [l] later   [o] open it again   [q] quit\n  > "
    threading.Thread(target=_read_keys, args=(session, keys), daemon=True).start()
    hinted = False
    from_terminal_at = 0.0

    while session.comp is not None:
        comp = session.comp
        print(f"\n[{session.i + 1}/{len(session.todo)}] {comp['title']}")
        print(describe(comp))
        started = time.monotonic()
        if not session.extension_active():
            open_url(cfg, helper.BASE + "/go")  # the extension takes over this tab and moves it along from here
        elif from_terminal_at:
            # You pressed a key here; the comp tab follows along within a few seconds. If it doesn't
            # (e.g. it was closed, or shows a page the extension can't reach), open the next comp in a new tab.
            deadline = time.time() + 6
            while time.time() < deadline and session.extension_seen <= from_terminal_at:
                time.sleep(0.25)
            if session.extension_seen <= from_terminal_at:
                open_url(cfg, helper.BASE + "/go")
        print(prompt, end="", flush=True)
        while True:
            try:
                source, choice, hosts, done = session.actions.get(timeout=12)
            except queue.Empty:
                if not hinted and not session.extension_active(60):
                    hinted = True
                    print("\n  (The Chrome extension isn't connected. See \"Chrome extension\" in README.md to add it,"
                          "\n   or use the Fill my details bookmark and the keys here.)" + prompt, end="", flush=True)
                continue
            if choice == "o":
                open_url(cfg, helper.BASE + "/go")
                print(prompt, end="", flush=True)
                continue
            if choice in ACTIONS:
                break
            if source == "terminal":
                print(prompt, end="", flush=True)
        _record(conn, comp, choice, host=" ".join(hosts), started=started)
        from_terminal_at = time.time() if source == "terminal" else 0.0
        if source == "extension":
            print({"e": "entered", "s": "skipped", "l": "later", "q": "quit"}[choice] + " (from Chrome)")
        elif choice in ("e", "s") and cfg.close_tabs and not session.extension_active():
            if not close_active_chrome_tab():
                print("  (Couldn't close the tab automatically; close it yourself.)")
        if choice == "q":
            session.finish(_session_line(conn, session_start))
            if done:
                done.set()
            break
        session.advance()
        if session.comp is None:
            session.finish(_session_line(conn, session_start))
        if done:
            done.set()
    if session.comp is None and not session.summary:
        session.finish(_session_line(conn, session_start))
    _goodbye(conn, session_start)


def _session_line(conn, session_start: str) -> str:
    row = conn.execute(
        "SELECT SUM(action = 'entered'), COALESCE(SUM(seconds), 0) FROM entries WHERE at >= ?", (session_start,)
    ).fetchone()
    return f"This session: {row[0] or 0} entered in {stats._duration(row[1])}."


def _goodbye(conn, session_start: str) -> None:
    print(f"\nDone. {_session_line(conn, session_start)}")
    print(f"All time: {stats.summary(conn)['entered']} entered. Double-click Comp stats for the full picture.")


def launch(pw, channel: str):
    profile = DATA_DIR / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    kwargs = dict(user_data_dir=str(profile), headless=False, no_viewport=True, args=["--start-maximized"])
    for ch in ([channel] if channel else []) + ["msedge", "chrome", None]:
        try:
            return pw.chromium.launch_persistent_context(**({**kwargs, "channel": ch} if ch else kwargs))
        except Exception as e:
            last = e
    raise SystemExit(f"Couldn't start a browser: {last}")


TASK_NAME = "Competition bot"


def _ago(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def background_status(conn) -> str:
    """One line on the background task: when it last checked OzBargain, or a warning if it isn't running."""
    beat = _ago(db.get_state(conn, "heartbeat"))
    feed_ok = _ago(db.get_state(conn, "last_feed_ok"))
    if beat is None or beat > 15 * 60:
        started = False
        if os.name == "nt":
            started = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True).returncode == 0
        return ("Heads up: the background task wasn't running, so new comps and win emails weren't being checked. "
                + ("It's started again now." if started else
                   "Run setup.bat to start it again (or restart your PC)."))
    if feed_ok is None:
        return "The background task is running."
    mins = int(feed_ok // 60)
    when = "just now" if mins < 1 else f"{mins} min ago" if mins < 120 else f"{mins // 60} hours ago"
    warn = "  (that's a while: see data\\compbot.log)" if mins >= 120 else ""
    return f"OzBargain last checked {when}.{warn}"


def run(cfg, conn) -> None:
    print(background_status(conn))
    todo = pending(conn, cfg.exclude_gleam)
    if not todo:
        print("No comps waiting. You're all caught up.")
        return
    if cfg.browser_mode != "edge":
        run_normal(cfg, conn, todo)
        return
    session_start = db.now()
    print(f"{len(todo)} comp(s) to go: closing soon first, then biggest prizes.\n"
          "The browser fills in your details. Check them, do any CAPTCHA or written answer, "
          "tick the boxes you agree to, then press the site's submit button yourself.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = launch(pw, cfg.browser_channel)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, comp in enumerate(todo, 1):
            print(f"\n[{i}/{len(todo)}] {comp['title']}")
            print(describe(comp))
            started = time.monotonic()
            if uses_gleam(comp):
                open_url(cfg, comp["go_url"])
                print("  This is a Gleam comp, so it opened in your normal browser. Gleam blocks the entry browser.\n"
                      "  Your browser's autofill can fill in your name and email.")
            else:
                try:
                    page.goto(comp["go_url"], wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                except Exception as e:
                    print(f"  Couldn't load the page ({e.__class__.__name__}). Press o to try your normal browser.")
                if uses_gleam(comp, page):
                    print("  This page uses Gleam, which blocks the entry browser. Press o to open it in your normal browser.")
                filled = prefill_all_frames(page, cfg.me)
                print(f"  Filled: {', '.join(dict.fromkeys(filled)) or 'nothing found yet (forms may load after a click; press r)'}")

            while True:
                choice = input(PROMPT).strip().lower()[:1]
                if choice == "o":
                    open_url(cfg, page.url if page.url.startswith("http") and not uses_gleam(comp) else comp["go_url"])
                    print("  Opened in your normal browser.")
                    continue
                if choice == "r":
                    filled = prefill_all_frames(page, cfg.me)
                    print(f"  Filled: {', '.join(dict.fromkeys(filled)) or 'nothing new'}")
                    continue
                if choice in ("e", "s", "l", "q"):
                    break
            _record(conn, comp, choice, "" if uses_gleam(comp) else (urlparse(page.url).hostname or ""), started)
            if choice == "q":
                break
        ctx.close()
    _goodbye(conn, session_start)
