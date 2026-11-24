"""local_guard: only the app's own page may talk to the local server."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Tools", "lib"))
import local_guard as G  # noqa: E402

P = 8765


def r(method="GET", **h):
    h.setdefault("Host", "127.0.0.1:%d" % P)
    return G.refused(h, method, P)


class GuardTest(unittest.TestCase):
    def test_the_apps_own_page_and_plain_clients_are_let_through(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "LOCALHOST:8765"):
            self.assertIsNone(r(Host=host))
        self.assertIsNone(r("POST", Origin="http://127.0.0.1:8765", **{"Sec-Fetch-Site": "same-origin"}))
        self.assertIsNone(r("POST"))                                           # curl: no Origin, no Sec-Fetch headers
        self.assertIsNone(r(**{"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "no-cors"}))
        self.assertIsNone(r(**{"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}))

    def test_another_hosts_name_is_refused(self):
        self.assertTrue(r(Host="evil.example:8765"))                           # DNS rebinding
        self.assertTrue(r(Host="127.0.0.1:9999"))
        self.assertTrue(r(Host=""))

    def test_a_cross_site_post_is_refused(self):
        self.assertTrue(r("POST", Origin="https://evil.example"))
        self.assertTrue(r("POST", Origin="http://127.0.0.1:9999"))
        self.assertTrue(r("POST", Origin="null"))
        self.assertTrue(r("POST", **{"Sec-Fetch-Site": "cross-site"}))

    def test_a_background_get_from_another_site_is_refused_but_a_followed_link_is_not(self):
        self.assertTrue(r(**{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}))      # an <img> src
        self.assertIsNone(r(**{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}))   # back from SolarEdge's sign-in


if __name__ == "__main__":
    unittest.main()
