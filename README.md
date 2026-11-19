# Competition bot

Watches OzBargain for new competitions, helps you enter them quickly, and pings Discord when an email looks like you've won. Free, runs on your Windows 11 PC.

**How it works**

- A background task checks the [OzBargain competitions feed](https://www.ozbargain.com.au/competition/feed) every 20 minutes and posts new comps that match your filters to Discord.
- Double-click **Enter comps** on your desktop. Each new comp opens in Edge with your name, email, phone, postcode, state and date of birth filled in. You check it, do any CAPTCHA or written answer, tick the boxes you agree to, and press the site's submit button. Then press `e` (entered), `s` (skip for good), `l` (later), `r` (re-fill if the form appeared after a click) or `o` (open in your normal browser). Gleam comps open in your normal browser automatically, because Gleam rejects entries from a browser a program is controlling.
- Enter comps logs every comp you go through and how long it was on screen (capped at 15 minutes, in case you walk away). Double-click **Comp stats** on your desktop to see comps entered, time spent, wins, prize value and what that works out to per hour. Press `w` there to record a win and its prize value.
- The same background task checks your Gmail inbox and spam every 10 minutes, without marking anything read. When an email looks like a win, it posts to Discord with a link to the email. Emails from brands you've entered with count extra.

It never submits an entry for you, because most comp terms disqualify automated entries.

## Running on GitHub Actions (no PC needed)

`.github/workflows/compbot.yml` runs the feed and inbox checks every 15 minutes on GitHub's servers. Keep the repo public so the runs are free, and add three repository secrets under *Settings → Secrets and variables → Actions*:

- `DISCORD_WEBHOOK`: the Discord webhook URL
- `GMAIL_ADDRESS`: the Gmail address to watch
- `GMAIL_APP_PASSWORD`: a Gmail app password for that address

Run logs only show counts. Email subjects and senders go to Discord only and are never stored. A monthly job commits a date to `.keepalive` so GitHub doesn't pause the schedule. The entry helper below still needs a PC.

## Setup on a Windows PC (about 10 minutes)

1. **Get the folder onto your PC.** Download this `competition-bot` folder and put it somewhere permanent, e.g. `C:\Users\<you>\competition-bot`.
2. **Install Python** (free) if you don't have it: open Terminal and run `winget install Python.Python.3.12`.
3. **Make a Discord webhook.** In your Discord server: channel settings, *Integrations*, *Webhooks*, *New Webhook*, *Copy Webhook URL*.
4. **Make a Gmail app password** for the Gmail account you use for comps. Sign in at [myaccount.google.com](https://myaccount.google.com), turn on *2-Step Verification* under *Security* if it isn't already, then open [App passwords](https://myaccount.google.com/apppasswords), create one named `compbot` and copy the 16 characters.
5. **Run `setup.bat`.** The first run installs everything and opens `config.toml` in Notepad. Paste in the webhook URL and app password, fill in your details, and save.
6. **Run `setup.bat` again.** It sends a test message to Discord, starts the background task (it also starts itself each time you log in), and puts **Enter comps** on your desktop.

The first feed check imports all comps that are open now as one Discord message, so you have a backlog to work through. After that, you get one message per batch of new comps.

## Settings (`config.toml`)

- `exclude_categories`: OzBargain tags to hide. The default hides *Purchase Required* and *Recruit/Referral*. Add `"Like/Follow/Comment/Share"` to hide those too.
- `exclude_gleam`: hides comps run on Gleam (tagged *Gleam* on OzBargain, or linking to gleam.io / gle.am). On by default; set it to `false` to see them again.
- `exclude_keywords`: hides comps whose title or description contains these words (default: NZ-, US- and UK-only).
- `check_every_minutes`: how often to check the feed and inbox. Please keep the feed at 15 minutes or more to be polite to OzBargain.

`config.toml` holds your password and personal details. Keep it on your PC only and don't share it.

## Handy commands

Open Terminal in the folder and run any of these:

```
.venv\Scripts\python -m compbot status        # counts of new/entered/skipped comps
.venv\Scripts\python -m compbot stats         # entries, time spent, wins, $/hour (same as Comp stats)
.venv\Scripts\python -m compbot win           # record a win
.venv\Scripts\python -m compbot feed          # check OzBargain now
.venv\Scripts\python -m compbot inbox         # check email now
.venv\Scripts\python -m compbot test-discord  # send a test message
```

The background task logs to `data\compbot.log`, and it posts to Discord if a check fails three times in a row. Run `uninstall.bat` to remove the background task and the desktop shortcuts.

## Notes

- Your PC needs to be on and logged in for checks to run. Missed checks catch up on the next run, since comps stay open for days or weeks.
- Pre-fill works on most ordinary forms but not every site. If a field stays blank, type it in.
- The browser keeps its own profile in `data\browser-profile`, so logins (e.g. Gleam) stay signed in between sessions.
- Win detection errs on the side of alerting. An occasional false alarm is expected, and you shouldn't treat it as a sure thing until you've read the email.
