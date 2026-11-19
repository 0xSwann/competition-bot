// Shown in the comp tab when the comp's page can't be loaded, so you can still skip it or try later.
const q = new URLSearchParams(location.search);
document.getElementById('err').textContent = q.get('e') ? '(' + q.get('e').replace('net::', '') + ')' : '';
document.getElementById('url').textContent = q.get('u') || '';

const ask = (msg) => new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(chrome.runtime.lastError ? null : r)));
let busy = false;

document.querySelectorAll('button').forEach((b) => b.addEventListener('click', async () => {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach((x) => { x.disabled = true; });
  await ask(b.dataset.k === 'retry' ? { type: 'retry' } : { type: 'act', choice: b.dataset.k });
}));

// Shows which comp this is, and follows along if you press a key in Enter comps instead.
async function check() {
  if (busy) return;
  const r = await ask({ type: 'status' });
  if (r && r.cur) document.getElementById('title').textContent = `Comp ${r.cur.index} of ${r.cur.total}: ${r.cur.title}`;
}
check();
setInterval(check, 2500);
