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
  const usedTxt = (used, size) => `${used < 1e9 ? Math.round(used / 1e6) + ' MB' : GB(used)} used of ${GB(size)}`;
  const bar = (name, used, size, txt, sub) => { const pc = Math.min(100, Math.max(0, (used / size) * 100));
    return `<div class="ur"><div class="uh"><span>${esc(name)}</span><small class="num">${txt}</small></div><div class="meter"><i${pc >= 90 ? ' class="hi"' : ''} style="width:${pc}%"></i></div>${sub ? `<div class="ai-key num">${esc(sub)}</div>` : ''}</div>`; };
  const tile = (big, small) => `<div class="card"><b class="num">${big}</b><small>${small}</small></div>`;
  const srow = (name, val) => `<div class="srow"><span class="nm">${esc(name)}</span><span class="vl num">${esc(val)}</span></div>`;

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
    const sys = [n.os && srow('Operating system', n.os), n.kernel && srow('Kernel', n.kernel), n.load && srow('Load, 1 / 5 / 15 min', n.load.map((x) => x.toFixed(2)).join(' / ')),
      n.net_in != null && srow('Network in', rate(n.net_in)), n.net_out != null && srow('Network out', rate(n.net_out))].filter(Boolean).join('');
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


  // ----- Hardware specs and health: the other machines, built from that panel's own cards -----
  const when = (t) => { const d = new Date(t * 1000), n = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(t * 1000).setHours(0, 0, 0, 0)) / 864e5);
    return `${n === 0 ? 'today' : n === 1 ? 'yesterday' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString('en-US', T12)}`; };
  const icon = (k, w) => (typeof sIcon === 'function' && typeof SI !== 'undefined' && SI[k] ? sIcon(k, w) : '');
  const spec = (k, title, sub, body) => (body ? `<div class="card spec"><h3>${icon(k, 2)}${title}${sub ? `<small>${sub}</small>` : ''}</h3>${body}</div>` : '');
  const meter = (used, size) => (size ? `<div class="meter" style="margin-top:8px"><i${used / size >= 0.9 ? ' class="hi"' : ''} style="width:${Math.min(100, (used / size) * 100)}%"></i></div>` : '');
  function hwNode(n) {
    const head = (sub) => `<div class="label">${esc(n.name)}</div><div class="card hw-head">${icon('box', 1.4)}<div><b>${esc(n.machine || n.name)}</b><small>${esc(sub)}</small></div></div>`;
    if (!n.up) return head('Unreachable') + `<div class="card sec"><h3>Can't reach ${esc(n.name)}</h3><ul><li class="plain">${esc(n.msg)}</li></ul></div>`;
    const up = n.uptime ? (n.uptime >= 86400 ? `${Math.floor(n.uptime / 86400)}d ${Math.floor((n.uptime % 86400) / 3600)}h` : `${Math.floor(n.uptime / 3600)}h ${Math.floor((n.uptime % 3600) / 60)}m`) : '';
    const cores = n.cores_phys && n.cores && n.cores !== n.cores_phys ? `${n.cores_phys} cores, ${n.cores} threads` : n.cores ? `${n.cores} cores` : '';
    const disks = (n.mounts || []).filter((m) => m.free != null).map((m) => spec('disk', esc(m.mount), `${GB(m.free)} free`,
      meter(m.size - m.free, m.size) + row('Storage', usedTxt(m.size - m.free, m.size)) + row('Disk', esc(m.device)) + row('File system', esc(m.fstype)))).join('');
    const hot = n.temps ? Math.max(...n.temps.map((t) => t.value)) : null;
    return head([n.os, up && 'up ' + up].filter(Boolean).join(' • '))
      + spec('cpu', 'Processor', n.cpu != null ? `${Math.round(n.cpu)}% busy` : '', row('Chip', esc(n.cpu_model)) + row('Cores', cores) + (n.ghz ? row('Clock speed', `up to ${(n.ghz / 1e9).toFixed(1)} GHz`) : '')
        + (n.load ? row('Load', n.load[0].toFixed(2)) : ''))
      + spec('ram', 'Memory', n.mem_used ? `${GB(n.mem_used)} in use` : '', (n.mem_used && n.mem_total ? meter(n.mem_used, n.mem_total) : '')
        + (n.mem_total ? row('Installed', `${Math.round(n.mem_total / 2 ** 30)} GB`) : '') + (n.swap_total ? row('Swap', usedTxt(n.swap_used || 0, n.swap_total)) : ''))
      + disks
      + (n.gpu ? spec('cpu', 'Graphics card', n.gpu.temp != null ? `${Math.round(n.gpu.temp)}°C` : '', (n.gpu.load != null ? row('Load', Math.round(n.gpu.load) + '%') : '')
        + (n.gpu.used != null && n.gpu.total ? row('Memory', `${MB(n.gpu.used)} of ${MB(n.gpu.total)}`) : '')) : '')
      + spec('soft', 'Temperatures', hot != null ? `hottest ${Math.round(hot)}°C` : '', (n.temps || []).map((t) => row(esc([t.name, t.sensor].filter(Boolean).join(' ')), Math.round(t.value) + '°C')).join(''))
      + spec('net', 'Network', n.net_in != null ? `in ${rate(n.net_in)} • out ${rate(n.net_out || 0)}` : '', (n.nics || []).map((i) => row(esc(i.name), i.up ? `Up${i.mbit ? `, ${i.mbit >= 1000 ? i.mbit / 1000 + ' Gbit' : i.mbit + ' Mbit'}` : ''}` : 'Down')).join(''))
      + spec('soft', 'Software', '', row('System', esc(n.os)) + row('Kernel', esc(n.kernel)) + (n.boot ? row('Started', when(n.boot)) : ''))
      + spec('box', 'Health', n.status === 'ok' ? 'All fine' : 'Needs attention', (n.running != null ? row('Containers running', String(Math.round(n.running))) : '')
        + (n.down?.length ? row('Not running', esc(n.down.join(', '))) : '') + (n.problems || []).map((t) => row('Problem', esc(t))).join('')
        + (n.checks || []).map((c) => row(esc(c.name), c.up ? 'Online' : 'Down')).join(''));
  }
  // The panel's own code rewrites #hw-body each time Server & Apps loads, so these sections are put back whenever that happens
  const hwBody = document.getElementById('hw-body');
  let hwHTML = '';
  const placeHw = () => {
    if (!hwBody || !hwHTML) return;
    let box = document.getElementById('nodes-hw');
    if (!box) { box = document.createElement('div'); box.id = 'nodes-hw'; hwBody.appendChild(box); }
    if (box.innerHTML !== hwHTML) box.innerHTML = hwHTML;
  };
  if (hwBody) new MutationObserver(placeHw).observe(hwBody, { childList: true });

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
    hwHTML = r.nodes.filter((n) => !n.primary).map(hwNode).join(''); placeHw();
  }
  const refresh = document.getElementById('server-refresh');
  if (refresh) refresh.addEventListener('click', loadNodes);
  const showTabOnce = showTab;  // the tab bar calls showTab by name, so wrapping it is enough to load when Server & Apps opens
  showTab = function (name) { showTabOnce(name); if (name === 'server') loadNodes(); };
})();
