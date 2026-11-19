document.getElementById('summary').textContent = new URLSearchParams(location.search).get('s') || 'No comps waiting.';
