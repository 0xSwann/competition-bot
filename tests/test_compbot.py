import email.message
import re
import time
from datetime import date
from pathlib import Path

import pytest

from compbot import db, feed, inbox
from compbot.config import DEFAULT_WIN_KEYWORDS, Config

FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:ozb="https://www.ozbargain.com.au">
<channel><title>OzBargain | Competitions</title>
<item>
  <title>Win a 6-Night Trip to Whistler</title>
  <link>https://www.ozbargain.com.au/competition/979426</link>
  <description>&lt;p&gt;Australia-wide. &lt;a href="https://www.ozbargain.com.au/goto/979426"&gt;Enter&lt;/a&gt;&lt;/p&gt;</description>
  <category>Lucky Draw</category><category>Website</category><category>Email Subscription</category>
  <ozb:meta comment-count="0" click-count="387" entries="38" closing="2099-05-06T23:59:00+10:00"/>
  <guid>979426 at https://www.ozbargain.com.au</guid>
</item>
<item>
  <title>Win a Garmin Watch</title>
  <link>https://www.ozbargain.com.au/competition/979421</link>
  <description>NZ only</description>
  <category>Lucky Draw</category><category>Website</category>
  <ozb:meta closing="2099-10-29T23:59:00+11:00" url="https://www.ozbargain.com.au/goto/979421"/>
</item>
<item>
  <title>Win a TV (buy any product)</title>
  <link>https://www.ozbargain.com.au/competition/979000</link>
  <description>x</description>
  <category>Purchase Required</category>
  <ozb:meta closing="2099-01-01"/>
</item>
<item>
  <title>Old comp</title>
  <link>https://www.ozbargain.com.au/competition/900000</link>
  <description>x</description>
  <category>Website</category>
  <ozb:meta closing="2020-01-01T00:00:00+10:00"/>
</item>
</channel></rss>"""


def cfg():
    return Config(
        discord_webhook="", gmail_address="", gmail_app_password="", me={},
        exclude_categories=["purchase required"], exclude_keywords=["nz only"],
        win_keywords=DEFAULT_WIN_KEYWORDS,
    )


def test_parse_feed():
    comps = feed.parse(FEED)
    assert [c["node_id"] for c in comps] == ["979426", "979421", "979000", "900000"]
    assert comps[0]["go_url"] == "https://www.ozbargain.com.au/goto/979426"
    assert comps[1]["go_url"] == "https://www.ozbargain.com.au/goto/979421"
    assert comps[2]["go_url"] == "https://www.ozbargain.com.au/goto/979000"  # built from the node id
    assert comps[0]["categories"] == "Lucky Draw, Website, Email Subscription"
    assert comps[0]["closing"].startswith("2099-05-06")


def test_filters_and_dedup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    first = feed.check(cfg(), conn, FEED)
    assert [c["node_id"] for c in first] == ["979426"]
    statuses = dict(conn.execute("SELECT node_id, status FROM comps").fetchall())
    assert statuses == {"979426": "new", "979421": "filtered", "979000": "filtered", "900000": "filtered"}
    assert feed.check(cfg(), conn, FEED) == []


@pytest.mark.parametrize("subject,body,domain,expected", [
    ("Congratulations, you've won!", "Please claim your prize by Friday", "promo.com.au", True),
    ("You're a winner - KitchenAid Stand Mixer", "", "kitchenaid.com.au", True),
    ("Winner notification", "", "mail.gadgetguy.com.au", True),
    ("Congratulations", "Your prize is on its way", "news.kitchenaid.com.au", True),  # known promoter
    ("Enter for your chance to win $10,000!", "Win a prize", "spam.com", False),
    ("Thanks for entering!", "Winners will be drawn on 1 Nov. Good luck.", "kitchenaid.com.au", False),
    ("Your weekly newsletter", "Big sale this weekend", "shop.com", False),
    ("THE WAIT IS OVER! IT'S EL CLASICO TIME", "this is the one you won't want to miss", "beinsports.net", False),
    ("Your 3kg WPI Loyalty Offer Ends Today", "Hi Louis, you won\u2019t get this price again", "aminoz.com.au", False),
    ("Our Biggest Black Friday Sale starts at midnight", "prize winner you won't believe", "knivesandstones.com.au", False),
    ("Prize draw results", "Congratulations, you have won a KitchenAid mixer!", "promo.com.au", True),
])
def test_win_detection(subject, body, domain, expected):
    known = {"kitchenaid.com.au"}
    assert inbox.looks_like_win(subject, body, domain, known, DEFAULT_WIN_KEYWORDS) is expected


def test_base_domain():
    assert inbox.base_domain("mail.news.kitchenaid.com.au") == "kitchenaid.com.au"
    assert inbox.base_domain("em.gleam.io") == "gleam.io"


class FakeImap:
    def __init__(self, msgs, validity="7", uidnext="100"):
        self.msgs, self.validity, self.uidnext = msgs, validity, uidnext

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def response(self, code):
        return (code, [self.validity.encode() if code == "UIDVALIDITY" else self.uidnext.encode()])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            crit = args[1]
            uids = sorted(self.msgs)
            if crit.startswith("UID "):
                lo = int(crit.split()[1].split(":")[0])
                uids = [u for u in uids if u >= lo] or uids[-1:]  # IMAP returns last msg for n:* past the end
            return ("OK", [" ".join(map(str, uids)).encode()])
        if cmd == "FETCH":
            return ("OK", [(b"x", self.msgs[int(args[0])])])


def make_msg(subject, sender, body):
    m = email.message.EmailMessage()
    m["Subject"], m["From"], m["Message-ID"] = subject, sender, f"<{abs(hash(subject))}@x>"
    m.set_content(body)
    return m.as_bytes()


def test_inbox_folder_scan(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    imap = FakeImap({
        5: make_msg("Newsletter", "a@shop.com", "sale"),
        6: make_msg("Congratulations, you've won!", "Promo <win@brand.com.au>", "claim your prize"),
    })
    hits = inbox._check_folder(imap, "INBOX", cfg(), conn, set())
    assert [h["subject"] for h in hits] == ["Congratulations, you've won!"]
    assert db.get_state(conn, "imap:INBOX") == "7:6"
    # Second pass: nothing new, and the "n:* returns last message" quirk doesn't re-alert.
    assert inbox._check_folder(imap, "INBOX", cfg(), conn, set()) == []


def test_inbox_first_run_empty_starts_at_end(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    inbox._check_folder(FakeImap({}, uidnext="500"), "INBOX", cfg(), conn, set())
    assert db.get_state(conn, "imap:INBOX") == "7:499"


FORM = """<!doctype html><html><body>
<form>
  <label for="fn">First name</label><input id="fn" name="first_name">
  <label>Surname <input name="lname"></label>
  <input type="email" placeholder="Your email address" name="em">
  <input name="email_confirm" placeholder="Confirm email">
  <input type="tel" name="contact" placeholder="Mobile">
  <input name="postcode" placeholder="Postcode">
  <select name="state"><option value="">Select state</option><option value="NSW">New South Wales</option><option value="VIC">Victoria</option></select>
  <label>Date of birth <input type="date" name="dob"></label>
  <input name="username" placeholder="Username">
  <input name="hidden_thing" type="hidden">
  <label><input type="checkbox" name="terms"> I agree to the terms</label>
  <textarea name="answer" placeholder="In 25 words or less, tell us why"></textarea>
  <input name="already" value="keep me" placeholder="Email">
  <button type="submit">Enter</button>
</form>
<iframe srcdoc="<input name='EmailAddress' placeholder='Email'>"></iframe>
</body></html>"""


def test_prefill_form(tmp_path):
    pw_api = pytest.importorskip("playwright.sync_api")
    from compbot.entry import prefill_all_frames

    page_file = tmp_path / "form.html"
    page_file.write_text(FORM)
    me = {"first_name": "Lou", "last_name": "Swann", "email": "lou@example.com", "phone": "0400000000",
          "postcode": "2000", "state": "NSW", "date_of_birth": "1995-03-04"}
    with pw_api.sync_playwright() as pw:
        exe = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
        try:
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        page = browser.new_page()
        page.goto(page_file.as_uri())
        page.wait_for_timeout(300)
        filled = prefill_all_frames(page, me)
        v = lambda sel: page.eval_on_selector(sel, "e => e.value")
        assert v("#fn") == "Lou"
        assert v("[name=lname]") == "Swann"
        assert v("[name=em]") == "lou@example.com"
        assert v("[name=email_confirm]") == "lou@example.com"
        assert v("[name=contact]") == "0400000000"
        assert v("[name=postcode]") == "2000"
        assert v("[name=state]") == "NSW"
        assert v("[name=dob]") == "1995-03-04"
        assert v("[name=username]") == ""
        assert v("[name=answer]") == ""
        assert v("[name=already]") == "keep me"
        assert page.eval_on_selector("[name=terms]", "e => e.checked") is False
        assert page.frames[1].eval_on_selector("input", "e => e.value") == "lou@example.com"
        assert "first name" in filled
        browser.close()


class FakeRelay:
    """Stands in for the Google script's web app: hands out waiting wins until they're acknowledged."""

    def __init__(self, wins, checked_at=None):
        self.wins = {w.get("id") or str(i): w for i, w in enumerate(wins)}
        self.checked_at = checked_at if checked_at is not None else time.time() * 1000
        self.acks = []

    def get(self, url, params=None, timeout=None):
        if params and "ack" in params:
            self.acks.append(params["ack"])
            for i in params["ack"].split(","):
                self.wins.pop(i, None)
            body = {"ok": True}
        else:
            body = {"wins": list(self.wins.values()), "checkedAt": self.checked_at}

        class Resp:
            def raise_for_status(self): pass
            def json(self): return body
        return Resp()


def _entered(conn):
    for node, title, domain in (("1", "Win a Smeg Toaster from Clipsal Home", "www.clipsal.com"),
                                ("2", "Win a $1,000 Voucher from Salty Aura", "gleam.io")):
        conn.execute("INSERT INTO comps (node_id, title, node_url, first_seen, status, entry_domain) "
                     "VALUES (?, ?, 'u', 'now', 'entered', ?)", (node, title, domain))
    conn.commit()


def test_inbox_relay_tiers(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(inbox.discord, "post",
                        lambda url, content, embeds=None, silent=False: sent.append((content, embeds, silent)))
    relay = FakeRelay([
        {"id": "a", "score": 3, "subject": "You've won!", "from": "Clipsal Home <promo@clipsal.com.au>", "snippet": "", "link": "x", "spam": False},
        {"id": "b", "score": 3, "subject": "You've won!", "from": "Promos <hello@salty-aura.com>", "snippet": "", "link": "x", "spam": True},
        {"id": "c", "score": 3, "subject": "You've won an iPhone!", "from": "Lucky <win@scam.biz>", "snippet": "", "link": "x", "spam": False},
        {"id": "d", "score": 3, "subject": "You've won an iPad!", "from": "Lucky <win@scam.biz>", "snippet": "", "link": "x", "spam": True},
        # Borderline: only a point or two on its own, enough with a brand you entered.
        {"id": "e", "score": 1, "subject": "Your prize from Clipsal", "from": "Clipsal Home <hi@clipsal.com.au>", "snippet": "", "link": "x", "spam": False},
        {"id": "f", "score": 1, "subject": "Prize season sale", "from": "Shop <hi@shop.com>", "snippet": "", "link": "x", "spam": False},
    ])
    monkeypatch.setattr(inbox.requests, "get", relay.get)
    conn = db.connect(tmp_path / "t.db")
    _entered(conn)
    c = cfg()
    c.gmail_relay_url = "https://script.google.com/macros/s/x/exec?key=k"

    hits = inbox.check(c, conn)
    # Known brands ping (even from spam or when borderline); unknown ones are posted quietly, spam included.
    assert [h["id"] for h in hits] == ["a", "b", "c", "d", "e"]
    assert [s_[2] for s_ in sent] == [False, False, True, True, False]
    assert "brand you entered" in sent[0][0]
    assert sent[1][1][0]["fields"][1] == {"name": "Folder", "value": "Spam"}
    assert "spam folder" in sent[3][0]
    assert relay.wins == {}  # all acknowledged

    # Nothing is posted twice, even if the script hands the same email over again.
    relay.wins = {"a": {"id": "a", "score": 3, "subject": "You've won!", "from": "Clipsal Home <promo@clipsal.com.au>"}}
    assert inbox.check(c, conn) == []


def test_inbox_relay_ignore_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(inbox.discord, "post", lambda *a, **k: None)
    relay = FakeRelay([
        {"id": "a", "score": 3, "subject": "You've won!", "from": "Clipsal Home <promo@clipsal.com.au>", "spam": False},
        {"id": "c", "score": 3, "subject": "You've won an iPhone!", "from": "Lucky <win@scam.biz>", "spam": False},
    ])
    monkeypatch.setattr(inbox.requests, "get", relay.get)
    conn = db.connect(tmp_path / "t.db")
    _entered(conn)
    c = cfg()
    c.gmail_relay_url = "https://x/exec?key=k"
    c.unknown_senders = "ignore"
    assert [h["id"] for h in inbox.check(c, conn)] == ["a"]


def test_inbox_relay_keeps_wins_when_discord_fails(monkeypatch, tmp_path):
    relay = FakeRelay([{"id": "a", "score": 3, "subject": "You've won!", "from": "x <a@b.com>", "spam": False}])
    monkeypatch.setattr(inbox.requests, "get", relay.get)

    def down(*a, **k):
        raise RuntimeError("Discord is down")
    monkeypatch.setattr(inbox.discord, "post", down)
    conn = db.connect(tmp_path / "t.db")
    c = cfg()
    c.gmail_relay_url = "https://x/exec?key=k"
    with pytest.raises(RuntimeError):
        inbox.check(c, conn)
    assert relay.wins == {}  # the script was told it's safe here

    sent = []
    monkeypatch.setattr(inbox.discord, "post", lambda url, content, embeds=None, silent=False: sent.append(embeds))
    assert len(inbox.check(c, conn)) == 1  # retried on the next check
    assert sent[0][0]["title"] == "You've won!"


def test_inbox_relay_old_script_without_ids(monkeypatch, tmp_path):
    """The previous script version sends no id or score and deletes wins itself."""
    monkeypatch.setattr(inbox.discord, "post", lambda *a, **k: None)
    wins = [{"subject": "You've won!", "from": "x <a@b.com>", "snippet": "", "link": "l", "spam": False}]

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"wins": wins}
    monkeypatch.setattr(inbox.requests, "get", lambda url, params=None, timeout=None: Resp())
    conn = db.connect(tmp_path / "t.db")
    c = cfg()
    c.gmail_relay_url = "https://x/exec?key=k"
    assert len(inbox.check(c, conn)) == 1
    assert inbox.check(c, conn) == []


def test_inbox_relay_warns_when_script_stops(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(inbox.discord, "post", lambda url, content, embeds=None, silent=False: sent.append(content))
    relay = FakeRelay([], checked_at=(time.time() - 5 * 3600) * 1000)
    monkeypatch.setattr(inbox.requests, "get", relay.get)
    conn = db.connect(tmp_path / "t.db")
    c = cfg()
    c.gmail_relay_url = "https://x/exec?key=k"
    inbox.check(c, conn)
    inbox.check(c, conn)
    assert len(sent) == 1 and "hasn't checked your Gmail for 5 hours" in sent[0]
    relay.checked_at = time.time() * 1000
    inbox.check(c, conn)
    assert len(sent) == 2 and "checking your Gmail again" in sent[1]


def test_entered_brands(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO comps (node_id, title, node_url, first_seen, status) VALUES "
                 "('1', 'Win a Ninja Air Fryer from Big W and Macarthur Central', 'u', 'now', 'entered'),"
                 "('2', 'Win $2,000 Worth of Gift Cards from GPT Property Management [NSW/VIC/QLD/WA]', 'u', 'now', 'entered'),"
                 "('3', 'Win an Eero from Gadget Guy + Eero', 'u', 'now', 'new')")
    assert inbox.entered_brands(conn) == {"bigw", "macarthurcentral", "gptpropertymanagement"}
    assert inbox.is_known_sender(conn, "BIG W <noreply@email.bigw.com.au>")
    assert not inbox.is_known_sender(conn, "Gadget Guy <hi@gadgetguy.com.au>")


def test_bookmarklet_fills_form(tmp_path):
    pw_api = pytest.importorskip("playwright.sync_api")
    from urllib.parse import unquote
    from compbot import entry

    me = {"first_name": "Lou", "last_name": "Swann", "email": "lou@example.com", "postcode": "3012", "state": "VIC"}
    monkey_page = tmp_path / "fill.html"
    entry.BOOKMARK_PAGE = monkey_page
    entry.write_bookmark_page(me)
    href = re.search(r'href="(javascript:[^"]+)"', monkey_page.read_text())[1]
    js = unquote(href[len("javascript:"):])

    (tmp_path / "form.html").write_text(FORM)
    with pw_api.sync_playwright() as pw:
        exe = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
        try:
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        page = browser.new_page()
        page.goto((tmp_path / "form.html").as_uri())
        page.evaluate(js)
        v = lambda sel: page.eval_on_selector(sel, "e => e.value")
        assert v("#fn") == "Lou" and v("[name=em]") == "lou@example.com" and v("[name=state]") == "VIC"
        assert "Filled: first name" in page.inner_text("body")
        browser.close()


def test_chrome_profile_dir(tmp_path):
    from compbot.entry import chrome_profile_dir
    ls = tmp_path / "Local State"
    ls.write_text('{"profile": {"info_cache": {"Default": {"name": "Work"}, "Profile 2": {"name": "Louis"}}}}')
    assert chrome_profile_dir("Louis", ls) == "Profile 2"
    assert chrome_profile_dir("louis", ls) == "Profile 2"
    assert chrome_profile_dir("Nobody", ls) is None
    assert chrome_profile_dir("Louis", tmp_path / "missing") is None


def test_new_comp_posts_are_silent(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(feed.discord, "post", lambda url, content, embeds=None, silent=False: sent.append(silent))
    conn = db.connect(tmp_path / "t.db")
    feed.check(cfg(), conn, FEED)                                   # first run summary
    feed.check(cfg(), conn, FEED.replace("979426", "979999"))       # one new comp
    assert sent == [True, True]


def test_discord_silent_flag(monkeypatch):
    from compbot import discord
    payloads = []

    class R:
        status_code = 204
        def raise_for_status(self): pass

    monkeypatch.setattr(discord.requests, "post", lambda url, json, timeout: (payloads.append(json), R())[1])
    discord.post("https://d/x", "hi", silent=True)
    discord.post("https://d/x", "hi")
    assert payloads[0]["flags"] == 4096 and "flags" not in payloads[1]


def test_close_tab_is_windows_only():
    from compbot.entry import close_active_chrome_tab
    assert close_active_chrome_tab() is False  # tests run on Linux


@pytest.mark.parametrize("text,expected", [
    ("Open to NSW residents only", {"NSW"}),
    ("Open to residents of NSW, VIC and QLD aged 18+", {"NSW", "VIC", "QLD"}),
    ("Queensland residents only", {"QLD"}),
    ("WA/SA residents", {"WA", "SA"}),
    ("Australia-wide. Win a trip to Victoria", set()),
    ("Don't only enter once", set()),
])
def test_restricted_states(text, expected):
    assert feed.restricted_states(text) == expected


def test_other_state_comps_hidden():
    c = cfg()
    c.me = {"state": "VIC"}
    base = {"categories": "Website", "closing": "2099-01-01"}
    assert not feed.is_wanted({**base, "title": "Win a car", "text": "NSW residents only"}, c)
    assert feed.is_wanted({**base, "title": "Win a car", "text": "Open to VIC and NSW residents"}, c)
    assert feed.is_wanted({**base, "title": "Win a car", "text": "Open to all Australians"}, c)
    c.me = {}
    assert feed.is_wanted({**base, "title": "Win a car", "text": "NSW residents only"}, c)  # no state set


def test_daily_comps_come_back(tmp_path):
    from compbot import entry
    conn = db.connect(tmp_path / "t.db")
    assert feed.is_daily({"categories": "Daily Entry", "title": "Win", "text": ""})
    assert feed.is_daily({"categories": "", "title": "Win", "text": "You can enter once per day"})
    assert not feed.is_daily({"categories": "Website", "title": "Win", "text": "One entry per person"})
    for node, daily, entered in [("1", 1, "2020-01-01T00:00:00+00:00"), ("2", 1, db.now()),
                                 ("3", 0, "2020-01-01T00:00:00+00:00")]:
        conn.execute("INSERT INTO comps (node_id, title, node_url, first_seen, status, entered_at, daily, closing) "
                     "VALUES (?, 't', 'u', 'x', 'entered', ?, ?, '2099-01-01')", (node, entered, daily))
    assert [r["node_id"] for r in entry.pending(conn)] == ["1"]  # daily, entered on an earlier day


def test_daily_column_migration(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE comps (node_id TEXT PRIMARY KEY, title TEXT NOT NULL, node_url TEXT NOT NULL, "
                "go_url TEXT, categories TEXT, closing TEXT, first_seen TEXT NOT NULL, status TEXT NOT NULL "
                "DEFAULT 'new', entered_at TEXT, entry_domain TEXT)")
    old.execute("INSERT INTO comps VALUES ('5', 'Win daily prizes', 'u', NULL, 'Website', '', 'x', 'new', NULL, NULL)")
    old.commit(); old.close()
    conn = db.connect(path)
    assert conn.execute("SELECT daily FROM comps WHERE node_id = '5'").fetchone()[0] == 1


POPUP = """<!doctype html><html><body>
<p>Page content</p>
<div id="modal" style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
  <div style="background:#fff;width:420px;margin:40px auto;padding:20px">
    <div class="row"><div class="lbl">First Name *</div><div><input name="field_1"></div></div>
    <div class="row"><span>Email address</span><input name="field_2"></div>
    <div class="row"><span>Your postcode</span><input name="field_3"></div>
    <div class="row"><input name="field_4"></div>
    <comp-form></comp-form>
    <iframe id="same" style="width:300px;height:80px"
      srcdoc="<div>Mobile number</div><input name='f9'>"></iframe>
  </div>
</div>
<script>
customElements.define('comp-form', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML = '<input placeholder="Last name" name="ln">'; }
});
</script>
</body></html>"""


def _browser(pw):
    exe = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    try:
        return pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as e:
        pytest.skip(f"no browser: {e}")


def _bookmarklet(tmp_path, me):
    from urllib.parse import unquote
    from compbot import entry
    entry.BOOKMARK_PAGE = tmp_path / "fill.html"
    entry.write_bookmark_page(me)
    href = re.search(r'href="(javascript:[^"]+)"', entry.BOOKMARK_PAGE.read_text())[1]
    return unquote(href[len("javascript:"):])


def test_bookmarklet_fills_popup(tmp_path):
    pw_api = pytest.importorskip("playwright.sync_api")
    me = {"first_name": "Lou", "last_name": "Swann", "email": "lou@example.com", "phone": "0400000000",
          "postcode": "3012"}
    js = _bookmarklet(tmp_path, me)
    (tmp_path / "popup.html").write_text(POPUP)
    with pw_api.sync_playwright() as pw:
        browser = _browser(pw)
        page = browser.new_page()
        page.goto((tmp_path / "popup.html").as_uri())
        page.wait_for_timeout(300)
        page.evaluate(js)
        v = lambda sel: page.eval_on_selector(sel, "e => e.value")
        assert v("[name=field_1]") == "Lou"
        assert v("[name=field_2]") == "lou@example.com"
        assert v("[name=field_3]") == "3012"
        assert v("[name=field_4]") == ""  # no label anywhere near it
        assert page.evaluate("document.querySelector('comp-form').shadowRoot.querySelector('input').value") == "Swann"
        assert page.frames[1].eval_on_selector("input", "e => e.value") == "0400000000"
        browser.close()


def test_bookmarklet_offers_other_site_form(tmp_path):
    pw_api = pytest.importorskip("playwright.sync_api")
    js = _bookmarklet(tmp_path, {"email": "lou@example.com"})
    (tmp_path / "inner.html").write_text("<input placeholder='Email'>")
    (tmp_path / "outer.html").write_text(
        "<p>Hi</p><iframe src='http://127.0.0.1:9/form' style='width:400px;height:300px'></iframe>")
    with pw_api.sync_playwright() as pw:
        browser = _browser(pw)
        page = browser.new_page()
        page.goto((tmp_path / "outer.html").as_uri())
        page.wait_for_timeout(300)
        page.evaluate(js)
        body = page.inner_text("body")
        assert "inside a box from another site" in body
        assert page.get_attribute("a[target=_blank]", "href") == "http://127.0.0.1:9/form"
        browser.close()


def test_stats_log_and_report(tmp_path, monkeypatch):
    from compbot import entry, stats
    conn = db.connect(tmp_path / "s.db")
    comps = [{"node_id": str(i), "title": t} for i, t in enumerate(
        ["Win a $1,500 Holiday from Qantas", "Win a Toaster from Clipsal", "Win 1 of 5 $2k Prizes from Acme"])]
    for c in comps:
        conn.execute("INSERT INTO comps (node_id, title, node_url, first_seen, categories) VALUES (?, ?, 'u', 'x', ?)",
                     (c["node_id"], c["title"], "Lucky Draw, Email Subscription" if c["node_id"] != "2" else "Skill"))
    clock = iter([0, 300, 300, 400, 400, 99999])
    monkeypatch.setattr(entry.time, "monotonic", lambda: next(clock))
    entry._record(conn, comps[0], "e", started=entry.time.monotonic())  # 5 min
    entry._record(conn, comps[1], "s", started=entry.time.monotonic())  # 100 s
    entry._record(conn, comps[2], "e", started=entry.time.monotonic())  # walked away: capped at 15 min
    s = stats.summary(conn)
    assert (s["entered"], s["skipped"], s["seconds"]) == (2, 1, 300 + 100 + stats.MAX_SECONDS)
    assert conn.execute("SELECT status FROM comps WHERE node_id = '0'").fetchone()[0] == "entered"

    [row] = stats.find_entered(conn, "qantas")
    stats.set_win(conn, row["id"], stats.prize_hint(row["title"]))
    s = stats.summary(conn)
    assert (s["wins"], s["prize_total"]) == (1, 1500)
    text = stats.report(conn)
    assert "Comps entered:  2   (and 1 skipped)" in text and "Wins:           1   worth $1,500" in text
    assert "$4,154 an hour" in text and "above" in text
    assert "Prizes entered: $3,500 in total" in text
    assert "Lucky Draw" in text and "1 entered, about 5m 0s each, 1 skipped, 1 win ($1,500)" in text
    assert "Skill" in text and "about 15m 0s each" in text
    stats.add_outside_win(conn, "Movie tickets", 40)
    assert stats.summary(conn)["entered"] == 2 and stats.summary(conn)["wins"] == 2


def test_prize_hint():
    from compbot.stats import prize_hint
    assert prize_hint("Win a $1,500 Holiday") == 1500
    assert prize_hint("Win 1 of 5 $2k Prizes") == 2000
    assert prize_hint("Win a Toaster") is None


def test_stats_backfills_earlier_entries(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.split("CREATE TABLE IF NOT EXISTS entries")[0])
    old.execute("INSERT INTO comps (node_id, title, node_url, first_seen, status, entered_at) "
                "VALUES ('1', 'Win a Car', 'u', 'x', 'entered', '2026-11-01T00:00:00+00:00')")
    old.commit()
    old.close()
    from compbot import stats
    conn = db.connect(path)
    s = stats.summary(conn)
    assert (s["entered"], s["untimed"], s["seconds"]) == (1, 1, 0)
    db.connect(path)  # connecting again must not backfill twice
    assert stats.summary(db.connect(path))["entered"] == 1


def test_gleam_comps_hidden(tmp_path):
    from compbot import entry
    c = cfg()
    base = {"title": "Win a TV", "text": "Enter on the brand's site", "categories": "Website", "closing": "2099-01-01",
            "go_url": "https://www.ozbargain.com.au/goto/1", "node_url": "https://www.ozbargain.com.au/competition/1"}
    assert feed.is_wanted(base, c)
    assert not feed.is_wanted({**base, "categories": "Website, Gleam"}, c)
    assert not feed.is_wanted({**base, "text": "Enter at https://gleam.io/abc12/win-a-tv"}, c)
    assert not feed.is_wanted({**base, "text": "Enter here: gle.am/xyz"}, c)
    assert feed.is_wanted({**base, "title": "Win a gleaming new car", "text": "See gleamingcars.com.au"}, c)
    c.exclude_gleam = False
    assert feed.is_wanted({**base, "categories": "Gleam"}, c)

    # Gleam comps already waiting from before the filter are left out of Enter comps.
    conn = db.connect(tmp_path / "t.db")
    for node, cats in [("1", "Website"), ("2", "Gleam, Social Media")]:
        conn.execute("INSERT INTO comps (node_id, title, node_url, first_seen, status, categories, closing) "
                     "VALUES (?, 't', 'u', 'x', 'new', ?, '2099-01-01')", (node, cats))
    assert [r["node_id"] for r in entry.pending(conn)] == ["1"]
    assert sorted(r["node_id"] for r in entry.pending(conn, skip_gleam=False)) == ["1", "2"]


DOB_FORM = """<!doctype html><html><body><form>
  <div class="a"><label>Date of birth</label>
    <select name="dob_day"><option value="">Day</option>""" + "".join(f"<option>{i}</option>" for i in range(1, 32)) + """</select>
    <select name="dob_month"><option value="">Month</option>""" + "".join(
        f"<option value='{i}'>{n}</option>" for i, n in enumerate(
            ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"], 1)) + """</select>
    <select name="dob_year"><option value="">Year</option>""" + "".join(f"<option>{y}</option>" for y in range(2008, 1930, -1)) + """</select>
  </div>
  <div class="b"><span>Birthday</span>
    <input name="bd" placeholder="DD" maxlength="2"><input name="bm" placeholder="MM" maxlength="2"><input name="by" placeholder="YYYY" maxlength="4">
  </div>
  <div class="c"><label>Your date of birth <input name="dob_text" placeholder="dd-mm-yyyy"></label></div>
  <div class="d"><label>Date of birth <input name="dob_plain"></label></div>
</form></body></html>"""


def test_prefill_split_date_of_birth(tmp_path):
    pw_api = pytest.importorskip("playwright.sync_api")
    from compbot.entry import prefill_all_frames

    (tmp_path / "dob.html").write_text(DOB_FORM)
    with pw_api.sync_playwright() as pw:
        exe = "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
        try:
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        page = browser.new_page()
        page.goto((tmp_path / "dob.html").as_uri())
        prefill_all_frames(page, {"date_of_birth": "1995-03-04"})
        v = lambda sel: page.eval_on_selector(sel, "e => e.value")
        assert (v("[name=dob_day]"), v("[name=dob_month]"), v("[name=dob_year]")) == ("4", "3", "1995")
        assert (v("[name=bd]"), v("[name=bm]"), v("[name=by]")) == ("04", "03", "1995")
        assert v("[name=dob_text]") == "04-03-1995"
        assert v("[name=dob_plain]") == "04/03/1995"
        browser.close()


def test_queue_order_closing_soon_then_value(tmp_path):
    from datetime import timedelta
    from compbot import entry
    conn = db.connect(tmp_path / "q.db")
    soon = (date.today() + timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=20)).isoformat()
    for node, title, closing in (("1", "Win a Toaster", later), ("2", "Win a $5,000 Holiday", later),
                                 ("3", "Win a $50 Voucher", soon), ("4", "Win $200 Cash", later)):
        conn.execute("INSERT INTO comps (node_id, title, node_url, go_url, first_seen, closing) VALUES (?, ?, 'u', 'g', 'x', ?)",
                     (node, title, closing))
    conn.commit()
    assert [r["node_id"] for r in entry.pending(conn)] == ["3", "2", "4", "1"]
    assert "Prize: about $5,000" in entry.describe(entry.pending(conn)[1])


def test_background_status(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from compbot import entry
    monkeypatch.setattr(entry.os, "name", "posix")
    conn = db.connect(tmp_path / "b.db")
    assert "wasn't running" in entry.background_status(conn)
    now = datetime.now(timezone.utc)
    db.set_state(conn, "heartbeat", now.isoformat())
    db.set_state(conn, "last_feed_ok", (now - timedelta(minutes=12)).isoformat())
    assert entry.background_status(conn) == "OzBargain last checked 12 min ago."
    db.set_state(conn, "heartbeat", (now - timedelta(hours=1)).isoformat())
    assert "wasn't running" in entry.background_status(conn)
