"""Tesla fetches: several browsers at once, the registry written one site at a time, and no waiting for a quiet network."""
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "Tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.join(TOOLS, "lib"))
sys.path.insert(0, os.path.join(TOOLS, "commands"))
from providers.chargers.chargers_tesla import TeslaChargerExplorer  # noqa: E402

URL = "https://www.tesla.com/en_AU/findus/location/supercharger/%s"


def station(i):
    return {"title": "Site %s, NSW" % i, "url": URL % i, "slug": str(i), "type": "supercharger"}


class FakePage:
    def close(self):
        pass


class FakeContext:
    def new_page(self):
        return FakePage()

    def close(self):
        pass


class FakeBrowser:
    def new_context(self, **kw):
        return FakeContext()

    def close(self):
        pass


class FakePW:
    def __enter__(self):
        return types.SimpleNamespace(webkit=types.SimpleNamespace(launch=lambda headless=True: FakeBrowser()))

    def __exit__(self, *a):
        return False


class Parallel(unittest.TestCase):
    def run_batch(self, n, workers):
        ex = TeslaChargerExplorer()
        ex.load_active_registries = lambda: ({}, {})
        seen, active, peak, in_registry, overlap = [], [0], [0], [0], []
        lock = threading.Lock()

        def payload(page, url, **kw):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.4)                                # long enough that the browsers, started 0.7 s apart, overlap
            with lock:
                active[0] -= 1
            return {"x": url}, None

        def parse(url, api, nxt, charger_type="supercharger", list_title=None):
            return list_title, {"title": list_title}

        def update(key, rec):
            in_registry[0] += 1
            if in_registry[0] > 1:
                overlap.append(key)                        # two threads in the registry at once
            time.sleep(0.01)
            seen.append(key)
            in_registry[0] -= 1
            ex.last_change_summary = ["changed " + key]
            return "VERIFIED"

        ex._scrape_page_payload = payload
        ex._parse_scraped_data = parse
        ex.update_registry = update
        ex.get_station_status = lambda *a, **k: "UP_TO_DATE"
        with mock.patch("playwright.sync_api.sync_playwright", lambda: FakePW()):
            res = ex.scrape_all_stations([station(i) for i in range(n)], pacing_delay=0, workers=workers)
        return res, seen, peak[0], overlap, ex

    def test_every_site_is_fetched_once_with_workers_at_once(self):
        res, seen, peak, overlap, ex = self.run_batch(12, 3)
        self.assertEqual(sorted(seen), sorted("Site %s, NSW" % i for i in range(12)))
        self.assertEqual(res["checked"], 12)
        self.assertEqual(res["changed"], 12)
        self.assertEqual(res["failed"], 0)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 3)
        self.assertEqual(overlap, [])

    def test_one_worker_fetches_one_site_at_a_time(self):
        res, seen, peak, overlap, ex = self.run_batch(5, 1)
        self.assertEqual(peak, 1)
        self.assertEqual(len(seen), 5)

    def test_a_failing_site_is_counted_and_the_rest_go_on(self):
        ex = TeslaChargerExplorer()
        ex.load_active_registries = lambda: ({}, {})
        ex.get_station_status = lambda *a, **k: "UP_TO_DATE"
        def payload(page, url, **kw):
            if url.endswith("/1"):
                raise RuntimeError("boom")
            return {}, None
        ex._scrape_page_payload = payload
        ex._parse_scraped_data = lambda url, a, n, charger_type="supercharger", list_title=None: (list_title, {"t": 1})
        ex.update_registry = lambda k, r: "VERIFIED"
        with mock.patch("playwright.sync_api.sync_playwright", lambda: FakePW()):
            res = ex.scrape_all_stations([station(i) for i in range(4)], pacing_delay=0, workers=2)
        self.assertEqual((res["checked"], res["failed"]), (4, 1))

    def test_a_known_site_that_came_back_without_charger_details_keeps_its_entry(self):
        ex = TeslaChargerExplorer()
        ex.load_active_registries = lambda: ({"Site 0, NSW": {"hardware": {"stalls": 4, "max_power_kw": 325}}}, {})
        ex.get_station_status = lambda *a, **k: "UP_TO_DATE"
        ex.get_station_record = lambda st, **k: ("Site 0, NSW", {"hardware": {"stalls": 4}}, None) if st["slug"] == "0" else (None, None, None)
        ex._scrape_page_payload = lambda page, url, **kw: ({"_page_text": "x"}, {"props": {}})      # cached figures only
        ex._parse_scraped_data = lambda url, a, n, charger_type="supercharger", list_title=None: (list_title, {"hardware": {"max_power_kw": 250}})
        written = []
        ex.update_registry = lambda k, r: written.append(k) or "VERIFIED"
        with mock.patch("playwright.sync_api.sync_playwright", lambda: FakePW()):
            res = ex.scrape_all_stations([station(0), station(1)], pacing_delay=0, workers=1)
        self.assertEqual(written, ["Site 1, NSW"])         # site 1 is new, so a cached-only record is all there is; site 0 keeps what it has
        self.assertEqual((res["checked"], res["failed"]), (2, 1))

    def test_the_http_status_belongs_to_the_thread_that_read_it(self):
        ex = TeslaChargerExplorer()
        out = {}

        def run(n, status):
            ex.last_http_status = status
            time.sleep(0.05)
            out[n] = ex.last_http_status
        ts = [threading.Thread(target=run, args=(1, 404)), threading.Thread(target=run, args=(2, 200))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(out, {1: 404, 2: 200})


class WaitForData(unittest.TestCase):
    class Page:
        def __init__(self, captured, appear):
            self.captured, self.appear, self.ms = captured, appear, 0

        def wait_for_timeout(self, ms):
            self.ms += ms
            time.sleep(ms / 1000.0)
            for k, at in list(self.appear.items()):
                if self.ms >= at:
                    self.captured[k] = {}
                    del self.appear[k]

    def test_returns_when_both_calls_have_arrived_without_waiting_longer(self):
        cap = {}
        page = self.Page(cap, {"get-charger-details": 200, "get-location-details": 300})
        t = time.time()
        TeslaChargerExplorer()._wait_for_details(page, cap, 35)
        self.assertLess(time.time() - t, 1.2)
        self.assertEqual(len(cap), 2)

    def test_the_charger_details_are_waited_for_even_when_the_location_call_comes_first(self):
        cap = {}
        page = self.Page(cap, {"get-location-details": 100, "get-charger-details": 1500})
        TeslaChargerExplorer()._wait_for_details(page, cap, 35, only_loc_s=5)
        self.assertEqual(len(cap), 2)                       # without them a record falls back to the page's cached, lower figures

    def test_a_page_with_the_charger_details_only_is_not_held_beyond_the_grace_time(self):
        cap = {}
        page = self.Page(cap, {"get-charger-details": 100})
        t = time.time()
        TeslaChargerExplorer()._wait_for_details(page, cap, 35, other_s=0.4)
        self.assertLess(time.time() - t, 1.5)

    def test_a_page_with_only_the_location_call_is_read_after_that_limit(self):
        cap = {}
        t = time.time()
        TeslaChargerExplorer()._wait_for_details(self.Page(cap, {"get-location-details": 100}), cap, 35, only_loc_s=0.4)
        self.assertLess(time.time() - t, 1.5)

    def test_a_page_without_any_call_is_read_as_it_is_after_the_limit(self):
        cap = {}
        t = time.time()
        TeslaChargerExplorer()._wait_for_details(self.Page(cap, {}), cap, 35, none_s=0.3)
        self.assertLess(time.time() - t, 1.5)


class WorkersSetting(unittest.TestCase):
    def test_flag_beats_the_shared_setting_which_defaults_to_three(self):
        import charger_find as CF
        self.assertEqual(CF.tesla_workers(types.SimpleNamespace(workers=5)), 5)
        self.assertEqual(CF.tesla_workers(types.SimpleNamespace(workers=0)), 1)
        with mock.patch("lib.app_config.get", lambda k, d=None: {"chargers.tesla_workers": 4}.get(k, d)):
            self.assertEqual(CF.tesla_workers(types.SimpleNamespace(workers=None)), 4)
        with mock.patch("lib.app_config.get", lambda k, d=None: d):
            self.assertEqual(CF.tesla_workers(types.SimpleNamespace(workers=None)), 2)


if __name__ == "__main__":
    unittest.main()
