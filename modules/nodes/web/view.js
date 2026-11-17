// ---------- Nodes: every machine's live numbers, built from the same pieces as the Server tab ----------
// The tab button and the page are added here, so the page only has them while the module is on.
(function () {
  const nav = document.querySelector('.tabs .in'), server = document.getElementById('v-server');
  if (!nav || !server || document.getElementById('v-nodes')) return;
  server.insertAdjacentHTML('afterend', `<section class="view" id="v-nodes" hidden>
    <div class="top"><div><h1>Nodes</h1><p id="nodes-sub">Your machines</p></div>
      <button class="icon-btn" id="nodes-refresh" aria-label="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg></button></div>
    <div id="nodes-body"></div>
  </section>`);
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="7" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.5 7h7M7.4 9.2l3.2 6.6M16.6 9.2l-3.2 6.6"/></svg>';
  (nav.querySelector('[data-tab="server"]') || nav.lastElementChild).insertAdjacentHTML('afterend', `<button class="tab" data-tab="nodes">${icon}Nodes</button>`);

  const feel = (t) => (t < 60 ? 'cool' : t < 80 ? 'warm' : 'hot');
  const GB = (b) => (b / 2 ** 30).toFixed(1) + ' GB';
  const MB = (b) => Math.round(b / 2 ** 20) + ' MB';
  // The bar widths are set after drawing (data-w), so the page markup carries no style text
  const bar = (name, pc, txt) => `<div class="ur"><div class="uh"><span>${name}</span><small class="num">${txt}</small></div><div class="meter"><i${pc >= 90 ? ' class="hi"' : ''} data-w="${Math.min(100, Math.max(0, pc))}"></i></div></div>`;
  const tile = (big, small) => `<div class="card"><b class="num">${big}</b><small>${small}</small></div>`;

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
      + (n.gpu?.temp != null ? tile(Math.round(n.gpu.temp) + '°C', 'GPU temperature, ' + feel(n.gpu.temp)) : '') + '</div>';
    const bars = [n.mem_used && n.mem_total ? bar('Memory', (n.mem_used / n.mem_total) * 100, `${GB(n.mem_used)} of ${Math.round(n.mem_total / 2 ** 30)} GB`) : '',
      n.size && n.free != null ? bar('Storage', ((n.size - n.free) / n.size) * 100, `${GB(n.size - n.free)} of ${Math.round(n.size / 2 ** 30)} GB`) : '',
      n.gpu?.load != null ? bar('GPU load', n.gpu.load, Math.round(n.gpu.load) + '%') : '',
      n.gpu?.used != null && n.gpu.total ? bar('GPU memory', (n.gpu.used / n.gpu.total) * 100, `${MB(n.gpu.used)} of ${MB(n.gpu.total)}`) : ''].join('');
    if (bars) h += `<div class="card use">${bars}</div>`;
    if (n.wants_gpu && !n.gpu) h += `<div class="card sec"><h3>GPU</h3><ul><li class="plain">The GPU numbers aren't coming through. Check that the GPU exporter is running on ${esc(n.name)}.</li></ul></div>`;
    if (n.down?.length) h += `<div class="card sec"><h3>Containers not running<small>last 24 hours</small></h3><ul>${n.down.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
    if (n.checks?.length) h += `<div class="card sec"><h3>Services</h3>${n.checks.map((c) => `<div class="srow"><i class="dot ${c.up ? 'ok' : 'bad'}"></i><span class="nm">${esc(c.name)}</span><span class="vl num">${c.up ? 'Online' : 'Down'}</span></div>`).join('')}</div>`;
    return h;
  }

  let loading = null;
  const loadNodes = () => loading || (loading = loadNow().finally(() => { loading = null; }));
  async function loadNow() {
    const body = document.getElementById('nodes-body');
    if (!body.innerHTML) body.innerHTML = '<div class="skel"></div><div class="skel"></div>';
    let r; try { r = await getJSON('/api/bot/nodes'); } catch { r = null; }
    if (!r) { body.innerHTML = '<div class="err">Can\'t reach your assistant. It may be restarting.</div>'; return; }
    body.classList.toggle('settled', !!body.querySelector('.kv'));  // quiet refreshes don't replay the entrance animation
    body.innerHTML = r.nodes.length ? r.nodes.map(nodeHTML).join('')
      : '<div class="card sec"><h3>No nodes yet</h3><ul><li class="plain">Add your machines under nodes: in config.yaml, then restart. config.example.yaml shows how.</li></ul></div>';
    body.querySelectorAll('[data-w]').forEach((i) => { i.style.width = i.dataset.w + '%'; });
    document.getElementById('nodes-sub').textContent = 'Updated ' + new Date().toLocaleTimeString('en-US', T12);
  }
  document.getElementById('nodes-refresh').onclick = () => spinning(document.getElementById('nodes-refresh'), loadNodes);
  const showTabOnce = showTab;  // the tab bar calls showTab by name, so wrapping it is enough to load when the tab opens
  showTab = function (name) { showTabOnce(name); if (name === 'nodes') loadNodes(); };
})();
