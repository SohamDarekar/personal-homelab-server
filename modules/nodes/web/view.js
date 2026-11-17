// ---------- Nodes: every machine you watch, shown on the Server & Apps page ----------
// It adds two blocks under the Server page's own cards and uses that page's own pieces (cards, bars, rows), so it looks the same.
// There is no tab of its own: it loads when Server & Apps opens and when you refresh it.
(function () {
  const anchor = document.getElementById('server-body');
  if (!anchor || document.getElementById('nodes-body')) return;
  anchor.insertAdjacentHTML('afterend', '<div id="nodes-body"></div><div id="nodes-ai"></div>');

  const feel = (t) => (t < 60 ? 'cool' : t < 80 ? 'warm' : 'hot');
  const MB = (b) => Math.round(b / 2 ** 20) + ' MB';
  const rate = (b) => (b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB/s' : Math.round(b / 1e3) + ' KB/s');
  const usedTxt = (used, size) => `${used < 1e9 ? Math.max(1, Math.round(used / 1e6)) + ' MB' : GB(used)} used of ${GB(size)}`;
  const bar = (name, used, size, txt, sub) => { const pc = Math.min(100, Math.max(0, (used / size) * 100));
    return `<div class="ur"><div class="uh"><span>${esc(name)}</span><small class="num">${txt}</small></div><div class="meter"><i${pc >= 90 ? ' class="hi"' : ''} style="width:${pc}%"></i></div>${sub ? `<div class="ai-key num">${esc(sub)}</div>` : ''}</div>`; };
  const tile = (big, small) => `<div class="card"><b class="num">${big}</b><small>${small}</small></div>`;
  const row = (name, val) => `<div class="srow"><span class="nm">${esc(name)}</span><span class="vl num">${esc(val)}</span></div>`;

  function nodeHTML(n) {
    const word = !n.up ? 'Unreachable' : n.status === 'ok' ? 'Up' : 'Needs attention';
    let h = `<div class="label">${esc(n.name)}</div><div class="card st-head"><i class="dot ${n.status}"></i><div><b>${word}</b>${n.primary ? '<small>Main machine</small>' : ''}</div></div>`;
    if (!n.up) return h + `<div class="card sec"><h3>Can't reach ${esc(n.name)}</h3><ul><li class="plain">${esc(n.msg)}</li></ul></div>`;
    if (n.cpu == null) return h + `<div class="card sec"><h3>No numbers yet</h3><ul><li class="plain">${esc(n.msg)}</li></ul></div>`;
    h += `<div class="kv">${tile(Math.round(n.cpu) + '%', 'How busy it is')}`
      + (n.temp != null ? tile(Math.round(n.temp) + '°C', 'Temperature, ' + feel(n.temp)) : '')
      + (n.mem != null ? tile(Math.round(n.mem) + '%', 'Memory used') : '')
      + (n.free != null && n.size ? tile(Math.round(n.free / 1e9) + ' GB', `Free, out of ${Math.round(n.size / 1e9)} GB`) : '')
      + (n.uptime != null ? tile((n.uptime / 86400).toFixed(1) + 'd', 'Since last restart') : '')
      + (n.running != null ? tile(Math.round(n.running), 'Containers running') : '')
      + (n.cores != null ? tile(Math.round(n.cores), 'CPU cores') : '')
      + (n.load ? tile(n.load[0].toFixed(2), `Load, 5 min ${n.load[1].toFixed(2)}`) : '')
      + (n.gpu?.temp != null ? tile(Math.round(n.gpu.temp) + '°C', 'GPU temperature, ' + feel(n.gpu.temp)) : '') + '</div>';
    const bars = [n.mem_used && n.mem_total ? bar('Memory', n.mem_used, n.mem_total, `${GB(n.mem_used)} of ${Math.round(n.mem_total / 2 ** 30)} GB`) : '',
      n.swap_total ? bar('Swap', n.swap_used || 0, n.swap_total, usedTxt(n.swap_used || 0, n.swap_total)) : '',
      n.gpu?.load != null ? bar('GPU load', n.gpu.load, 100, Math.round(n.gpu.load) + '%') : '',
      n.gpu?.used != null && n.gpu.total ? bar('GPU memory', n.gpu.used, n.gpu.total, `${MB(n.gpu.used)} of ${MB(n.gpu.total)}`) : '',
      ...(n.mounts || []).filter((m) => m.free != null).map((m) => bar(m.mount, m.size - m.free, m.size, usedTxt(m.size - m.free, m.size), [m.device, m.fstype].filter(Boolean).join(' • ')))].join('');
    if (bars) h += `<div class="card use">${bars}</div>`;
    const sys = [n.os && row('Operating system', n.os), n.kernel && row('Kernel', n.kernel), n.load && row('Load, 1 / 5 / 15 min', n.load.map((x) => x.toFixed(2)).join(' / ')),
      n.net_in != null && row('Network in', rate(n.net_in)), n.net_out != null && row('Network out', rate(n.net_out))].filter(Boolean).join('');
    if (sys) h += `<div class="card sec"><h3>System</h3>${sys}</div>`;
    if (n.wants_gpu && !n.gpu) h += `<div class="card sec"><h3>GPU</h3><ul><li class="plain">The GPU numbers aren't coming through. Check that the GPU exporter is running on ${esc(n.name)}.</li></ul></div>`;
    if (n.down?.length) h += `<div class="card sec"><h3>Containers not running<small>last 24 hours</small></h3><ul>${n.down.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    if (n.checks?.length) h += `<div class="card sec"><h3>Services</h3>${n.checks.map((c) => `<div class="srow"><i class="dot ${c.up ? 'ok' : 'bad'}"></i><span class="nm">${esc(c.name)}</span><span class="vl num">${c.up ? 'Online' : 'Down'}</span></div>`).join('')}</div>`;
    return h;
  }

  // AI usage (tokens in and out of the local model). It used to sit at the bottom of the Health tab; it lives here now.
  const d0 = (s) => new Date(s + 'T12:00:00');
  const short = (s) => d0(s).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  const add = (s, n) => { const x = d0(s); x.setDate(x.getDate() + n); return isoDay(x); };
  const tk = (n) => (n >= 1e6 ? +(n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M' : n >= 1e3 ? +(n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k' : String(Math.round(n)));
  function aiHTML(a) {
    const sum = (ds) => ds.reduce((t, d) => { for (const v of Object.values(a.days[d] || {})) { t.in += v.in; t.out += v.out; t.calls += v.calls; } return t; }, { in: 0, out: 0, calls: 0 });
    const mon = add(a.today, -((d0(a.today).getDay() + 6) % 7)), week = [...Array(7)].map((_, i) => add(mon, i)).filter((d) => d <= a.today);
    const x = a.total || sum(week), col = (k, v) => `<span><small>${k}</small><b class="num">${tk(v)}</b><small>${v.toLocaleString('en-IN')}</small></span>`;
    return `<div class="label">AI usage<span class="lr">All models</span></div>
      <div class="card ai-c3"><div><p>So far, since ${short(a.first)}</p>${col('In', x.in)}${col('Out', x.out)}</div></div>
      <p class="hint ai-note">Tokens in and out. Days run midnight to midnight (server time). Open WebUI and n8n aren’t counted.</p>`;
  }
  async function loadAI() {
    const box = document.getElementById('nodes-ai');
    try { box.innerHTML = aiHTML(await getJSON('/api/bot/sg/ai')); }
    catch (e) { box.innerHTML = String(e.message) === '404' ? '' : '<div class="err">Couldn’t load AI usage right now. Try again in a minute.</div>'; }
  }

  let loading = null;
  const loadNodes = () => loading || (loading = loadNow().finally(() => { loading = null; }));
  async function loadNow() {
    const body = document.getElementById('nodes-body');
    if (!body.innerHTML) body.innerHTML = '<div class="skel"></div>';
    loadAI();
    let r; try { r = await getJSON('/api/bot/nodes'); } catch { r = null; }
    if (!r) { body.innerHTML = '<div class="err">Can\'t reach your assistant. It may be restarting.</div>'; return; }
    body.classList.toggle('settled', !!body.querySelector('.kv'));  // quiet refreshes don't replay the entrance animation
    body.innerHTML = r.nodes.length ? r.nodes.map(nodeHTML).join('')
      : '<div class="card sec"><h3>No nodes yet</h3><ul><li class="plain">Add your machines under nodes: in config.yaml, then restart. config.example.yaml shows how.</li></ul></div>';
  }
  const refresh = document.getElementById('server-refresh');
  if (refresh) refresh.addEventListener('click', loadNodes);
  const showTabOnce = showTab;  // the tab bar calls showTab by name, so wrapping it is enough to load when Server & Apps opens
  showTab = function (name) { showTabOnce(name); if (name === 'server') loadNodes(); };
})();
