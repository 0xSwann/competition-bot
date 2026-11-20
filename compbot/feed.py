"""Polls the OzBargain competitions RSS feed and records new comps."""
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

from . import db, discord

USER_AGENT = "compbot/1.0 (personal use; polls the competitions RSS feed every ~20 minutes)"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _closing_date(text: str):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    comps = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = item.findtext("description") or ""
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        meta = next((el for el in item if _local(el.tag) == "meta"), None)
        attrs = meta.attrib if meta is not None else {}

        m = re.search(r"/(?:competition|node)/(\d+)", link) or re.search(r"(\d+)", item.findtext("guid") or "")
        if not m:
            continue
        node_id = m[1]

        go_url = attrs.get("url", "")
        if "/goto/" not in go_url:
            g = re.search(r'href="([^"]*/goto/\d+)"', desc)
            go_url = g[1] if g else ""
        if go_url.startswith("/"):
            go_url = "https://www.ozbargain.com.au" + go_url
        if not go_url:
            # OzBargain's "Go to" link for a comp uses its node id and redirects to the entry page.
            go_url = f"https://www.ozbargain.com.au/goto/{node_id}"

        text = re.sub(r"<[^>]+>", " ", desc)
        comps.append({
            "node_id": node_id,
            "title": title,
            "node_url": link,
            "go_url": go_url,
            "categories": ", ".join(categories),
            "closing": attrs.get("closing", ""),
            "text": re.sub(r"\s+", " ", text).strip(),
        })
    return comps


STATES = {
    "NSW": "new south wales", "VIC": "victoria", "QLD": "queensland", "WA": "western australia",
    "SA": "south australia", "TAS": "tasmania", "NT": "northern territory", "ACT": "australian capital territory",
}
_STATE_WORD = "|".join([*STATES, *STATES.values(), "victorian", "queensland", "tasmanian"])
_RESTRICTION = re.compile(
    rf"\b(?:residents? of|open to|only for|living in)\s+(?:the\s+)?((?:(?:{_STATE_WORD})(?:\s*(?:,|/|&|and|or)\s*)?)+)"
    rf"|\b((?:(?:{_STATE_WORD})(?:\s*(?:,|/|&|and|or)\s*)?)+)\s+(?:residents?|only)\b",
    re.I,
)
_ALIASES = {"victorian": "VIC", "tasmanian": "TAS"}


def restricted_states(text: str) -> set[str]:
    """States a comp says it is limited to, e.g. "NSW residents only" -> {"NSW"}. Empty means no limit found."""
    found = set()
    for m in _RESTRICTION.finditer(text or ""):
        for word in re.split(r"\s*(?:,|/|&|\band\b|\bor\b)\s*", (m[1] or m[2]).strip(), flags=re.I):
            w = word.strip().lower()
            code = _ALIASES.get(w) or next((k for k, v in STATES.items() if w in (k.lower(), v)), None)
            if code:
                found.add(code)
    return found


# Only wording about how often you can *enter* counts. "Daily prizes", "drawn daily" or "daily deals" don't
# mean you can enter again tomorrow: comps like that came back every day and their sites said "already entered".
_DAILY = re.compile(
    r"\b(?:enter|entry|play)\s+(?:once\s+)?(?:again\s+)?(?:daily|every\s+day|each\s+day)\b"
    r"|\bdaily\s+entr(?:y|ies)\b"
    r"|\bentry\s+frequency\W{0,3}daily\b"
    r"|\b(?:one|1)\s+entry\s+(?:per\s+person\s+)?(?:per|a|each)\s+day\b"
    r"|\b(?:enter|entry)\b[^.]{0,30}\bonce\s+(?:a|per|each)\s+day\b"
    r"|\bevery\s+day\s+(?:to|you\s+can)\s+enter\b",
    re.I,
)


def is_daily(comp) -> bool:
    """True if the comp says you can enter once a day (e.g. "enter daily", "one entry per day")."""
    get = comp.get if isinstance(comp, dict) else (lambda k, d="": comp[k] if k in comp.keys() else d)
    return bool(_DAILY.search(" ".join(str(get(k, "") or "") for k in ("categories", "title", "text"))))


def _title_words(title: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d{3})", "", (title or "").lower())  # $1,000 -> $1000
    t = re.sub(r"\[[^\]]*\]", " ", t)  # [VIC], [Members App]
    return " ".join(re.findall(r"[a-z0-9]+", t))


def same_comp(a: str, b: str) -> bool:
    """True when two OzBargain titles look like the same comp posted twice: nearly the same words and the
    same numbers (so "$500 gift card" and "$1,000 gift card" from one shop stay separate)."""
    from difflib import SequenceMatcher
    wa, wb = _title_words(a), _title_words(b)
    if not wa or not wb:
        return False
    if sorted(re.findall(r"\d+", wa)) != sorted(re.findall(r"\d+", wb)):
        return False
    if wa == wb or SequenceMatcher(None, wa, wb).ratio() >= 0.88:
        return True
    # One title is the other plus a few words, e.g. "... from Emporium Melbourne" added on the re-post.
    sa, sb = set(wa.split()), set(wb.split())
    small = min(sa, sb, key=len)
    return len(small) >= 5 and len(sa & sb) / len(small) >= 0.9


def recently_entered(conn, days: int) -> list:
    """Comps you marked entered in the last `days` days (newest first)."""
    if not days or days <= 0:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM comps WHERE status = 'entered' AND entered_at >= ? ORDER BY entered_at DESC", (cutoff,)
    ).fetchall()


def repeat_of(comp, entered_rows):
    """The comp you already entered that this one is a re-post of, or None."""
    for r in entered_rows:
        if r["node_id"] != comp["node_id"] and same_comp(comp["title"], r["title"]):
            return r
    return None


_GLEAM_DOMAIN = re.compile(r"(?:^|[/.\s(\"'])(?:gleam\.io|gle\.am)\b", re.I)


def is_gleam(comp) -> bool:
    """True if the comp is run on Gleam: OzBargain's "Gleam" tag, or a gleam.io / gle.am link."""
    get = comp.get if isinstance(comp, dict) else (lambda k, d="": comp[k] if k in comp.keys() else d)
    cats = [c.strip().lower() for c in (get("categories", "") or "").split(",")]
    if "gleam" in cats:
        return True
    hay = " ".join(str(get(k, "") or "") for k in ("title", "text", "go_url", "node_url"))
    return bool(_GLEAM_DOMAIN.search(hay))


_INSTAGRAM = re.compile(r"\binstagram\b|\binstagr\.am\b|\binsta\b|\big\s+(?:post|story|stories|reel|comp|giveaway)", re.I)


def is_instagram(comp) -> bool:
    """True if the comp is entered on Instagram: OzBargain's "Instagram" tag, an instagram.com link,
    or Instagram mentioned in its title or description."""
    get = comp.get if isinstance(comp, dict) else (lambda k, d="": comp[k] if k in comp.keys() else d)
    cats = [c.strip().lower() for c in (get("categories", "") or "").split(",")]
    if any("instagram" in c for c in cats):
        return True
    hay = " ".join(str(get(k, "") or "") for k in ("title", "text", "go_url", "node_url"))
    return bool(_INSTAGRAM.search(hay))


def is_hidden_platform(comp, cfg) -> bool:
    """Gleam and Instagram comps, when config.toml hides them."""
    return ((getattr(cfg, "exclude_gleam", True) and is_gleam(comp))
            or (getattr(cfg, "exclude_instagram", True) and is_instagram(comp)))


def is_wanted(comp: dict, cfg) -> bool:
    if is_hidden_platform(comp, cfg):
        return False
    cats = [c.strip().lower() for c in comp["categories"].split(",") if c.strip()]
    if any(c in cfg.exclude_categories for c in cats):
        return False
    hay = (comp["title"] + " " + comp.get("text", "")).lower()
    if any(k in hay for k in cfg.exclude_keywords):
        return False
    my_state = str(cfg.me.get("state", "")).strip().upper()
    states = restricted_states(comp["title"] + " " + comp.get("text", ""))
    if my_state in STATES and states and my_state not in states:
        return False
    closes = _closing_date(comp["closing"])
    if closes and closes < date.today():
        return False
    return True


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text


def check(cfg, conn, xml_text: str | None = None) -> list[dict]:
    """Fetch the feed, store unseen comps and alert Discord. Returns the new wanted comps."""
    comps = parse(xml_text if xml_text is not None else fetch(cfg.feed_url))
    first_run = db.get_state(conn, "feed_initialised") is None
    entered = recently_entered(conn, getattr(cfg, "repeat_days", 30))
    new_wanted = []
    for c in comps:
        if conn.execute("SELECT 1 FROM comps WHERE node_id = ?", (c["node_id"],)).fetchone():
            continue
        wanted = is_wanted(c, cfg)
        status = "new" if wanted else "filtered"
        if wanted and repeat_of(c, entered):
            wanted, status = False, "repeat"  # posted again on OzBargain, but you've already entered it
        conn.execute(
            "INSERT INTO comps (node_id, title, node_url, go_url, categories, closing, first_seen, status, daily) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c["node_id"], c["title"], c["node_url"], c["go_url"], c["categories"],
             c["closing"], db.now(), status, int(is_daily(c))),
        )
        if wanted:
            new_wanted.append(c)
    conn.commit()

    if first_run:
        db.set_state(conn, "feed_initialised", db.now())
        discord.post(
            cfg.discord_webhook,
            f"🎟️ Competition bot is running. Here are the **{len(new_wanted)}** open comps "
            "that match your filters. Tap a title to go to its entry page.",
            [discord.comp_embed(c) for c in new_wanted],
            silent=True,  # new comps post without a notification; only wins ping
        )
    elif new_wanted:
        discord.post(
            cfg.discord_webhook,
            f"🎟️ **{len(new_wanted)} new comp{'s' if len(new_wanted) != 1 else ''}** on OzBargain. "
            "Tap a title to go to its entry page.",
            [discord.comp_embed(c) for c in new_wanted],
            silent=True,  # new comps post without a notification; only wins ping
        )
    return new_wanted
