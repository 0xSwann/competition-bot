"""Command line: python -m compbot <command>"""
import argparse
import logging
import logging.handlers
import time
import traceback

from . import config, db, discord, entry, feed, inbox, stats


def main() -> None:
    p = argparse.ArgumentParser(prog="compbot", description="OzBargain competition helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="watch the feed and inbox forever (what the scheduled task runs)")
    sub.add_parser("feed", help="check the OzBargain feed once")
    sub.add_parser("inbox", help="collect win emails from the Google script (or check Gmail) once")
    sub.add_parser("enter", help="open new comps one by one with your details pre-filled")
    sub.add_parser("bookmark", help="make the \"Fill my details\" bookmark page and open it")
    sub.add_parser("test-discord", help="send a test message to the Discord webhook")
    sub.add_parser("status", help="show counts of comps by status")
    sub.add_parser("stats", help="entries, time spent, wins and $/hour; press w to record a win")
    sub.add_parser("win", help="record a win")
    args = p.parse_args()

    cfg = config.load()
    conn = db.connect()

    if args.cmd == "feed":
        print(f"{len(feed.check(cfg, conn))} new comp(s) matched your filters.")
    elif args.cmd == "inbox":
        print(f"{len(inbox.check(cfg, conn))} possible win(s) found.")
    elif args.cmd == "enter":
        entry.run(cfg, conn)
    elif args.cmd == "bookmark":
        entry.open_url(cfg, entry.write_bookmark_page(cfg.me).as_uri())
        print("Opened the bookmark page in your browser.")
    elif args.cmd == "test-discord":
        if not cfg.discord_webhook:
            raise SystemExit("No Discord webhook_url in config.toml yet.")
        discord.post(cfg.discord_webhook, "✅ Competition bot can reach this channel.")
        print("Sent.")
    elif args.cmd == "status":
        for row in conn.execute("SELECT status, COUNT(*) n FROM comps GROUP BY status"):
            print(f"{row['status']:>9}: {row['n']}")
        print(f"possible wins flagged: {conn.execute('SELECT COUNT(*) FROM emails WHERE flagged = 1').fetchone()[0]}")
    elif args.cmd == "stats":
        stats.run(conn)
    elif args.cmd == "win":
        stats.record_win_interactive(conn)
    elif args.cmd == "run":
        run_forever(cfg, conn)


HEARTBEAT_SECONDS = 5 * 60


def run_forever(cfg, conn) -> None:
    # Keeps at most about 3 MB of log: compbot.log plus two older files.
    handler = logging.handlers.RotatingFileHandler(
        config.DATA_DIR / "compbot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    next_feed = next_inbox = 0.0
    failures = {"feed": 0, "inbox": 0}
    logging.info("started")
    last_beat = 0.0
    while True:
        now = time.time()
        if now - last_beat >= HEARTBEAT_SECONDS:  # lets Enter comps tell whether this task is running
            db.set_state(conn, "heartbeat", db.now())
            last_beat = now
        for name, due, minutes, fn in (
            ("feed", next_feed, cfg.feed_minutes, feed.check),
            ("inbox", next_inbox, cfg.inbox_minutes, inbox.check),
        ):
            if now < due:
                continue
            try:
                found = fn(cfg, conn)
                logging.info("%s check ok: %d found", name, len(found))
                db.set_state(conn, f"last_{name}_ok", db.now())
                failures[name] = 0
            except Exception:
                failures[name] += 1
                logging.error("%s check failed:\n%s", name, traceback.format_exc())
                if failures[name] == 3:  # tell you once if something keeps breaking
                    try:
                        discord.post(cfg.discord_webhook, f"⚠️ The {name} check has failed 3 times in a row. See data/compbot.log.")
                    except Exception:
                        pass
            if name == "feed":
                next_feed = now + minutes * 60
            else:
                next_inbox = now + minutes * 60
        time.sleep(30)


if __name__ == "__main__":
    main()
