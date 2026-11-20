# Competition bot

Watches OzBargain for new competitions, helps you enter them quickly, and pings Discord when an email looks like you've won. Free, runs on your Windows 11 PC plus a small Google script in your comps Gmail.

It never submits an entry for you, because most comp terms disqualify automated entries.

**How it works**

- A background task on your PC checks the [OzBargain competitions feed](https://www.ozbargain.com.au/competition/feed) every 20 minutes and posts new comps that match your filters to Discord, without a notification.
- Double-click **Enter comps** on your desktop. Comps closing in the next two days come first, then the rest from the biggest prize down. Each one opens in Chrome with a small Competition bot bar in the bottom-left corner:
  - **Fill my details** (or Alt+Shift+F) fills your name, email, phone, address, state, postcode and date of birth, including forms embedded from another site.
  - Check the form, do any CAPTCHA or written answer, tick the boxes you agree to, and press the site's submit button yourself.
  - Then press **Entered**, **Skip** (never show it again) or **Later** (Alt+Shift+E / S / L). The tab moves on to the next comp.
  - You can also type `e`, `s`, `l`, `o` (open it again) or `q` (quit) in the Enter comps window.
- Enter comps logs every comp you go through and how long it was on screen (capped at 15 minutes, in case you walk away). Double-click **Comp stats** to see comps entered, time spent, wins, prize value, $/hour, the prize value you entered for each hour, and a breakdown by entry type. Press `w` there to record a win.
- The Google script checks your Gmail (inbox and spam) every 10 minutes without marking anything read, and keeps anything that might be a win until your PC collects it. The PC posts it to Discord. Emails from brands or websites you entered with ping you; other win-like emails post as grey ❔ messages without a ping.
- If the Google script stops checking Gmail for 2 hours, or a check on the PC fails 3 times in a row, you get a warning in Discord.

## Setup on a Windows PC

1. **Get the folder onto your PC**, somewhere permanent.
2. **Install Python** (free) if you don't have it: open Terminal and run `winget install Python.Python.3.12`.
3. **Make a Discord webhook.** In your Discord server: channel settings, *Integrations*, *Webhooks*, *New Webhook*, *Copy Webhook URL*.
4. **Set up the Google script** in the Gmail account you use for comps:
   1. Go to [script.google.com](https://script.google.com), *New project*, and paste in everything from `phone/Code.gs`. Save.
   2. Choose `setup` in the function menu and press *Run*. Approve the permissions Google asks for.
   3. *Deploy*, *New deployment*, type *Web app*, Execute as *Me*, Who has access *Anyone*, *Deploy*.
   4. Run `showLink` and follow what it prints. You'll end up with a `relay_url = "..."` line.
5. **Run `setup.bat`.** The first run installs everything and opens `config.toml` in Notepad. Paste in the webhook URL and the `relay_url` line, fill in your details, and save.
6. **Run `setup.bat` again.** It sends a test message to Discord, starts the background task (it also starts itself each time you log in), and puts **Enter comps** and **Comp stats** on your desktop.
7. **Add the Chrome extension** (below).

The first feed check imports all comps that are open now as one Discord message, so you have a backlog to work through.

## Chrome extension

The extension puts the Competition bot bar on each comp and fills forms. Add it once, in the Chrome profile you enter comps with:

1. In Chrome, go to `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Press **Load unpacked** and choose the `extension` folder inside this folder.
4. Optional: click the puzzle piece in Chrome's toolbar and pin **Competition bot**. Clicking it fills the form on any page.

To change the shortcuts, go to `chrome://extensions/shortcuts`. After an update changes the extension, press the reload arrow on Competition bot in `chrome://extensions` (update.bat tells you when).

The extension only talks to Enter comps on your own PC (127.0.0.1). It remembers your details from the last time Enter comps was open, so the fill button works on other pages too.

## Updating

Double-click `update.bat`. It downloads the latest version from GitHub, keeps your `config.toml` and `data` folder, and restarts the background task. It also tells you if the Chrome extension or the Google script changed and what to do.

When the Google script changes: paste the new `phone/Code.gs` into script.google.com, save, then *Deploy*, *Manage deployments*, the pencil, Version *New version*, *Deploy*. The `relay_url` stays the same.

## Settings (`config.toml`)

- `exclude_categories`: OzBargain tags to hide. The default hides *Purchase Required* and *Recruit/Referral*. Add `"Like/Follow/Comment/Share"` to hide those too.
- `exclude_gleam`: hides comps run on Gleam (tagged *Gleam* on OzBargain, or linking to gleam.io / gle.am). On by default; set it to `false` to see them again.
- `repeat_days` (default 30): once you mark a comp entered, it won't be shown again for this many days, even if OzBargain posts it again under a new title or link (titles that are nearly the same with the same prize amounts count as the same comp). If a comp opens on a website you entered a comp on in that time, the extension's bar tells you which one, so you can press Skip if it's the same. `0` turns this off.
- Daily-entry comps (the comp says you can enter daily or once per day) come back each day you haven't entered them yet. If one comes back and the site says you've already entered, press Skip: it stays counted as entered and won't come back again.
- `exclude_instagram`: hides comps entered on Instagram (tagged *Instagram* on OzBargain, linking to instagram.com, or mentioning Instagram in the title or description). On by default; set it to `false` to see them again.
- `exclude_keywords`: hides comps whose title or description contains these words (default: NZ-, US- and UK-only).
- `state` under `[me]`: comps limited to other states are hidden.
- `unknown_senders` under `[inbox]`: what happens with win-like emails from senders you haven't entered with. `quiet` (default) posts them without a ping, `ignore` drops them, `alert` pings like a real win.
- `check_every_minutes`: how often to check the feed. Please keep it at 15 minutes or more to be polite to OzBargain.

`config.toml` holds your personal details. Keep it on your PC only and don't share it.

## Handy commands

Open Terminal in the folder and run any of these:

```
.venv\Scripts\python -m compbot status        # counts of new/entered/skipped comps
.venv\Scripts\python -m compbot stats         # entries, time spent, wins, $/hour (same as Comp stats)
.venv\Scripts\python -m compbot win           # record a win
.venv\Scripts\python -m compbot feed          # check OzBargain now
.venv\Scripts\python -m compbot inbox         # collect win emails from the Google script now
.venv\Scripts\python -m compbot bookmark      # make a "Fill my details" bookmark, if you'd rather not use the extension
.venv\Scripts\python -m compbot test-discord  # send a test message
```

The background task logs to `data\compbot.log` (it keeps about 3 MB). Run `uninstall.bat` to remove the background task and the desktop shortcuts.

## Notes

- Your PC needs to be on and logged in for comps to be checked and for win emails to reach Discord. The Google script keeps possible wins for up to 150 emails while the PC is off. Enter comps tells you when OzBargain was last checked, and restarts the background task if it isn't running.
- Filling works on most ordinary forms but not every site. If a field stays blank, type it in. If a site says something about your browser looks off, try Chrome's own autofill (Settings, Autofill, Addresses) on that site instead.
- Win detection errs on the side of alerting. An occasional false alarm is expected, and you shouldn't treat it as a sure thing until you've read the email.

## Tests

```
pip install pytest playwright
python -m pytest tests
node phone/test_code.js
```
