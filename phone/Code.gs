/**
 * Competition bot, phone edition. Runs free on Google Apps Script in the Gmail
 * account that owns it: watches OzBargain comps, spots win emails, alerts Discord.
 *
 * Setup: paste your Discord webhook below, then run setup() once.
 */

// ===== Your settings =====
const DISCORD_WEBHOOK = 'PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE';

// true when the PC version is running: the PC posts the comps, and this script hands win emails to the PC,
// which posts them to Discord (Discord blocks messages sent from Google's servers).
const PC_MODE = false;

// Comps with any of these OzBargain tags are never sent to you.
const EXCLUDE_CATEGORIES = ['Purchase Required', 'Recruit/Referral', 'Gleam'];
// Comps whose title or description contains any of these are never sent to you.
const EXCLUDE_KEYWORDS = ['new zealand only', 'nz only', 'nz residents only', 'usa only', 'us residents only', 'uk only', 'gleam.io', 'gle.am/'];
// ==========================

const FEED_URL = 'https://www.ozbargain.com.au/competition/feed';
const USER_AGENT = 'compbot/1.0 (personal use; polls the competitions RSS feed every 15 minutes)';

const WIN_KEYWORDS = ['congratulations', 'congrats', "you've won", 'you have won', 'you won', 'winner',
  'lucky winner', 'prize', 'claim your', "you're a winner", 'selected as', 'drawn as'];
const STRONG_PHRASES = ["you've won", 'you have won', 'you won', "you're a winner", 'you are a winner',
  'you’ve won', 'you’re a winner', 'winner notification', 'claim your prize', 'prize claim',
  'you are the winner', "you're the winner", 'lucky winner'];
const PROMO_PHRASES = ['chance to win', 'enter to win', 'could win', 'win a ', 'win an ', 'thanks for entering',
  'thank you for entering', "you're in", 'you’re in', 'entry confirmed', 'you have been entered',
  "you've been entered", 'entry received'];
const SECOND_LEVEL = ['com', 'net', 'org', 'edu', 'gov', 'co', 'asn', 'id'];

// ---------- Setup ----------

/** Run once. Creates the timers. */
function setup() {
  if (!PC_MODE && DISCORD_WEBHOOK.indexOf('https://') !== 0) throw new Error('Paste your Discord webhook URL at the top first.');
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('key')) props.setProperty('key', Utilities.getUuid());
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  if (!PC_MODE) ScriptApp.newTrigger('checkFeed').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('checkInbox').timeBased().everyMinutes(10).create();
  if (PC_MODE) {
    Logger.log('Set up. Next: Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone), then run showLink.');
  } else {
    postDiscord_('\u2705 Competition bot is set up. You\u2019ll get new comps and possible wins here.');
    checkFeed();
  }
  checkInbox();
}

/** Run after deploying as a web app. Prints the line to paste into the PC version's config.toml. */
function showLink() {
  const key = PropertiesService.getScriptProperties().getProperty('key');
  if (!key) throw new Error('Run setup first.');
  // getUrl() can return the /dev test link, which only works while signed in, so ask for the /exec one instead.
  Logger.log('In Deploy > Manage deployments, copy the Web app URL (it ends in /exec). Then put this line in ' +
    'config.toml under [gmail], with that URL in place of WEB_APP_URL:\n\nrelay_url = "WEB_APP_URL?key=' + key + '"');
}

/** The PC version calls this to collect possible wins found since its last call. */
function doGet(e) {
  const key = PropertiesService.getScriptProperties().getProperty('key');
  const out = function (obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); };
  if (!e || !e.parameter || !key || e.parameter.key !== key) return out({ error: 'wrong key' });
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const wins = getList_('pending') || [];
    PropertiesService.getScriptProperties().setProperty('pending', '[]');
    return out({ wins: wins, checkedAt: Number(PropertiesService.getScriptProperties().getProperty('inboxCheckedAt') || 0) });
  } finally {
    lock.releaseLock();
  }
}

/** Run to stop everything. */
function stop() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
}

// ---------- State (Script Properties) ----------

function getList_(key) {
  const v = PropertiesService.getScriptProperties().getProperty(key);
  return v ? JSON.parse(v) : null;
}

function setList_(key, list, cap) {
  PropertiesService.getScriptProperties().setProperty(key, JSON.stringify(list.slice(-cap)));
}

// ---------- OzBargain feed ----------

function decodeXml_(s) {
  return (s || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#039;|&#39;|&apos;/g, "'").replace(/&amp;/g, '&');
}

function tag_(xml, name) {
  const m = xml.match(new RegExp('<' + name + '(?:\\s[^>]*)?>([\\s\\S]*?)</' + name + '>'));
  return m ? decodeXml_(m[1]).trim() : '';
}

function attr_(tagText, name) {
  const m = tagText.match(new RegExp('\\s' + name + '="([^"]*)"'));
  return m ? decodeXml_(m[1]) : '';
}

function parseFeed(xml) {
  const items = xml.match(/<item[\s>][\s\S]*?<\/item>/g) || [];
  return items.map(function (item) {
    const link = tag_(item, 'link');
    const idMatch = link.match(/\/(?:competition|node)\/(\d+)/) || tag_(item, 'guid').match(/(\d+)/);
    if (!idMatch) return null;
    const desc = tag_(item, 'description');
    const metaMatch = item.match(/<ozb:meta\b[^>]*>/);
    const meta = metaMatch ? metaMatch[0] : '';
    const categories = (item.match(/<category[^>]*>[\s\S]*?<\/category>/g) || [])
      .map(function (c) { return decodeXml_(c.replace(/<\/?category[^>]*>/g, '')).trim(); });
    let goUrl = attr_(meta, 'url');
    if (goUrl.indexOf('/goto/') < 0) {
      const g = desc.match(/href="([^"]*\/goto\/\d+)"/);
      goUrl = g ? g[1] : '';
    }
    if (goUrl.indexOf('/') === 0) goUrl = 'https://www.ozbargain.com.au' + goUrl;
    if (!goUrl) goUrl = 'https://www.ozbargain.com.au/goto/' + idMatch[1];
    return {
      id: idMatch[1],
      title: tag_(item, 'title'),
      nodeUrl: link,
      goUrl: goUrl,
      categories: categories,
      closing: attr_(meta, 'closing'),
      text: desc.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
    };
  }).filter(Boolean);
}

function isWanted(comp, today) {
  const excluded = EXCLUDE_CATEGORIES.map(function (c) { return c.toLowerCase(); });
  if (comp.categories.some(function (c) { return excluded.indexOf(c.toLowerCase()) >= 0; })) return false;
  const hay = (comp.title + ' ' + comp.text).toLowerCase();
  if (EXCLUDE_KEYWORDS.some(function (k) { return hay.indexOf(k.toLowerCase()) >= 0; })) return false;
  const closes = (comp.closing.match(/^\d{4}-\d{2}-\d{2}/) || [])[0];
  if (closes && closes < today) return false;
  return true;
}

function checkFeed() {
  const res = UrlFetchApp.fetch(FEED_URL, { headers: { 'User-Agent': USER_AGENT }, muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('OzBargain feed returned HTTP ' + res.getResponseCode());
  const today = Utilities.formatDate(new Date(), 'Australia/Sydney', 'yyyy-MM-dd');
  const result = processFeed(res.getContentText(), getList_('seen'), today);
  setList_('seen', result.seen, 700); // Script Properties values are limited to 9 KB
  if (result.firstRun) {
    postDiscord_('🎟️ Found **' + result.wanted.length + '** open comps that match your filters. ' +
      'New ones will show up here as they’re posted.', result.wanted.map(compEmbed_));
  } else if (result.wanted.length) {
    postDiscord_('🎟️ **' + result.wanted.length + ' new comp' + (result.wanted.length === 1 ? '' : 's') +
      '** on OzBargain. Tap a title to go to the entry page.', result.wanted.map(compEmbed_));
  }
}

/** Pure logic, separated for testing. seen is null on the first run. */
function processFeed(xml, seen, today) {
  const firstRun = seen === null;
  seen = seen || [];
  const wanted = [];
  parseFeed(xml).forEach(function (c) {
    if (seen.indexOf(c.id) >= 0) return;
    seen.push(c.id);
    if (isWanted(c, today)) wanted.push(c);
  });
  return { firstRun: firstRun, seen: seen, wanted: wanted };
}

function compEmbed_(c) {
  const fields = [];
  if (c.closing) fields.push({ name: 'Closes', value: c.closing.slice(0, 16).replace('T', ' '), inline: true });
  if (c.categories.length) fields.push({ name: 'Entry', value: c.categories.join(', ').slice(0, 1000), inline: true });
  fields.push({ name: 'OzBargain', value: '[Comments and details](' + c.nodeUrl + ')', inline: false });
  return { title: c.title.slice(0, 256), url: c.goUrl, color: 0x3498db, fields: fields };
}

// ---------- Inbox ----------

function baseDomain(host) {
  const parts = host.toLowerCase().replace(/\.$/, '').split('.');
  if (parts.length >= 3 && SECOND_LEVEL.indexOf(parts[parts.length - 2]) >= 0 && parts[parts.length - 1].length === 2) {
    return parts.slice(-3).join('.');
  }
  return parts.slice(-2).join('.');
}

function norm_(text) {
  return (text || '').toLowerCase().replace(/[\u2018\u2019]/g, "'");
}

// Whole phrases only, so "you won" doesn't match "you won't" and "prize" doesn't match "prized".
function has_(text, list) {
  return list.some(function (p) {
    const escaped = norm_(p).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp("(?<![a-z])" + escaped + "(?![a-z'])").test(text);
  });
}

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
      if (looksLikeWin(msg.getSubject(), body)) {
        hits.push({ subject: msg.getSubject(), from: msg.getFrom(), snippet: body.slice(0, 300),
          link: 'https://mail.google.com/mail/u/' + Session.getEffectiveUser().getEmail() + '/#all/' + thread.getId(), spam: thread.isInSpam() });
      }
    }
  }
  setList_('emails', done, 300); // Script Properties values are limited to 9 KB
  props.setProperty('inboxCheckedAt', String(started));
  if (PC_MODE) {
    queueForPc_(hits);
    return;
  }
  hits.forEach(function (h) {
    const fields = [{ name: 'From', value: h.from.slice(0, 256) }];
    if (h.spam) fields.push({ name: 'Folder', value: 'Spam' });
    postDiscord_('\uD83C\uDFC6 **Possible competition win!** Check your email; claim deadlines can be short.', [{
      title: (h.subject || '(no subject)').slice(0, 256), url: h.link, description: h.snippet, color: 0xf1c40f, fields: fields,
    }]);
  });
}

function queueForPc_(hits) {
  if (!hits.length) return;
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const pending = (getList_('pending') || []).concat(hits.map(function (h) {
      return { subject: h.subject.slice(0, 150), from: h.from.slice(0, 100), snippet: h.snippet.slice(0, 200), link: h.link, spam: h.spam };
    }));
    setList_('pending', pending, 12); // Script Properties values are limited to 9 KB
  } finally {
    lock.releaseLock();
  }
}

// ---------- Discord ----------

function postDiscord_(content, embeds) {
  embeds = embeds || [];
  for (let i = 0; i === 0 || i < embeds.length; i += 10) {
    const payload = { content: i === 0 ? content : '', embeds: embeds.slice(i, i + 10) };
    for (let attempt = 0; attempt < 3; attempt++) {
      const res = UrlFetchApp.fetch(DISCORD_WEBHOOK, {
        method: 'post', contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true,
      });
      if (res.getResponseCode() === 429) {
        let wait = 2;
        try { wait = JSON.parse(res.getContentText()).retry_after || 2; } catch (e) {}
        Utilities.sleep(Math.min(10, Math.ceil(wait)) * 1000);
        continue;
      }
      if (res.getResponseCode() >= 300) throw new Error('Discord returned HTTP ' + res.getResponseCode() + ': ' + res.getContentText());
      break;
    }
  }
}
