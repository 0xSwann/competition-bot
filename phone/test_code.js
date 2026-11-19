const fs = require('fs'), vm = require('vm'), assert = require('assert');
let src = fs.readFileSync(__dirname + '/Code.gs', 'utf8')
  .replace("PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE", "https://discord.test/hook");

const FEED = fs.readFileSync(__dirname + '/test_feed.xml', 'utf8');
const props = {}, posts = [], triggers = [];
const msgs = [
  { id: 'm1', subject: "Congratulations, you've won!", body: 'Please claim your prize by Friday', from: 'Promo <a@b.com.au>' },
  { id: 'm2', subject: 'Enter for your chance to win $10,000!', body: 'Win a prize', from: 'spam@x.com' },
  { id: 'm3', subject: 'Thanks for entering!', body: 'Winners drawn 1 Nov. Good luck.', from: 'k@kitchenaid.com.au' },
  { id: 'm4', subject: "You're a winner - KitchenAid Stand Mixer", body: '', from: 'k@kitchenaid.com.au' },
];
const ctx = {
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] ?? null, setProperty: (k, v) => { props[k] = v; } }) },
  UrlFetchApp: { fetch: (url, opts) => {
    if (url.includes('ozbargain')) return { getResponseCode: () => 200, getContentText: () => FEED };
    posts.push(JSON.parse(opts.payload)); return { getResponseCode: () => 204, getContentText: () => '' };
  } },
  Utilities: { formatDate: () => '2026-10-27', sleep: () => {}, getUuid: () => 'k-123' },
  Session: { getEffectiveUser: () => ({ getEmail: () => 'me@example.com' }) },
  LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
  ContentService: { MimeType: { JSON: 'json' }, createTextOutput: t => ({ setMimeType: () => t }) },
  Logger: { log: () => {} },
  ScriptApp: { getProjectTriggers: () => [], deleteTrigger: () => {},
    newTrigger: n => ({ timeBased: () => ({ everyMinutes: m => ({ create: () => triggers.push([n, m]) }) }) }) },
  GmailApp: { search: (q, start) => start > 0 ? [] : [{
    getId: () => 't1', isInSpam: () => false,
    getMessages: () => msgs.map(m => ({ getId: () => m.id, getDate: () => new Date(), getSubject: () => m.subject, getPlainBody: () => m.body, getFrom: () => m.from })),
  }] },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);

assert.strictEqual(ctx.looksLikeWin("THE WAIT IS OVER! IT'S EL CLASICO TIME", "the one you won't want to miss"), false);
assert.strictEqual(ctx.looksLikeWin('Your 3kg WPI Loyalty Offer Ends Today', 'you won\u2019t get this price again'), false);
assert.strictEqual(ctx.looksLikeWin('Our Biggest Black Friday Sale', "prize winner you won't believe"), false);
assert.strictEqual(ctx.looksLikeWin('Prize draw results', 'Congratulations, you have won a mixer!'), true);
assert.strictEqual(ctx.looksLikeWin("You\u2019ve won!", ''), true);

const comps = ctx.parseFeed(FEED);
assert.strictEqual(JSON.stringify(comps.map(c => c.id)), JSON.stringify(['979426', '979421', '979000', '900000']));
assert.strictEqual(comps[0].goUrl, 'https://www.ozbargain.com.au/goto/979426');
assert.strictEqual(comps[1].goUrl, 'https://www.ozbargain.com.au/goto/979421');
assert.strictEqual(JSON.stringify(comps[0].categories), JSON.stringify(['Lucky Draw', 'Website', 'Email Subscription']));
assert.strictEqual(comps[0].title, 'Win a 6-Night Trip to Whistler & More');

ctx.setup();
assert.strictEqual(JSON.stringify(triggers), JSON.stringify([['checkFeed', 15], ['checkInbox', 10]]));
// setup test msg, first-run feed summary, 2 win alerts
assert.strictEqual(posts.length, 4, JSON.stringify(posts, null, 1));
assert.ok(posts[1].content.includes('**1**'));
assert.strictEqual(posts[1].embeds[0].url, 'https://www.ozbargain.com.au/goto/979426');
assert.ok(posts[2].embeds[0].title.startsWith('Congratulations'));
assert.ok(posts[3].embeds[0].title.startsWith("You're a winner"));

posts.length = 0;
ctx.checkFeed(); ctx.checkInbox();
assert.strictEqual(posts.length, 0, 'no repeats');

// new comp appears
const FEED2 = FEED.replace('979426', '979999').replace('979426', '979999');
ctx.UrlFetchApp.fetch = (url, opts) => url.includes('ozbargain')
  ? { getResponseCode: () => 200, getContentText: () => FEED2 }
  : (posts.push(JSON.parse(opts.payload)), { getResponseCode: () => 204, getContentText: () => '' });
ctx.checkFeed();
assert.strictEqual(posts.length, 1);
assert.ok(posts[0].content.includes('1 new comp**'));
assert.strictEqual(posts[0].embeds.length, 1);

// PC mode: wins are queued for the PC, nothing is posted to Discord, doGet hands them over once.
for (const k of Object.keys(props)) delete props[k];
posts.length = 0; triggers.length = 0;
const pcSrc = src.replace('const PC_MODE = false;', 'const PC_MODE = true;');
const ctx2 = Object.assign({}, ctx);
vm.createContext(ctx2); vm.runInContext(pcSrc, ctx2);
ctx2.setup();
assert.strictEqual(JSON.stringify(triggers), JSON.stringify([['checkInbox', 10]]));
assert.strictEqual(posts.length, 0);
assert.strictEqual(JSON.parse(ctx2.doGet({ parameter: { key: 'nope' } })).error, 'wrong key');
const got = JSON.parse(ctx2.doGet({ parameter: { key: 'k-123' } }));
assert.strictEqual(got.wins.length, 2);
assert.ok(got.wins[0].link.startsWith('https://mail.google.com/mail/u/me@example.com/#all/'));
assert.strictEqual(JSON.parse(ctx2.doGet({ parameter: { key: 'k-123' } })).wins.length, 0);
console.log('all phone tests passed');
