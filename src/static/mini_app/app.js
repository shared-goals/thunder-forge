async function loadTranslations() {
  const res = await fetch('/mini-app/translations.json');
  if (!res.ok) return {};
  return await res.json();
}

function applyI18n(tr) {
  for (const el of document.querySelectorAll('[data-i18n]')) {
    const key = el.getAttribute('data-i18n');
    if (key && tr[key]) el.textContent = tr[key];
  }
}

function getInitData() {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.initData) return tg.initData;

  // iOS / desktop fallback: Telegram may pass init data in URL hash
  if (window.location.hash && window.location.hash.startsWith('#tgWebAppData=')) {
    return decodeURIComponent(window.location.hash.slice('#tgWebAppData='.length));
  }
  return '';
}

async function postJson(url, initData) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'tma ' + initData,
    },
    body: JSON.stringify({}),
  });

  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }

  if (!res.ok) {
    const detail = data && (data.detail || data.error) ? (data.detail || data.error) : res.statusText;
    throw new Error(res.status + ' ' + detail);
  }
  return data;
}

(async () => {
  const out = document.getElementById('out');
  const tr = await loadTranslations();
  applyI18n(tr);

  const initData = getInitData();
  if (!initData) {
    out.textContent = 'Missing Telegram init data (open inside Telegram).';
    return;
  }

  try {
    const me = await postJson('/api/mini-app/me', initData);
    const status = await postJson('/api/mini-app/status', initData);
    out.textContent = JSON.stringify({ me, status }, null, 2);
  } catch (e) {
    out.textContent = String(e && e.message ? e.message : e);
  }
})();
