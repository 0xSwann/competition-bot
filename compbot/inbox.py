"""Watches a Gmail inbox over IMAP for emails that look like competition wins."""
import email
import hashlib
import imaplib
import json
import re
import time
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from urllib.parse import quote

import requests

from . import db, discord

FOLDERS = ["INBOX", "[Gmail]/Spam"]

STRONG_PHRASES = [
    "you've won", "you have won", "you won", "you're a winner", "you are a winner",
    "you’ve won", "you’re a winner", "winner notification", "claim your prize",
    "prize claim", "you are the winner", "you're the winner", "lucky winner",
]
PROMO_PHRASES = [
    "chance to win", "enter to win", "could win", "win a ", "win an ", "thanks for entering",
    "thank you for entering", "you're in", "you’re in", "entry confirmed",
    "you have been entered", "you've been entered", "entry received",
]

SECOND_LEVEL = {"com", "net", "org", "edu", "gov", "co", "asn", "id"}


def base_domain(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _norm(text: str) -> str:
    return text.lower().replace("\u2019", "'").replace("\u2018", "'")


def _has(text: str, phrases) -> bool:
    # Whole phrases only, so "you won" doesn't match "you won't" and "prize" doesn't match "prized".
    return any(re.search(r"(?<![a-z])" + re.escape(_norm(p)) + r"(?![a-z'])", text) for p in phrases)


def score(subject: str, body: str, sender_domain: str, known_domains: set, keywords: list) -> int:
    s, b = _norm(subject), _norm(body)
    points = 0
    if _has(s, STRONG_PHRASES):
        points += 3
    elif _has(b, STRONG_PHRASES):
        points += 2
    if _has(s, keywords):
        points += 1
    # Win words in the body alone don't count: marketing emails are full of "prize" and "winner".
    if sender_domain and base_domain(sender_domain) in known_domains:
        points += 2
    if _has(s, PROMO_PHRASES) and not _has(s, STRONG_PHRASES):
        points -= 2
    return points


def looks_like_win(subject, body, sender_domain, known_domains, keywords) -> bool:
    return score(subject, body, sender_domain, known_domains, keywords) >= 3


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _body_text(msg) -> str:
    plain, html = [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        (plain if ctype == "text/plain" else html).append(text)
    text = "\n".join(plain) if plain else re.sub(r"<[^>]+>", " ", "\n".join(html))
    return re.sub(r"\s+", " ", text)[:20000]


def known_promoter_domains(conn) -> set:
    """Websites you entered comps on. entry_domain can hold several hosts (the page and any form inside it)."""
    rows = conn.execute("SELECT entry_domain FROM comps WHERE status = 'entered' AND entry_domain IS NOT NULL")
    return {base_domain(h) for r in rows for h in (r["entry_domain"] or "").split() if "." in h}


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def entered_brands(conn) -> set:
    """Brand names from the titles of comps you've entered, e.g. "Win a Toaster from Clipsal Home" -> "clipsalhome"."""
    brands = set()
    for r in conn.execute("SELECT title FROM comps WHERE status = 'entered'"):
        m = re.search(r"\bfrom\s+(.+?)\s*(?:\[.*)?$", r["title"], re.I)
        if not m:
            continue
        for part in re.split(r"\s*(?:\+|,|&|\band\b|\bx\b)\s*", m[1], flags=re.I):
            key = _squash(part)
            if len(key) >= 3:
                brands.add(key)
    return brands


def is_known_sender(conn, sender: str) -> bool:
    """True when the email comes from a brand or site you've entered a comp with."""
    if conn is None:
        return False
    addr = parseaddr(sender)[1]
    domain = addr.split("@")[-1] if "@" in addr else ""
    if domain and base_domain(domain) in known_promoter_domains(conn):
        return True
    squashed = _squash(sender)
    return any(b in squashed for b in entered_brands(conn))


KNOWN_SENDER_POINTS = 2
RELAY_STALE_HOURS = 2


def alert(cfg, conn, h: dict) -> bool:
    """Posts a possible win. Returns False when it isn't worth posting.

    h["score"] is the email's win score without the points for brands you entered with; emails found by
    an older Gmail watcher have no score and were already judged to be possible wins.
    """
    known = is_known_sender(conn, h["sender"])
    points = h.get("score", 3) + (KNOWN_SENDER_POINTS if known else 0)
    if points < 3:
        return False
    mode = cfg.unknown_senders
    if not known and mode == "ignore":
        return False
    loud = known or mode == "alert"
    fields = [{"name": "From", "value": h["sender"][:256] or "?"}]
    if h.get("spam"):
        fields.append({"name": "Folder", "value": "Spam"})
    discord.post(
        cfg.discord_webhook,
        "🏆 **Possible competition win!** It's from a brand you entered with. Check your email; claim deadlines can be short. "
        "If it's real, open **Comp stats** on your PC and press `w` to log it."
        if known else
        "🏆 **Possible competition win!** Check your email; claim deadlines can be short."
        if loud else
        ("❔ Win-like email in your **spam folder** from a sender you haven't entered a comp with. "
         "Real winner emails sometimes land there, so have a look if it seems real."
         if h.get("spam") else
         "❔ Win-like email from a sender you haven't entered a comp with. Probably spam, but have a look if it seems real."),
        [{
            "title": h["subject"][:256] or "(no subject)",
            "url": h.get("link") or None,
            "description": h["snippet"],
            "color": discord.GOLD if loud else discord.GREY,
            "fields": fields,
        }],
        silent=not loud,
    )
    return True


def queue(conn, hits: list[dict]) -> None:
    """Saves possible wins before anything is posted, so a failed Discord post can be retried next check."""
    for h in hits:
        key = h.get("id") or hashlib.sha1(
            "|".join(str(h.get(k, "")) for k in ("subject", "sender", "link")).encode()).hexdigest()
        conn.execute("INSERT OR IGNORE INTO alerts (id, data, received_at) VALUES (?, ?, ?)",
                     (key, json.dumps(h), db.now()))
    conn.commit()


def flush(cfg, conn) -> list[dict]:
    """Posts saved possible wins that haven't been handled yet. A Discord error stops here and is retried later."""
    posted = []
    for row in conn.execute("SELECT id, data FROM alerts WHERE posted_at IS NULL ORDER BY received_at").fetchall():
        h = json.loads(row["data"])
        if alert(cfg, conn, h):
            posted.append(h)
        conn.execute("UPDATE alerts SET posted_at = ? WHERE id = ?", (db.now(), row["id"]))
        conn.commit()
    return posted


def _warn_if_stale(cfg, conn, checked_at_ms) -> None:
    """Tells you once if the Google script has stopped checking Gmail (e.g. Google turned off its timer)."""
    if not checked_at_ms:
        return
    hours = (time.time() - checked_at_ms / 1000) / 3600
    warned = db.get_state(conn, "relay_stale_warned")
    if hours >= RELAY_STALE_HOURS and not warned:
        discord.post(
            cfg.discord_webhook,
            f"⚠️ The Google script hasn't checked your Gmail for {hours:.0f} hours, so win emails aren't being spotted. "
            "Open the script at script.google.com, run **setup** again and approve anything Google asks for.",
        )
        db.set_state(conn, "relay_stale_warned", db.now())
    elif hours < RELAY_STALE_HOURS and warned:
        conn.execute("DELETE FROM state WHERE key = 'relay_stale_warned'")
        conn.commit()
        discord.post(cfg.discord_webhook, "✅ The Google script is checking your Gmail again.", silent=True)


def check_relay(cfg, conn) -> list[dict]:
    """Collects possible wins found by the Google Apps Script watcher (phone/Code.gs) and posts them."""
    r = requests.get(cfg.gmail_relay_url, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Gmail watcher said: {data['error']}. Check relay_url in config.toml.")
    hits = []
    for w in data.get("wins", []):
        h = {"id": w.get("id"), "subject": w.get("subject", ""), "sender": w.get("from", ""),
             "snippet": w.get("snippet", ""), "link": w.get("link", ""), "spam": bool(w.get("spam"))}
        if "score" in w:
            h["score"] = int(w["score"])
        hits.append(h)
    queue(conn, hits)
    ids = [h["id"] for h in hits if h["id"]]
    if ids:  # saved here now, so the script can forget them
        requests.get(cfg.gmail_relay_url, params={"ack": ",".join(ids)}, timeout=60).raise_for_status()
    _warn_if_stale(cfg, conn, data.get("checkedAt"))
    return flush(cfg, conn)


def check(cfg, conn) -> list[dict]:
    if cfg.gmail_relay_url:
        return check_relay(cfg, conn)
    if not (cfg.gmail_address and cfg.gmail_app_password):
        print("[inbox] gmail not configured; skipping")
        return []
    known = known_promoter_domains(conn)
    hits = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        imap.login(cfg.gmail_address, cfg.gmail_app_password)
        for folder in FOLDERS:
            hits += _check_folder(imap, folder, cfg, conn, known)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    for h in hits:
        h["link"] = (f"https://mail.google.com/mail/u/{cfg.gmail_address}/#search/rfc822msgid%3A{quote(h['message_id'])}"
                     if h["message_id"] else "")
        h["spam"] = "Spam" in h.pop("folder")
        h["id"] = h["message_id"] or None
    queue(conn, hits)
    return flush(cfg, conn)


def _check_folder(imap, folder, cfg, conn, known) -> list[dict]:
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        return []
    validity = imap.response("UIDVALIDITY")[1][0].decode()
    state_key = f"imap:{folder}"
    saved = db.get_state(conn, state_key)
    last_uid = 0
    if saved and saved.split(":")[0] == validity:
        last_uid = int(saved.split(":")[1])

    if last_uid:
        typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
    else:
        since = (date.today() - timedelta(days=cfg.inbox_lookback_days)).strftime("%d-%b-%Y")
        typ, data = imap.uid("SEARCH", None, f"SINCE {since}")
    uids = [int(u) for u in data[0].split() if int(u) > last_uid] if typ == "OK" else []

    hits = []
    for uid in uids:
        key = f"{folder}:{validity}:{uid}"
        if conn.execute("SELECT 1 FROM emails WHERE msg_key = ?", (key,)).fetchone():
            continue
        typ, msg_data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = _decode(msg.get("Subject"))
        sender = _decode(msg.get("From"))
        addr = parseaddr(sender)[1]
        domain = addr.split("@")[-1] if "@" in addr else ""
        body = _body_text(msg)
        flagged = looks_like_win(subject, body, domain, known, cfg.win_keywords)
        # Only the message key is stored, never subjects or senders.
        conn.execute(
            "INSERT INTO emails (msg_key, checked_at, flagged) VALUES (?, ?, ?)",
            (key, db.now(), int(flagged)),
        )
        if flagged:
            hits.append({
                "subject": subject, "sender": sender, "folder": folder,
                "message_id": (msg.get("Message-ID") or "").strip("<> "),
                "snippet": body[:300],
                "score": 3,  # already judged a possible win, including the points for brands you entered with
            })
    if uids:
        db.set_state(conn, state_key, f"{validity}:{max(uids)}")
    elif not last_uid:
        # Nothing recent on the first run: start from the current end of the mailbox.
        uidnext = imap.response("UIDNEXT")[1]
        start = int(uidnext[0]) - 1 if uidnext and uidnext[0] else 0
        db.set_state(conn, state_key, f"{validity}:{start}")
    conn.commit()
    return hits
