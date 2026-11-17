"""The Nodes module: two machines read through fake Prometheus replies (both up, one down, bad JSON, timeout, no GPU numbers),
every Telegram sentence, the routing against the existing Server phrases, alerts that fire once and recover, and the config check."""
import os
import re
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import harness
from core import capabilities, config, http_api, scheduler, store
from core.app import App
from modules import nodes
from phrases import PHRASES

N1, N2 = "http://192.168.1.20:9090", "http://10.0.0.5:9090"
UP = {nodes.CPU: 12.4, nodes.TEMP: 54, nodes.MEM: 41, nodes.MEM_USED: 6.4e9, nodes.MEM_TOTAL: 15.6e9, nodes.DISK: 63, nodes.FREE: 3.7e11,
      nodes.SIZE: 1e12, nodes.UPTIME: 6.2 * 86400, nodes.RUNNING: 14, nodes.GPU_LOAD: 3, nodes.GPU_USED: 412 * 2 ** 20,
      nodes.GPU_TOTAL: 4096 * 2 ** 20, nodes.GPU_TEMP: 41}
CONF = {"nodes": [{"name": "Node 2", "prom": N2}, {"name": "Node 1", "prom": N1, "primary": True, "gpu": True, "checks": [{"name": "Pi-hole", "url": "http://192.168.1.20/admin"}]}]}


def vec(v):
    return {"data": {"result": [{"metric": {}, "value": [0, str(v)]}]}}


class Fake:
    """Stands in for core.helpers.http. modes maps a machine address to how it behaves."""

    def __init__(self):
        self.modes = {N1: "up", N2: "up"}
        self.values = {N1: dict(UP), N2: {k: v for k, v in UP.items() if not k.startswith("avg(nvidia") and "nvidia" not in k}}
        self.gone = {N1: [], N2: []}
        self.pages = {"http://192.168.1.20/admin": 200}
        self.calls = []

    def __call__(self, url, data=None, timeout=30, headers=None, raw=False, method=None, binary=False):
        self.calls.append(url)
        if "/api/v1/query" not in url:  # a service check
            code = self.pages.get(url)
            if code is None:
                raise URLError("refused")
            if code >= 400:
                raise HTTPError(url, code, "x", {}, None)
            return "ok"
        base = url.split("/api/v1/")[0]
        mode = self.modes[base]
        if mode == "down":
            raise URLError("connection refused")
        if mode == "timeout":
            raise TimeoutError("timed out")
        if mode == "badjson":
            raise ValueError("Expecting value")
        if mode == "weird":
            return {"nope": 1}
        if mode == "error":
            raise HTTPError(url, 503, "unavailable", {}, None)
        q = parse_qs(urlparse(url).query)["query"][0]
        if q == nodes.GONE:
            return {"data": {"result": [{"metric": {"name": n}} for n in self.gone[base]]}}
        v = self.values[base].get(q)
        return vec(v) if v is not None else {"data": {"result": []}}


class FakeTelegram:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, text, *a, **k):
        self.sent.append(text)

    def typing(self, *a, **k):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self._http = nodes.http
        nodes.http = self.fake
        nodes.read.clear()
        self.addCleanup(lambda: setattr(nodes, "http", self._http))

    def make_app(self, data=None):
        self.tg = FakeTelegram()
        app = App(config.Config(data if data is not None else CONF), http_api.Router(), scheduler.Scheduler("UTC"), capabilities.Capabilities(),
                  store.Store(tempfile.mkdtemp()), None, self.tg)
        nodes.register(app)
        return app

    def ask(self, text, data=None):
        """What the bot sends for a sentence: the reply, or None when the module leaves it alone."""
        self.tg = FakeTelegram()
        nodes.read.clear()
        t = text.lower().strip()
        handled = nodes.answer(data if data is not None else CONF, self.tg.send, self.tg.typing, text, t)
        return "\n".join(self.tg.sent) if handled else None


class Reading(Base):
    def test_both_up_primary_first(self):
        ns = nodes.gather(CONF)
        self.assertEqual([n["name"] for n in ns], ["Node 1", "Node 2"])
        self.assertTrue(all(n["up"] and n["status"] == "ok" for n in ns))
        self.assertEqual(round(ns[0]["gpu"]["load"]), 3)
        self.assertIsNone(ns[1]["gpu"])
        self.assertEqual(ns[0]["checks"], [{"name": "Pi-hole", "up": True}])

    def test_one_down_does_not_break_the_other(self):
        self.fake.modes[N2] = "down"
        a, b = nodes.gather(CONF)
        self.assertTrue(a["up"] and a["status"] == "ok")
        self.assertFalse(b["up"])
        self.assertEqual(b["status"], "bad")
        self.assertIn("Check that its Prometheus is running", b["msg"])
        self.assertNotIn("Traceback", b["msg"])

    def test_bad_answers_say_what_to_check(self):
        for mode, words in (("badjson", "numbers I can read"), ("weird", "numbers I can read"), ("error", "503"), ("timeout", "Check that its Prometheus")):
            nodes.read.clear()
            self.fake.modes[N2] = mode
            n = nodes.gather(CONF)[1]
            self.assertFalse(n["up"], mode)
            self.assertIn(words, n["msg"], mode)

    def test_gpu_numbers_missing(self):
        for q in (nodes.GPU_LOAD, nodes.GPU_USED, nodes.GPU_TOTAL, nodes.GPU_TEMP):
            del self.fake.values[N1][q]
        n = nodes.gather(CONF)[0]
        self.assertTrue(n["up"])
        self.assertIsNone(n["gpu"])
        self.assertIn("GPU numbers aren't coming through", nodes.block(n))

    def test_no_machine_numbers_is_a_warning_not_a_crash(self):
        self.fake.values[N2] = {}
        n = nodes.gather(CONF)[1]
        self.assertTrue(n["up"])
        self.assertEqual(n["status"], "warn")
        self.assertIn("node-exporter", n["msg"])

    def test_service_check_down_and_a_refusing_page_is_still_up(self):
        self.fake.pages["http://192.168.1.20/admin"] = 403
        self.assertTrue(nodes.gather(CONF)[0]["checks"][0]["up"])
        nodes.read.clear()
        del self.fake.pages["http://192.168.1.20/admin"]
        n = nodes.gather(CONF)[0]
        self.assertFalse(n["checks"][0]["up"])
        self.assertEqual(n["status"], "bad")

    def test_answers_are_cached(self):
        nodes.gather(CONF)
        before = len(self.fake.calls)
        nodes.gather(CONF)
        self.assertEqual(len(self.fake.calls), before)

    def test_limits_follow_config_and_ignore_nonsense(self):
        self.assertEqual(nodes.limits({}), nodes.LIMITS)
        self.assertEqual(nodes.limits({"node_limits": {"temp": 70, "disk": "lots", "zzz": 1}})["temp"], 70)
        self.assertEqual(nodes.limits({"node_limits": {"disk": "lots"}})["disk"], 90)

    def test_hot_node_is_a_warning(self):
        self.fake.values[N2][nodes.TEMP] = 88
        n = nodes.gather(CONF)[1]
        self.assertEqual(n["status"], "warn")
        self.assertIn("temperature 88°C", [p["text"] for p in n["problems"]])

    def test_containers_not_running(self):
        self.fake.gone[N2] = ["sonarr", "radarr"]
        n = nodes.gather(CONF)[1]
        self.assertEqual(n["down"], ["radarr", "sonarr"])

    def test_json_route(self):
        app = self.make_app()
        r = app.router.find("GET", "/nodes")(None)
        self.assertEqual([n["name"] for n in r["nodes"]], ["Node 1", "Node 2"])
        self.assertNotIn("192.168.1.20", str(r))
        self.assertNotIn("10.0.0.5", str(r))
        self.assertEqual(r["nodes"][0]["problems"], [])


class Telegram(Base):
    def test_how_is_a_node(self):
        r = self.ask("how is node 1")
        self.assertIn("<b>Node 1</b> is up", r)
        self.assertIn("🧠 Working at 12% • memory 41% used", r)
        self.assertIn("🌡 54°C (cool) • 💾 storage 63% used", r)
        self.assertIn("📦 14 containers running • on for 6.2 days", r)
        self.assertIn("🎮 GPU 3% busy • 412 of 4096 MB memory • 41°C (cool)", r)
        self.assertNotIn("Node 2", r)
        r2 = self.ask("how is node 2")
        self.assertIn("<b>Node 2</b> is up", r2)
        self.assertNotIn("GPU", r2)

    def test_both_nodes(self):
        for s in ("how are my nodes", "status of both", "/nodes", "/nodes@my_bot", "how is everything on both machines"):
            r = self.ask(s)
            self.assertIsNotNone(r, s)
            self.assertLess(r.index("Node 1"), r.index("Node 2"), s)

    def test_gpu(self):
        for s in ("gpu", "how hot is the gpu", "GPU?"):
            r = self.ask(s)
            self.assertIn("🎮 GPU 3% busy", r, s)
            self.assertNotIn("Node 2", r, s)
        self.assertIn("Node 2 has no GPU", self.ask("gpu on node 2"))
        self.assertIn("No node is set up with a GPU", self.ask("gpu", {"nodes": [{"name": "Node 2", "prom": N2}]}))

    def test_single_questions(self):
        self.assertIn("storage 63% used, 370 GB free of 1000 GB", self.ask("disk space on node 2"))
        self.assertIn("🧠 Node 1: memory 41% used", self.ask("memory on node 1"))
        t = self.ask("temperatures")
        self.assertIn("Node 1: 54°C", t)
        self.assertIn("Node 2: 54°C", t)

    def test_containers(self):
        self.fake.gone[N2] = ["sonarr"]
        r = self.ask("which containers are down")
        self.assertIn("📦 Node 1: all 14 containers running", r)
        self.assertIn("📦 Node 2: 1 not running\n• sonarr", r)
        self.assertIn("all 14", self.ask("containers on node 1"))

    def test_container_numbers_missing(self):
        self.fake.values[N2].pop(nodes.RUNNING)
        self.assertIn("no container numbers", self.ask("containers on node 2"))

    def test_unreachable_node_says_what_to_check(self):
        self.fake.modes[N2] = "down"
        r = self.ask("how is node 2")
        self.assertIn("<b>Node 2</b> is unreachable", r)
        self.assertIn("Check that its Prometheus is running and that Tailscale is up", r)
        self.assertNotIn("Traceback", r)
        both = self.ask("status of both")
        self.assertIn("is up", both)
        self.assertIn("is unreachable", both)

    def test_unknown_node_and_no_nodes(self):
        self.assertIn("I only know 2 nodes: Node 1, Node 2", self.ask("how is node 3"))
        self.assertIn("No nodes are set up yet", self.ask("/nodes", {}))
        self.assertIn("No nodes are set up yet", self.ask("how is node 1", {"nodes": []}))

    def test_node_names_work_too(self):
        data = {"nodes": [{"name": "Attic", "prom": N1, "primary": True}, {"name": "Cellar", "prom": N2}]}
        self.assertIn("<b>Cellar</b> is up", self.ask("how is cellar", data))

    def test_other_features_keep_their_sentences(self):
        for s in ("restart n8n on node 1", "remind me to check node 2 at 6pm", "show the logs on node 1", "note: node 2 fan is loud"):
            self.assertIsNone(self.ask(s), s)

    def test_server_phrases_are_never_claimed(self):
        """Every phrase the router tests already know, and every Server and Ollama example, must reach the old path unchanged."""
        import intents
        texts = [p for p, _ in PHRASES] + [x for k in ("server", "restart_app", "logs", "free_ai_memory", "reboot_server") for x in intents.EXAMPLES[k]]
        for extra in ("server status", "disk space", "how hot is the cpu", "is anything down", "stats", "how much ram is used", "how is the homelab"):
            texts.append(extra)
        taken = [x for x in texts if self.ask(x) is not None]
        self.assertEqual(taken, [])

    def test_replies_use_12_hour_times_and_no_em_dashes(self):
        for f in ("__init__.py", os.path.join("web", "view.js"), "module.yaml"):
            self.assertNotIn("—", open(os.path.join(harness.ROOT, "modules", "nodes", f), encoding="utf-8").read(), f)


class Routing(Base):
    def test_route_tries_the_module_before_the_classifier(self):
        bot = harness.load_bot()
        from core import intent as ci
        from core.telegram import handlers as h
        app = self.make_app()
        saved = {k: getattr(h, k) for k in ("send", "dispatch", "fresh_data")} | {"classify": ci.classify}
        seen = []
        h.send, h.dispatch, h.fresh_data = (lambda *a, **k: None), (lambda *a: seen.append("dispatch")), (lambda: None)
        ci.classify = lambda text: (seen.append("classify") or ("server", "stub"))
        hook = app.text_hooks[0]
        bot.TEXT_HOOKS.append(hook)
        try:
            bot.route("how is node 2")
            self.assertEqual(seen, [])
            self.assertIn("Node 2", "\n".join(self.tg.sent))
            bot.route("server status")
            self.assertEqual(seen, ["classify", "dispatch"])  # the old path, untouched
        finally:
            bot.TEXT_HOOKS.remove(hook)
            for k, v in saved.items():
                setattr(ci if k == "classify" else h, k, v)


class Alerts(Base):
    def run_check(self, app):
        nodes.read.clear()
        nodes.check_alerts(app)

    def test_down_alert_fires_once_and_recovers_once(self):
        app = self.make_app()
        self.fake.modes[N2] = "down"
        self.run_check(app)
        self.assertEqual(self.tg.sent, [])  # one miss is not enough (a Prometheus restart)
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("Node 2 is unreachable", self.tg.sent[0])
        self.run_check(app)
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 1)
        self.fake.modes[N2] = "up"
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 2)
        self.assertIn("Node 2 is back", self.tg.sent[1])
        self.assertRegex(self.tg.sent[1], r"Since \d{1,2}(:\d\d)? (AM|PM)")
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 2)

    def test_a_blip_that_clears_before_the_limit_says_nothing(self):
        app = self.make_app()
        self.fake.modes[N2] = "down"
        self.run_check(app)
        self.fake.modes[N2] = "up"
        self.run_check(app)
        self.fake.modes[N2] = "down"
        self.run_check(app)
        self.assertEqual(self.tg.sent, [])

    def test_temperature_disk_memory_and_service_each_alert_once(self):
        app = self.make_app({**CONF, "node_limits": {"fails": 1}})
        self.fake.values[N1][nodes.TEMP] = 91
        self.fake.values[N1][nodes.DISK] = 95
        self.fake.values[N1][nodes.MEM] = 97
        del self.fake.pages["http://192.168.1.20/admin"]
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 4)
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 4)
        self.fake.values[N1][nodes.TEMP] = 50
        self.run_check(app)
        self.assertEqual(len(self.tg.sent), 5)
        self.assertIn("cooled down", self.tg.sent[4])

    def test_a_down_node_keeps_its_other_alerts_open(self):
        app = self.make_app({**CONF, "node_limits": {"fails": 1}})
        self.fake.values[N2][nodes.DISK] = 95
        self.run_check(app)
        self.fake.modes[N2] = "down"
        self.run_check(app)
        sent = len(self.tg.sent)
        self.assertEqual(sent, 2)  # the disk, then the node going down; no false "disk is fine" while it cannot be read

    def test_state_survives_a_restart(self):
        app = self.make_app({**CONF, "node_limits": {"fails": 1}})
        self.fake.modes[N2] = "down"
        self.run_check(app)
        again = App(config.Config(CONF), http_api.Router(), scheduler.Scheduler("UTC"), capabilities.Capabilities(), app.store, None, self.tg)
        self.fake.modes[N2] = "up"
        nodes.read.clear()
        nodes.check_alerts(again)
        self.assertIn("Node 2 is back", self.tg.sent[-1])


class ConfigCheck(unittest.TestCase):
    def test_a_good_nodes_section(self):
        self.assertEqual(config.check(CONF), [])
        self.assertEqual(config.check({"node_limits": {"temp": 80, "disk": 85, "memory": 90, "fails": 3}}), [])

    def test_typos_and_wrong_types_are_explained(self):
        bad = config.check({"nodes": [{"name": "A", "adress": "x"}]})
        self.assertTrue(any("nodes[1].adress is not a setting I know" in p for p in bad), bad)
        self.assertTrue(any("should be a list" in p for p in config.check({"nodes": {"name": "A"}})))
        self.assertTrue(any("whole number" in p for p in config.check({"node_limits": {"temp": "hot"}})))
        self.assertTrue(any("true or false" in p for p in config.check({"nodes": [{"name": "A", "prom": "x", "gpu": "yes please"}]})))
        self.assertTrue(any("checks[1].url" in p or "url" in p for p in config.check({"nodes": [{"name": "A", "prom": "x", "checks": [{"name": "B", "url": 5}]}]})))

    def test_module_is_off_unless_switched_on(self):
        from core import registry
        mods = registry.discover(os.path.join(harness.ROOT, "modules"), log=lambda m: None)
        self.assertFalse(mods["nodes"].default)
        order, _ = registry.plan(mods, {})
        self.assertNotIn("nodes", order)
        order, _ = registry.plan(mods, {"nodes": True})
        self.assertIn("nodes", order)


if __name__ == "__main__":
    unittest.main()
