"""Nodes: live numbers from each machine's own Prometheus, a Nodes tab, Telegram answers about any machine, and alerts.
Each machine is asked on its own with a short timeout, so one slow or dead machine never holds up the others."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote

from core.helpers import cached, esc, http, now, safe, t12

TIMEOUT = 4      # seconds each question to a machine may take
CACHE = 20       # seconds an answer is kept, so refreshing and chatting do not hammer Prometheus
EVERY = 300      # the alert check runs every 5 minutes
LIMITS = {"temp": 85, "disk": 90, "memory": 92, "fails": 2}  # degrees C, percent full, percent used, checks in a row before an alert

# The same PromQL the Server module uses, so both pages agree
CPU = '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100'
TEMP = 'max(node_hwmon_temp_celsius{chip=~".*coretemp.*"})'
MEM = "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100"
MEM_USED = "node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes"
MEM_TOTAL = "node_memory_MemTotal_bytes"
DISK = '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100'
FREE = 'node_filesystem_avail_bytes{mountpoint="/"}'
SIZE = 'node_filesystem_size_bytes{mountpoint="/"}'
UPTIME = "time() - node_boot_time_seconds"
# Containers (cAdvisor): running now, and the names seen in the last day that are not there now
RUNNING = 'count((time() - container_last_seen{name!=""}) < 120)'
GONE = ('group by (name) (last_over_time(container_last_seen{name!=""}[1d])) '
        'unless group by (name) (container_last_seen{name!=""})')
# More detail for the Server & Apps page
CORES = 'count(count by (cpu) (node_cpu_seconds_total{mode="idle"}))'
LOAD = ("node_load1", "node_load5", "node_load15")
SWAP_TOTAL, SWAP_FREE = "node_memory_SwapTotal_bytes", "node_memory_SwapFree_bytes"
NET = 'device!~"lo|veth.*|docker.*|br-.*|tailscale.*|flannel.*|cni.*"'
NET_IN, NET_OUT = f"sum(rate(node_network_receive_bytes_total{{{NET}}}[1m]))", f"sum(rate(node_network_transmit_bytes_total{{{NET}}}[1m]))"
OS_INFO, UNAME = "node_os_info", "node_uname_info"
FS = 'fstype!~"tmpfs|ramfs|overlay|squashfs|nsfs|devtmpfs|fuse.*|autofs|efivarfs|cgroup.*"'
FS_SIZE, FS_FREE = f"node_filesystem_size_bytes{{{FS}}}", f"node_filesystem_avail_bytes{{{FS}}}"
# GPU (utkuozdemir/nvidia_gpu_exporter)
GPU_LOAD = "avg(nvidia_smi_utilization_gpu_ratio) * 100"
GPU_USED = "sum(nvidia_smi_memory_used_bytes)"
GPU_TOTAL = "sum(nvidia_smi_memory_total_bytes)"
GPU_TEMP = "max(nvidia_smi_temperature_gpu)"


def query(base, q):
    return http(f"{base.rstrip('/')}/api/v1/query?query={quote(q)}", timeout=TIMEOUT)["data"]["result"]


def number(base, q):
    try:
        r = query(base, q)
        return float(r[0]["value"][1]) if r else None
    except Exception:
        return None


def series(base, q):
    """Every series of a query as (labels, value), or None when the machine does not answer."""
    try:
        return [(r["metric"], float(r["value"][1])) for r in query(base, q)]
    except Exception:
        return None


def mounts(base):
    """One row per disk (a disk mounted twice, as btrfs does, is shown once at its shortest mount point), sorted by mount point."""
    size, free = series(base, FS_SIZE), series(base, FS_FREE)
    if not size:
        return None
    left = {(m.get("device"), m.get("mountpoint")): v for m, v in (free or [])}
    best = {}
    for m, v in size:
        if v <= 0 or not m.get("mountpoint"):
            continue
        row = {"mount": m["mountpoint"], "device": (m.get("device") or "").rsplit("/", 1)[-1], "fstype": m.get("fstype", ""), "size": v,
               "free": left.get((m.get("device"), m["mountpoint"]))}
        old = best.get(m.get("device"))
        if old is None or len(row["mount"]) < len(old["mount"]):
            best[m.get("device")] = row
    return sorted(best.values(), key=lambda r: r["mount"])


def names(base, q):
    try:
        return sorted(r["metric"]["name"] for r in query(base, q))
    except Exception:
        return None


def check_url(url):
    """True when the address answers. A page that refuses you (401, 403, 404) is still a service that is up."""
    try:
        http(url, raw=True, timeout=TIMEOUT)
        return True
    except HTTPError as e:
        return e.code < 500
    except Exception:
        return False


FIX = "Check that its Prometheus is running and that Tailscale is up on both machines."


@cached(CACHE)
def read(prom, gpu, checks):
    """One machine's numbers. Never raises: a machine that cannot be read comes back with up False and a plain message."""
    r = {"up": False, "msg": "", "cpu": None, "temp": None, "mem": None, "mem_used": None, "mem_total": None, "disk": None, "free": None,
         "size": None, "uptime": None, "running": None, "down": None, "gpu": None, "checks": [], "cores": None, "load": None,
         "swap_used": None, "swap_total": None, "net_in": None, "net_out": None, "os": "", "kernel": "", "mounts": None}
    try:
        cpu = query(prom, CPU)
    except HTTPError as e:
        r["msg"] = f"Prometheus answered with an error ({e.code}). Check that the address in config.yaml points at Prometheus."
        return r
    except (ValueError, KeyError, TypeError):
        r["msg"] = "Prometheus answered, but not with numbers I can read. Check that the address in config.yaml points at Prometheus."
        return r
    except Exception:
        r["msg"] = f"Can't reach it. {FIX}"
        return r
    r["up"] = True
    asks = {"cpu": None, "temp": TEMP, "mem": MEM, "mem_used": MEM_USED, "mem_total": MEM_TOTAL, "disk": DISK, "free": FREE, "size": SIZE, "uptime": UPTIME, "running": RUNNING, "cores": CORES,
            "load1": LOAD[0], "load5": LOAD[1], "load15": LOAD[2], "swap_total": SWAP_TOTAL, "swap_free": SWAP_FREE, "net_in": NET_IN, "net_out": NET_OUT}
    if gpu:
        asks.update(gpu_load=GPU_LOAD, gpu_used=GPU_USED, gpu_total=GPU_TOTAL, gpu_temp=GPU_TEMP)
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = {k: pool.submit(number, prom, q) for k, q in asks.items() if q}
        gone = pool.submit(names, prom, GONE)
        fs, osr, unr = pool.submit(mounts, prom), pool.submit(series, prom, OS_INFO), pool.submit(series, prom, UNAME)
        ups = [pool.submit(check_url, u) for _, u in checks]
        try:
            r["cpu"] = float(cpu[0]["value"][1]) if cpu else None
        except (ValueError, KeyError, IndexError, TypeError):
            r["cpu"] = None
        for k, f in got.items():
            r[k] = f.result()
        r["down"] = gone.result() if r["running"] is not None else None
        r["mounts"] = fs.result()
        osl, unl = osr.result(), unr.result()
        r["os"] = (osl[0][0].get("pretty_name") or osl[0][0].get("name") or "") if osl else ""
        r["kernel"] = (unl[0][0].get("release") or "") if unl else ""
        r["checks"] = [{"name": n, "up": f.result()} for (n, _), f in zip(checks, ups)]
    r["load"] = [r.pop(k) for k in ("load1", "load5", "load15")]
    r["load"] = r["load"] if all(v is not None for v in r["load"]) else None
    sw, sf = r["swap_total"], r.pop("swap_free")
    r["swap_used"] = (sw - sf) if sw and sf is not None else None
    if gpu:
        g = {"load": r.pop("gpu_load"), "used": r.pop("gpu_used"), "total": r.pop("gpu_total"), "temp": r.pop("gpu_temp")}
        r["gpu"] = g if any(v is not None for v in g.values()) else None
    if r["cpu"] is None:
        r["msg"] = "It answers, but has no machine numbers. Check that node-exporter is running and listed in its prometheus.yml."
    return r


def limits(config):
    out = dict(LIMITS)
    for k, v in (config.get("node_limits", None) or {}).items():
        if k in out and isinstance(v, int) and not isinstance(v, bool) and v > 0:
            out[k] = v
    return out


def configured(config):
    """The machines from config.yaml, primary first, in the order written otherwise. Entries without a name and an address are skipped."""
    rows = [n for n in (config.get("nodes", None) or []) if isinstance(n, dict) and n.get("name") and n.get("prom")]
    return sorted(rows, key=lambda n: not n.get("primary"))


def feel(t):
    return "cool" if t < 60 else "warm" if t < 80 else "hot"


def problems(n, lim):
    """What is wrong with one machine: key, plain text, whether it earns an alert, and the alert and recovery messages."""
    name, out = n["name"], []
    if not n["up"]:
        return [{"key": "down", "text": f"{name} is unreachable", "alert": True, "msg": f"⚠️ <b>{esc(name)} is unreachable</b>\n{esc(n['msg'])}", "back": f"✅ <b>{esc(name)} is back</b>"}]
    if n["msg"]:
        out.append({"key": "nodata", "text": n["msg"], "alert": False})
    if n["temp"] is not None and n["temp"] >= lim["temp"]:
        out.append({"key": "temp", "text": f"temperature {n['temp']:.0f}°C", "alert": True, "msg": f"🌡 <b>{esc(name)} is running hot</b> ({n['temp']:.0f}°C). Check that its vents aren't blocked.", "back": f"✅ <b>{esc(name)} has cooled down</b>"})
    if n["disk"] is not None and n["disk"] >= lim["disk"]:
        out.append({"key": "disk", "text": f"storage {n['disk']:.0f}% full", "alert": True, "msg": f"💾 <b>{esc(name)} storage is {n['disk']:.0f}% full.</b> Time to clean up.", "back": f"✅ <b>{esc(name)} has space again</b>"})
    if n["mem"] is not None and n["mem"] >= lim["memory"]:
        out.append({"key": "memory", "text": f"memory {n['mem']:.0f}% used", "alert": True, "msg": f"🧠 <b>{esc(name)} is low on memory</b> ({n['mem']:.0f}% used).", "back": f"✅ <b>{esc(name)} memory is back to normal</b>"})
    if n["down"]:
        out.append({"key": "gone", "text": f"{len(n['down'])} container{'s' if len(n['down']) != 1 else ''} not running: " + ", ".join(n["down"]), "alert": False})
    for c in n["checks"]:
        if not c["up"]:
            out.append({"key": f"check:{c['name']}", "text": f"{c['name']} is down", "alert": True, "msg": f"🌐 <b>{esc(c['name'])} is down</b> on {esc(name)}.", "back": f"✅ <b>{esc(c['name'])} is back</b> on {esc(name)}"})
    return out


def status(n, probs):
    if not n["up"] or any(c["key"].startswith("check:") for c in probs):
        return "bad"
    return "warn" if probs else "ok"


def gather(config):
    """Every machine, read side by side. The result keeps the config order, primary first."""
    rows, lim = configured(config), limits(config)

    def one(row):
        checks = tuple((c["name"], c["url"]) for c in (row.get("checks") or []) if isinstance(c, dict) and c.get("name") and c.get("url"))
        n = {"name": str(row["name"]), "primary": bool(row.get("primary")), "wants_gpu": bool(row.get("gpu")), **read(str(row["prom"]), bool(row.get("gpu")), checks)}
        n["problems"] = problems(n, lim)
        n["status"] = status(n, n["problems"])
        return n
    with ThreadPoolExecutor(max_workers=max(1, len(rows))) as pool:
        return list(pool.map(one, rows))


def public(n):
    """What the dashboard gets: no addresses, no alert wording."""
    return {**{k: v for k, v in n.items() if k != "problems"}, "problems": [p["text"] for p in n["problems"]]}


# ================================================================ Telegram
def block(n):
    name = esc(n["name"])
    if not n["up"]:
        return unreachable(n)
    texts = [p["text"] for p in n["problems"]]
    lines = [f"✅ <b>{name}</b> is up" if not texts else f"⚠️ <b>{name}</b> needs attention\n" + "\n".join(f"• {esc(x)}" for x in texts)]
    if n["cpu"] is not None:
        lines.append(f"🧠 Working at {n['cpu']:.0f}%" + (f" • memory {n['mem']:.0f}% used" if n["mem"] is not None else ""))
        bits = ([f"🌡 {n['temp']:.0f}°C ({feel(n['temp'])})"] if n["temp"] is not None else []) + ([f"💾 storage {n['disk']:.0f}% used"] if n["disk"] is not None else [])
        if bits:
            lines.append(" • ".join(bits))
        bits = ([f"📦 {n['running']:.0f} containers running"] if n["running"] is not None else []) + ([f"on for {n['uptime'] / 86400:.1f} days"] if n["uptime"] is not None else [])
        if bits:
            lines.append(" • ".join(bits))
    if n["wants_gpu"]:
        lines.append(gpu_line(n))
    return "\n".join(lines)


def gpu_line(n, long=False):
    g = n["gpu"]
    if not g:
        return f"🎮 GPU numbers aren't coming through. Check that the GPU exporter is running on {esc(n['name'])}."
    bits = ([f"{g['load']:.0f}% busy"] if g["load"] is not None else []) + ([f"{g['used'] / 2**20:.0f} of {g['total'] / 2**20:.0f} MB memory"] if g["used"] is not None and g["total"] else []) \
        + ([f"{g['temp']:.0f}°C ({feel(g['temp'])})"] if g["temp"] is not None else [])
    return "🎮 GPU " + " • ".join(bits) if bits else "🎮 GPU numbers aren't coming through."


def unreachable(n):
    return f"⚠️ <b>{esc(n['name'])}</b> is unreachable\n{esc(n['msg'])}"


def topic_line(topic, n):
    """One machine's answer to a single question (temperature, disk, memory, containers, gpu)."""
    name = esc(n["name"])
    if not n["up"]:
        return unreachable(n)
    if topic == "gpu":
        return gpu_line(n) if n["wants_gpu"] else f"🎮 {name} has no GPU."
    if topic == "temp":
        return f"🌡 {name}: {n['temp']:.0f}°C ({feel(n['temp'])})" if n["temp"] is not None else f"🌡 {name}: no temperature reading."
    if topic == "disk":
        if n["disk"] is None:
            return f"💾 {name}: no storage reading."
        free = f", {n['free'] / 1e9:.0f} GB free of {n['size'] / 1e9:.0f} GB" if n["free"] is not None and n["size"] else ""
        return f"💾 {name}: storage {n['disk']:.0f}% used{free}"
    if topic == "memory":
        return f"🧠 {name}: memory {n['mem']:.0f}% used" if n["mem"] is not None else f"🧠 {name}: no memory reading."
    if topic == "containers":
        if n["running"] is None:
            return f"📦 {name}: no container numbers. Add cAdvisor to {esc(n['name'])}'s monitoring to see them."
        if n["down"]:
            return f"📦 {name}: {len(n['down'])} not running\n" + "\n".join(f"• {esc(x)}" for x in n["down"])
        return f"📦 {name}: all {n['running']:.0f} containers running"
    return block(n)


TOPICS = (("gpu", r"\bgpu\b|\bvram\b|graphics card"), ("temp", r"temperatures?|\btemps?\b|how hot|\bheat\b"), ("disk", r"\bdisk\b|storage|\bspace\b"),
          ("memory", r"\bmemory\b|\bram\b"), ("containers", r"\bcontainers?\b|\bapps?\b.*\bdown\b"))
LEAVE = r"\b(restart|reboot|logs?|remind|reminders?|notes?|n8n|workflows?|search|google)\b|node\.?js"  # other features own these
ALL = r"\b(both|nodes|machines)\b|\b(all|every) (my |the )?(servers?|boxes|computers)\b"


def pick(t, rows):
    """Which machines a sentence names: (list, named, missing). Named is True when it says a number, a name, or both/all."""
    chosen, missing = [], []
    for m in re.finditer(r"\bnode\s*(\d+)\b|\b(\d+)(?:st|nd|rd|th)? node\b", t):
        i = int(m.group(1) or m.group(2))
        (chosen.append(rows[i - 1]) if 1 <= i <= len(rows) else missing.append(i))
    for r in rows:
        if r not in chosen and re.search(rf"\b{re.escape(r['name'].lower())}\b", t):
            chosen.append(r)
    named = bool(chosen or missing)
    if re.search(ALL, t):
        return list(rows), True, []
    return chosen, named, missing


def answer(config, send, typing, text, t):
    """The Telegram side. Returns True when the sentence was about nodes (and has been answered), False to let the bot carry on."""
    if re.search(LEAVE, t):
        return False
    rows = configured(config)
    command = re.fullmatch(r"/nodes(@\w+)?", t) is not None
    chosen, named, missing = pick(t, rows)
    topic = next((k for k, rx in TOPICS if re.search(rx, t)), None)
    bare = (topic == "temp" and re.fullmatch(r"(show |my |the |all )?(temps|temperatures)( now| please)?\??", t) is not None) or \
           (topic == "containers" and re.search(r"\b(down|stopped|not running|dead)\b", t) is not None and not named)
    if not (command or named or topic == "gpu" or bare):
        return False
    if not rows:
        send("🖥 No nodes are set up yet. Add your machines under <code>nodes:</code> in config.yaml.")
        return True
    if missing:
        send(f"🖥 I only know {len(rows)} node{'s' if len(rows) != 1 else ''}: " + ", ".join(esc(r["name"]) for r in rows) + ".")
        return True
    typing()
    if not named:
        chosen = [r for r in rows if r.get("gpu")] if topic == "gpu" else rows
        if topic == "gpu" and not chosen:
            send("🎮 No node is set up with a GPU. Add <code>gpu: true</code> to one under <code>nodes:</code> in config.yaml.")
            return True
    topic = None if command else topic
    out = [topic_line(topic, n) for n in gather({"nodes": chosen, "node_limits": config.get("node_limits", None)})]
    send(("\n\n" if topic is None else "\n").join(out))
    return True


# ================================================================ alerts
def check_alerts(app, send=None):
    """One pass: tell Telegram once when something goes wrong and once when it clears. State is kept in nodes-alerts.json."""
    send = send or app.telegram.send
    seen = (app.store.read("nodes-alerts.json", {}) or {}).get("open", {})
    lim = limits(app.config.data)
    ns = gather(app.config.data)
    current, down = {}, set()
    for n in ns:
        if not n["up"]:
            down.add(n["name"])
        for p in n["problems"]:
            if p["alert"]:
                current[f"{n['name']}|{p['key']}"] = p
    for key, p in current.items():
        e = seen.setdefault(key, {"n": 0, "sent": False, "at": time.time()})
        e["n"] += 1
        e["back"] = p["back"]
        if e["n"] >= lim["fails"] and not e["sent"]:
            send(p["msg"])
            e["sent"] = True
    for key in list(seen):
        if key in current or key.split("|")[0] in down:  # a machine that is down says nothing about its other readings
            continue
        e = seen.pop(key)
        if e["sent"]:
            since = t12(datetime.fromtimestamp(e["at"], now().tzinfo))
            send(f"{e['back']}\n<i>Since {since}</i>")
    app.store.write("nodes-alerts.json", {"open": seen})


def register(app):
    cfg = app.config

    @app.router.get("/nodes")
    def nodes_view(req):
        return {"nodes": [public(n) for n in gather(cfg.data)], "generated": int(time.time())}

    def hook(text, t):
        try:
            return answer(cfg.data, app.telegram.send, app.telegram.typing, text, t)
        except Exception as e:
            print(f"nodes: {type(e).__name__}: {e}")
            app.telegram.send("😕 I can't read the nodes right now. Try again in a minute.")
            return True

    app.text_hooks.append(hook)

    if configured(cfg.data):
        def loop():
            time.sleep(60)  # let the app finish starting
            while True:
                safe(lambda: check_alerts(app))
                time.sleep(EVERY)
        threading.Thread(target=loop, daemon=True).start()
