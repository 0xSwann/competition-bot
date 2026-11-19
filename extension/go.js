// Runs on Enter comps' "go" page (127.0.0.1:47615/go): makes this tab the comp tab, then opens the comp.
document.documentElement.dataset.compbot = '1'; // tells the page the extension is here, so it waits for us
chrome.runtime.sendMessage({ type: 'claim' }, (cur) => {
  if (chrome.runtime.lastError || !cur || cur.error) return location.replace('/go?direct=1');
  location.replace(cur.done ? '/done' : cur.goUrl);
});
