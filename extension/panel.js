// The Competition bot bar on the comp tab: which comp this is, and Fill / Entered / Skip / Later buttons.
(() => {
  if (window.top !== window || window.__compbotPanel) return;
  window.__compbotPanel = true;

  let host = null, timer = null, busy = false, collapsed = false;

  const ask = (msg) => new Promise((resolve) => {
    try { chrome.runtime.sendMessage(msg, (r) => resolve(chrome.runtime.lastError ? null : r)); }
    catch (e) { resolve(null); } // the extension was reloaded: this page's copy is stale
  });

  const money = (n) => '$' + Math.round(n).toLocaleString('en-AU');

  function render(cur) {
    if (!host) {
      host = document.createElement('div');
      host.style.cssText = 'all:initial;position:fixed;z-index:2147483646;left:16px;bottom:16px';
      document.documentElement.appendChild(host);
      host.attachShadow({ mode: 'open' });
    }
    const info = [`Comp ${cur.index} of ${cur.total}`];
    if (cur.prize) info.push('about ' + money(cur.prize));
    if (cur.closes) info.push('closes ' + cur.closes);
    if (cur.daily) info.push("today's daily entry");
    host.shadowRoot.innerHTML = `
      <style>
        .bar { font: 13px/1.35 system-ui, sans-serif; color: #1b1b1b; background: #fff; border: 1px solid #d6d6d6;
               border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.18); padding: 10px 12px; width: 300px; }
        .top { display: flex; justify-content: space-between; gap: 8px; color: #555; font-size: 12px; }
        .title { font-weight: 600; margin: 4px 0 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .row { display: flex; gap: 6px; }
        button { font: 600 13px system-ui, sans-serif; border-radius: 7px; border: 1px solid #c9c9c9; background: #f4f4f4;
                 color: #1b1b1b; padding: 6px 8px; cursor: pointer; flex: 1; }
        button:disabled { opacity: .5; cursor: default; }
        .fill { background: #1f7a4d; border-color: #1f7a4d; color: #fff; width: 100%; margin-bottom: 6px; }
        .entered { background: #e7f4ec; border-color: #9fd0b2; }
        .tip { color: #777; font-size: 11px; margin-top: 6px; }
        .warn { background: #fff4d6; border: 1px solid #e9c46a; border-radius: 7px; padding: 6px 8px; margin: 0 0 8px;
                color: #5c4200; font-size: 12px; }
        .min { background: none; border: 0; flex: none; padding: 0 2px; color: #777; font-size: 15px; line-height: 1; }
        .pill { font: 600 12px system-ui, sans-serif; background: #1f7a4d; color: #fff; border: 0; border-radius: 16px;
                padding: 7px 12px; flex: none; }
      </style>
      ${collapsed ? `<button class="pill" data-k="expand">Comp ${cur.index}/${cur.total}</button>` : `
      <div class="bar">
        <div class="top"><span>${info.map(esc).join(' · ')}</span><button class="min" data-k="collapse" title="Make smaller">–</button></div>
        <div class="title" title="${esc(cur.title)}">${esc(cur.title)}</div>
        ${cur.sameSite ? `<div class="warn">You entered a comp on this website on ${esc(cur.sameSite.on)}:
          <b>${esc(cur.sameSite.title)}</b>. If it's the same comp, press Skip.</div>` : ''}
        ${cur.daily ? `<div class="warn">Back for today's daily entry. If the site says you've already entered,
          press Skip and it won't come back.</div>` : ''}
        <button class="fill" data-k="fill">Fill my details</button>
        <div class="row">
          <button class="entered" data-k="e" title="I submitted it. Go to the next comp.">Entered</button>
          <button data-k="s" title="Never show this comp again">Skip</button>
          <button data-k="l" title="Show it again next time">Later</button>
        </div>
        <div class="tip">Alt+Shift+F fill · E entered · S skip · L later</div>
      </div>`}`;
    host.shadowRoot.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => click(b.dataset.k)));
    host._cur = cur;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function remove() {
    if (host) host.remove();
    host = null;
    clearInterval(timer);
  }

  async function click(k) {
    if (k === 'collapse' || k === 'expand') { collapsed = k === 'collapse'; return render(host._cur); }
    if (busy) return;
    if (k === 'fill') return ask({ type: 'fill' });
    busy = true;
    host.shadowRoot.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    await ask({ type: 'act', choice: k });
    busy = false; // the tab moves on to the next comp
  }

  async function check() {
    if (busy) return;
    const r = await ask({ type: 'status' });
    if (!r || !r.show) return remove();
    if (!host || !host._cur || host._cur.id !== r.cur.id) render(r.cur);
  }

  check().then(() => { if (host) timer = setInterval(check, 2500); });
})();
