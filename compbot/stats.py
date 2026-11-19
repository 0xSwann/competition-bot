"""Entry log and stats: comps entered, time spent, wins and what that works out to per hour."""
import re
from datetime import datetime

from . import db

# If a comp sits on screen longer than this, you probably walked away, so it only counts this much.
MAX_SECONDS = 15 * 60
GOAL_PER_HOUR = 50


def log(conn, comp, action: str, seconds: float) -> None:
    """Records what you did with a comp in Enter comps (entered, skipped, later or quit) and how long it took."""
    conn.execute(
        "INSERT INTO entries (node_id, title, action, at, seconds) VALUES (?, ?, ?, ?, ?)",
        (comp["node_id"], comp["title"], action, db.now(), int(min(max(seconds, 0), MAX_SECONDS))),
    )
    conn.commit()


def prize_hint(title: str) -> float | None:
    """The first dollar amount in a comp's title, e.g. "Win a $1,500 Holiday" -> 1500."""
    m = re.search(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", title or "", re.I)
    if not m:
        return None
    value = float(m[1].replace(",", ""))
    return value * 1000 if m[2] else value


def summary(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "since": q("SELECT MIN(at) FROM entries WHERE action != 'outside'"),
        "entered": q("SELECT COUNT(*) FROM entries WHERE action = 'entered'"),
        "untimed": q("SELECT COUNT(*) FROM entries WHERE action = 'entered' AND seconds IS NULL"),
        "skipped": q("SELECT COUNT(*) FROM entries WHERE action = 'skipped'"),
        "seconds": q("SELECT COALESCE(SUM(seconds), 0) FROM entries"),
        "timed_entries": q("SELECT COUNT(*) FROM entries WHERE action = 'entered' AND seconds IS NOT NULL"),
        "wins": q("SELECT COUNT(*) FROM entries WHERE won = 1"),
        "prize_total": q("SELECT COALESCE(SUM(prize_value), 0) FROM entries WHERE won = 1"),
    }


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {seconds % 60}s" if m else f"{seconds}s"


def _date(iso: str | None) -> str:
    try:
        d = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return "?"
    return f"{d.day} {d:%b %Y}"


def report(conn) -> str:
    s = summary(conn)
    if not s["since"]:
        return "No entries logged yet. They're recorded automatically as you go through Enter comps."
    days = max(1, (datetime.now().astimezone() - datetime.fromisoformat(s["since"]).astimezone()).days + 1)
    lines = [f"Since {_date(s['since'])} ({days} day{'s' if days != 1 else ''})", ""]
    entered = f"Comps entered:  {s['entered']}"
    if s["skipped"]:
        entered += f"   (and {s['skipped']} skipped)"
    lines.append(entered)
    time_line = f"Time spent:     {_duration(s['seconds'])}"
    if s["timed_entries"]:
        time_line += f"   (about {_duration(s['seconds'] / s['timed_entries'])} per entry)"
    lines.append(time_line)
    if s["untimed"]:
        lines.append(f"                ({s['untimed']} entries from before tracking started have no time)")
    lines.append(f"Wins:           {s['wins']}" + (f"   worth ${s['prize_total']:,.0f}" if s["wins"] else ""))
    hours = s["seconds"] / 3600
    if hours >= 0.05:
        rate = s["prize_total"] / hours
        verdict = "above" if rate >= GOAL_PER_HOUR else "below"
        lines.append(f"Return:         ${rate:,.0f} an hour   ({verdict} your ${GOAL_PER_HOUR}/hour mark)")
    return "\n".join(lines)


def find_entered(conn, text: str) -> list:
    words = text.lower().split()
    rows = conn.execute(
        "SELECT * FROM entries WHERE action = 'entered' AND id IN "
        "(SELECT MAX(id) FROM entries WHERE action = 'entered' GROUP BY node_id) ORDER BY at DESC"
    ).fetchall()
    return [r for r in rows if all(w in r["title"].lower() for w in words)]


def set_win(conn, entry_id: int, value: float | None) -> None:
    conn.execute("UPDATE entries SET won = 1, prize_value = ?, won_at = ? WHERE id = ?", (value, db.now(), entry_id))
    conn.commit()


def clear_win(conn, entry_id: int) -> None:
    conn.execute("UPDATE entries SET won = 0, prize_value = NULL, won_at = NULL WHERE id = ?", (entry_id,))
    conn.commit()


def add_outside_win(conn, title: str, value: float | None) -> None:
    """A win from a comp you didn't enter through Enter comps. Counts as a win, not as an entry."""
    now = db.now()
    conn.execute(
        "INSERT INTO entries (node_id, title, action, at, won, prize_value, won_at) VALUES (NULL, ?, 'outside', ?, 1, ?, ?)",
        (title, now, value, now),
    )
    conn.commit()


def _ask_value(default: float | None) -> float | None:
    hint = f" [Enter for ${default:,.0f}]" if default else ""
    while True:
        raw = input(f"  Prize value in dollars{hint}: ").strip().replace("$", "").replace(",", "")
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Type a number, like 250.")


def record_win_interactive(conn) -> None:
    print("\nRecord a win. Type a word or two from the comp's name (e.g. the brand), or press Enter to cancel.")
    while True:
        text = input("  Search: ").strip()
        if not text:
            return
        matches = find_entered(conn, text)[:15]
        if matches:
            break
        print("  No entered comps match that. Try another word.")
    for i, r in enumerate(matches, 1):
        mark = f"  (WON ${r['prize_value'] or 0:,.0f})" if r["won"] else ""
        print(f"  [{i}] {r['title']}  ({_date(r['at'])}){mark}")
    print("  [n] It's not in this list (a comp you entered some other way)")
    pick = input("  Which one? ").strip().lower()
    if pick == "n":
        title = input("  What was it? ").strip() or "Win (not through Enter comps)"
        add_outside_win(conn, title, _ask_value(prize_hint(title)))
        print("  Saved. Congrats!")
        return
    if not pick.isdigit() or not 1 <= int(pick) <= len(matches):
        print("  Cancelled.")
        return
    row = matches[int(pick) - 1]
    if row["won"]:
        if input("  That's already marked as a win. Remove it? [y/N] ").strip().lower() == "y":
            clear_win(conn, row["id"])
            print("  Removed.")
        return
    set_win(conn, row["id"], _ask_value(prize_hint(row["title"])))
    print("  Saved. Congrats!")


def run(conn) -> None:
    """What the Comp stats shortcut runs: shows the numbers and lets you record a win."""
    while True:
        print("\n" + report(conn))
        choice = input("\n  [w] record a win   [Enter] close\n  > ").strip().lower()[:1]
        if choice != "w":
            return
        record_win_interactive(conn)
