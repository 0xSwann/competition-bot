/**
 * Competition bot Gmail watcher. Runs free on Google Apps Script in the Gmail account you use for comps.
 *
 * Every 10 minutes it looks for emails that might be competition wins and keeps them for the PC version,
 * which collects them through this script's web app link and posts them to Discord. (Discord blocks
 * messages sent from Google's servers, so the PC does the posting.)
 *
 * Setup: run setup() once, deploy as a web app (Execute as: Me, Who has access: Anyone), then run showLink().
 * After pasting in a new version of this file: Deploy > Manage deployments > edit (pencil) > Version: New version.
 */

const WIN_KEYWORDS = ['congratulations', 'congrats', "you've won", 'you have won', 'you won', 'winner',
  'lucky winner', 'prize', 'claim your', "you're a winner", 'selected as', 'drawn as'];
const STRONG_PHRASES = ["you've won", 'you have won', 'you won', "you're a winner", 'you are a winner',
  'you’ve won', 'you’re a winner', 'winner notification', 'claim your prize', 'prize claim',
  'you are the winner', "you're the winner", 'lucky winner'];
const PROMO_PHRASES = ['chance to win', 'enter to win', 'could win', 'win a ', 'win an ', 'thanks for entering',
  'thank you for entering', "you're in", 'you’re in', 'entry confirmed', 'you have been entered',
  "you've been entered", 'entry received'];

// Emails scoring this much go to the PC. The PC adds points for brands you entered with and makes the final call
// (3 or more is a possible win), so borderline emails from those brands aren't lost.
const SEND_TO_PC_SCORE = 1;
// Most possible wins kept waiting for the PC (e.g. while it's off). Each one is its own Script Property.
const MAX_WAITING = 150;

// ---------- Setup ----------

/** Run once. Creates the 10-minute timer. */
function setup() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('key')) props.setProperty('key', Utilities.getUuid());
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('checkInbox').timeBased().everyMinutes(10).create();
  checkInbox();
  Logger.log('Set up. Next: Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone), then run showLink.');
}

/** Run after deploying as a web app. Prints the line to paste into the PC version's config.toml. */
function showLink() {
  const key = PropertiesService.getScriptProperties().getProperty('key');
  if (!key) throw new Error('Run setup first.');
  // getUrl() can return the /dev test link, which only works while signed in, so ask for the /exec one instead.
  Logger.log('In Deploy > Manage deployments, copy the Web app URL (it ends in /exec). Then put this line in ' +
    'config.toml under [gmail], with that URL in place of WEB_APP_URL:\n\nrelay_url = "WEB_APP_URL?key=' + key + '"');
}

/** Run to stop everything. */
function stop() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
}

// ---------- Web app (the PC calls this) ----------

/**
 * Returns the possible wins waiting for the PC. They stay here until the PC confirms it has saved them
 * by calling again with ack=<id>,<id>, so nothing is lost if the PC or its internet drops out midway.
 */
function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const key = props.getProperty('key');
  const out = function (obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); };
  if (!e || !e.parameter || !key || e.parameter.key !== key) return out({ error: 'wrong key' });
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    migrateOldList_(props);
    if (e.parameter.ack) {
      e.parameter.ack.split(',').forEach(function (id) { if (id) props.deleteProperty('p:' + id); });
      return out({ ok: true });
    }
    return out({ wins: waiting_(props), checkedAt: Number(props.getProperty('inboxCheckedAt') || 0), version: 2 });
  } finally {
    lock.releaseLock();
  }
}

function waiting_(props) {
  const all = props.getProperties();
  return Object.keys(all).filter(function (k) { return k.indexOf('p:') === 0; })
    .map(function (k) { return JSON.parse(all[k]); })
    .sort(function (a, b) { return (a.at || 0) - (b.at || 0); });
}

/** Older versions kept waiting wins in one list called "pending". */
function migrateOldList_(props) {
  const old = props.getProperty('pending');
  if (old === null) return;
  JSON.parse(old || '[]').forEach(function (w, i) {
    w.id = w.id || ('old' + Date.now() + i);
    w.at = w.at || Date.now();
    props.setProperty('p:' + w.id, JSON.stringify(w));
  });
  props.deleteProperty('pending');
}

// ---------- Win scoring ----------

function norm_(text) {
  return (text || '').toLowerCase().replace(/[‘’]/g, "'");
}

// Whole phrases only, so "you won" doesn't match "you won't" and "prize" doesn't match "prized".
function has_(text, list) {
  return list.some(function (p) {
    const escaped = norm_(p).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp("(?<![a-z])" + escaped + "(?![a-z'])").test(text);
  });
}

/** Same scoring as compbot/inbox.py score(), minus the points for brands you entered with (only the PC knows those). */
function winScore(subject, body) {
  const s = norm_(subject), b = norm_(body);
  let points = 0;
  if (has_(s, STRONG_PHRASES)) points += 3;
  else if (has_(b, STRONG_PHRASES)) points += 2;
  if (has_(s, WIN_KEYWORDS)) points += 1;
  // Win words in the body alone don't count: marketing emails are full of "prize" and "winner".
  if (has_(s, PROMO_PHRASES) && !has_(s, STRONG_PHRASES)) points -= 2;
  return points;
}

function looksLikeWin(subject, body) {
  return winScore(subject, body) >= 3;
}

// ---------- Inbox ----------

function getList_(key) {
  const v = PropertiesService.getScriptProperties().getProperty(key);
  return v ? JSON.parse(v) : null;
}

function checkInbox() {
  const started = Date.now();
  const props = PropertiesService.getScriptProperties();
  const last = Number(props.getProperty('inboxCheckedAt') || 0);
  const done = getList_('emails') || [];
  // First run looks back 14 days; later runs look from the last check, with a few minutes' overlap.
  const since = last ? Math.floor(last / 1000) - 600 : Math.floor(started / 1000) - 14 * 86400;
  const query = 'in:anywhere -in:sent -in:chats -in:drafts after:' + since;
  const hits = [];
  let threads = [];
  for (let start = 0; start < 500; start += 100) {
    const page = GmailApp.search(query, start, 100);
    threads = threads.concat(page);
    if (page.length < 100) break;
  }
  const me = Session.getEffectiveUser().getEmail();
  outer:
  for (let t = 0; t < threads.length; t++) {
    const thread = threads[t];
    const msgs = thread.getMessages();
    for (let m = 0; m < msgs.length; m++) {
      if (Date.now() - started > 4 * 60 * 1000) break outer; // stay well inside Google's 6-minute limit
      const msg = msgs[m];
      if (msg.getDate().getTime() / 1000 < since) continue;
      const id = msg.getId();
      if (done.indexOf(id) >= 0) continue;
      done.push(id);
      const body = msg.getPlainBody().replace(/\s+/g, ' ').slice(0, 20000);
      const score = winScore(msg.getSubject(), body);
      if (score >= SEND_TO_PC_SCORE) {
        hits.push({ id: id, at: Date.now(), score: score, subject: (msg.getSubject() || '').slice(0, 150),
          from: (msg.getFrom() || '').slice(0, 100), snippet: body.slice(0, 200),
          link: 'https://mail.google.com/mail/u/' + me + '/#all/' + thread.getId(), spam: thread.isInSpam() });
      }
    }
  }
  // Script Properties values are limited to 9 KB, so only the last 300 email ids are remembered.
  props.setProperty('emails', JSON.stringify(done.slice(-300)));
  queueForPc_(hits);
  props.setProperty('inboxCheckedAt', String(started));
}

function queueForPc_(hits) {
  if (!hits.length) return;
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const props = PropertiesService.getScriptProperties();
    hits.forEach(function (h) { props.setProperty('p:' + h.id, JSON.stringify(h)); });
    // If the PC has been off for a long time, drop the weakest, oldest ones first.
    const waiting = waiting_(props);
    if (waiting.length > MAX_WAITING) {
      waiting.sort(function (a, b) { return (a.score || 3) - (b.score || 3) || (a.at || 0) - (b.at || 0); })
        .slice(0, waiting.length - MAX_WAITING)
        .forEach(function (w) { props.deleteProperty('p:' + w.id); });
    }
  } finally {
    lock.releaseLock();
  }
}
