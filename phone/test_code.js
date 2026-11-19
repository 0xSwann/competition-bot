// Run with: node phone/test_code.js
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/Code.gs', 'utf8');

const props = {}, triggers = [], fetches = [];
const msgs = [
  { id: 'm1', subject: "Congratulations, you've won!", body: 'Please claim your prize by Friday', from: 'Promo <a@b.com.au>' },
  { id: 'm2', subject: 'Enter for your chance to win $10,000!', body: 'Win a prize', from: 'spam@x.com' },
  { id: 'm3', subject: 'Thanks for entering!', body: 'Winners drawn 1 Nov. Good luck.', from: 'k@kitchenaid.com.au' },
  { id: 'm4', subject: "You're a winner - KitchenAid Stand Mixer", body: '', from: 'k@kitchenaid.com.au' },
  { id: 'm5', subject: 'Your prize from Clipsal', body: 'Details inside', from: 'hello@clipsal.com.au' },  // borderline: 1 point
  { id: 'm6', subject: 'Weekly newsletter', body: 'Big sale', from: 'shop@x.com' },
];
const ctx = {
  PropertiesService: { getScriptProperties: () => ({
    getProperty: k => props[k] ?? null, setProperty: (k, v) => { props[k] = v; }, deleteProperty: k => { delete props[k]; },
    getProperties: () => Object.assign({}, props),
  }) },
  UrlFetchApp: { fetch: (url) => { fetches.push(url); return { getResponseCode: () => 200, getContentText: () => '' }; } },
  Utilities: { getUuid: () => 'k-123' },
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
assert.strictEqual(ctx.looksLikeWin('Your 3kg WPI Loyalty Offer Ends Today', 'you won’t get this price again'), false);
assert.strictEqual(ctx.looksLikeWin('Our Biggest Black Friday Sale', "prize winner you won't believe"), false);
assert.strictEqual(ctx.looksLikeWin('Prize draw results', 'Congratulations, you have won a mixer!'), true);
assert.strictEqual(ctx.looksLikeWin("You’ve won!", ''), true);

ctx.setup();
assert.strictEqual(JSON.stringify(triggers), JSON.stringify([['checkInbox', 10]]));
assert.strictEqual(fetches.length, 0, 'never posts anywhere itself');
assert.strictEqual(JSON.parse(ctx.doGet({ parameter: { key: 'nope' } })).error, 'wrong key');

// Strong wins and borderline ones are both handed to the PC, with their score; promos and newsletters aren't.
let got = JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } }));
assert.deepStrictEqual(got.wins.map(w => w.id).sort(), ['m1', 'm4', 'm5']);
assert.strictEqual(got.version, 2);
assert.ok(got.checkedAt > 0);
assert.ok(got.wins.find(w => w.id === 'm1').score >= 3);
assert.strictEqual(got.wins.find(w => w.id === 'm5').score, 1);
assert.ok(got.wins[0].link.startsWith('https://mail.google.com/mail/u/me@example.com/#all/'));

// Nothing is removed until the PC acknowledges it.
got = JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } }));
assert.strictEqual(got.wins.length, 3);
JSON.parse(ctx.doGet({ parameter: { key: 'k-123', ack: 'm1,m4' } }));
got = JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } }));
assert.deepStrictEqual(got.wins.map(w => w.id), ['m5']);

// Checking again doesn't queue the same emails twice.
ctx.checkInbox();
assert.strictEqual(JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } })).wins.length, 1);

// Wins queued by the older version (one "pending" list) are carried over.
props.pending = JSON.stringify([{ subject: 'Old win', from: 'a@b.com', snippet: '', link: '', spam: false }]);
got = JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } }));
assert.strictEqual(got.wins.length, 2);
assert.ok(got.wins.some(w => w.subject === 'Old win' && w.id));
assert.strictEqual(props.pending, undefined);

// The waiting list is capped, dropping the weakest first.
for (let i = 0; i < 160; i++) props['p:x' + i] = JSON.stringify({ id: 'x' + i, at: i, score: 1 });
ctx.queueForPc_([{ id: 'big', at: 999, score: 3 }]);
const left = JSON.parse(ctx.doGet({ parameter: { key: 'k-123' } })).wins;
assert.strictEqual(left.length, 150);
assert.ok(left.some(w => w.id === 'big'));
console.log('all phone tests passed');
