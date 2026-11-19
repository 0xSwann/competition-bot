// Competition bot extension: talks to Enter comps on this PC (127.0.0.1 only), fills forms, moves to the next comp.
importScripts('prefill.js');

const BASE = 'http://127.0.0.1:47615'; // same as compbot/helper.py PORT
// Hosts that show up inside most pages (ads, analytics, CAPTCHAs, social embeds), so they don't say who runs the comp.
const SKIP_HOSTS = /(^|\.)(ozbargain\.com\.au|google\.com|google\.com\.au|gstatic\.com|googleapis\.com|googletagmanager\.com|doubleclick\.net|googlesyndication\.com|recaptcha\.net|hcaptcha\.com|cloudflare\.com|challenges\.cloudflare\.com|facebook\.com|facebook\.net|youtube\.com|youtube-nocookie\.com|twitter\.com|x\.com|instagram\.com|tiktok\.com|hotjar\.com|127\.0\.0\.1|localhost)$/;

async function api(path, body) {
  const res = await fetch(BASE + path, {
    method: body ? 'POST' : 'GET',
    headers: { 'X-Compbot': '1', 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (data.me) await chrome.storage.local.set({ me: data.me }); // so Fill works even when Enter comps is closed
  return data;
}

async function session() {
  return chrome.storage.session.get(['tabId', 'shownId']);
}

// Enter comps opens 127.0.0.1:47615/go, a page that forwards to the comp. go.js claims that tab as the comp tab.
async function claim(tabId) {
  const cur = await api('/api/current');
  await chrome.storage.session.set({ tabId, shownId: cur.done ? null : cur.id });
  return cur;
}

chrome.tabs.onRemoved.addListener(async (id) => {
  if (id === (await session()).tabId) await chrome.storage.session.remove(['tabId', 'shownId']);
});

async function hostsOf(tabId) {
  const hosts = [];
  for (const f of (await chrome.webNavigation.getAllFrames({ tabId })) || []) {
    try {
      const h = new URL(f.url).hostname;
      if (h && !SKIP_HOSTS.test(h) && !hosts.includes(h)) hosts.push(h);
    } catch (e) { /* about:blank and the like */ }
  }
  return hosts.slice(0, 6);
}

async function goTo(tabId, cur) {
  const url = cur.done
    ? chrome.runtime.getURL('done.html') + '?s=' + encodeURIComponent(cur.summary || '')
    : BASE + '/go';
  await chrome.tabs.update(tabId, { url });
}

/** e = entered, s = skip for good, l = later. Tells Enter comps, then moves the comp tab to the next comp. */
async function act(choice) {
  const { tabId } = await session();
  if (!tabId) return { error: 'no comp open' };
  const cur = await api('/api/current');
  if (cur.done) return cur;
  const hosts = choice === 'e' ? await hostsOf(tabId) : [];
  const next = await api('/api/action', { id: cur.id, choice, hosts });
  await goTo(tabId, next);
  return next;
}

function showToast(text, warn) {
  const old = document.getElementById('compbot-toast');
  if (old) old.remove();
  const t = document.createElement('div');
  t.id = 'compbot-toast';
  t.style.cssText = 'position:fixed;z-index:2147483647;top:16px;right:16px;max-width:360px;padding:10px 14px;' +
    'border-radius:8px;color:#fff;font:14px/1.4 system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.3);' +
    'background:' + (warn ? '#8a5a00' : '#1f7a4d');
  t.textContent = text;
  document.documentElement.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

async function toast(tabId, text, warn) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, func: showToast, args: [text, !!warn] });
  } catch (e) { /* a page Chrome doesn't let extensions touch */ }
}

/** Fills empty fields in every frame of the tab, including forms embedded from another site. */
async function fill(tabId) {
  let { me } = await chrome.storage.local.get('me');
  try { me = (await api('/api/current')).me || me; } catch (e) { /* Enter comps closed: use the saved details */ }
  if (!me) return toast(tabId, 'Open Enter comps once so the extension knows your details.', true);
  let results = [];
  try {
    results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: self.compbotPrefill, args: [me] });
  } catch (e) {
    return toast(tabId, "Chrome doesn't let extensions fill this page.", true);
  }
  const filled = [...new Set(results.flatMap((r) => r.result || []))];
  await toast(tabId, filled.length ? 'Filled: ' + filled.join(', ') : 'No empty fields found to fill', !filled.length);
}

chrome.action.onClicked.addListener((tab) => fill(tab.id));

chrome.commands.onCommand.addListener((cmd, tab) => {
  if (cmd === 'fill') return fill(tab.id);
  const choice = { entered: 'e', skip: 's', later: 'l' }[cmd];
  if (choice) act(choice).catch(() => toast(tab.id, "Enter comps isn't open.", true));
});

// Messages from panel.js, the bar shown on the comp tab.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === 'claim') {
      try { return reply(await claim(sender.tab.id)); } catch (e) { return reply({ error: String(e) }); }
    }
    const { tabId, shownId } = await session();
    const isComp = sender.tab && sender.tab.id === tabId && sender.frameId === 0;
    if (msg.type === 'status') {
      if (!isComp) return reply({ show: false });
      let cur;
      try { cur = await api('/api/current'); } catch (e) {
        await chrome.storage.session.remove(['tabId', 'shownId']); // Enter comps was closed
        return reply({ show: false });
      }
      // You pressed a key in Enter comps instead of on the page: catch up.
      if (cur.done || (shownId && cur.id !== shownId)) {
        await goTo(tabId, cur);
        return reply({ show: false });
      }
      return reply({ show: true, cur });
    }
    if (msg.type === 'act') {
      try { return reply(await act(msg.choice)); } catch (e) { return reply({ error: String(e) }); }
    }
    if (msg.type === 'fill') {
      await fill(sender.tab.id);
      return reply({});
    }
    reply({});
  })();
  return true; // reply comes later
});
