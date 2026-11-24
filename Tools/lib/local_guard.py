"""local_guard.py - the server on 127.0.0.1 answers the app's own page and nothing else.

Any web page open in the person's browser can send a request to http://127.0.0.1:8765. Two ways in, and what stops each:
  * a page on another site posting to it (a "simple" cross-origin POST needs no permission: the body is read as JSON whatever its Content-Type): a POST with an
    Origin that is not the app's own, or marked cross-site by the browser, is refused;
  * a page on a name that has been pointed at 127.0.0.1 (DNS rebinding) reading the answers: a request whose Host is not the app's own address is refused.
A GET that a page makes in the background (an image, a script) is refused when the browser marks it cross-site; a link the person follows (a navigation, such as
the return from SolarEdge's sign-in) is not. A client that sends none of these headers (curl, the app's own warm-up) is let through: it is another program on the
same machine, which could read the files anyway.

    reason = refused(headers, method, port)      None when the request is allowed, else a short reason
"""
HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _own(host, port):
    """True for 127.0.0.1:port, localhost:port, [::1]:port (the port is the server's own)."""
    host = (host or "").strip().lower()
    for h in HOSTS:
        if host == h and port in (80,):
            return True
        if host == "%s:%d" % (h, port):
            return True
    return False


def refused(headers, method, port):
    if not _own(headers.get("Host"), port):
        return "Host %s is not this app's address" % (headers.get("Host") or "(none)")
    site = (headers.get("Sec-Fetch-Site") or "").lower()
    mode = (headers.get("Sec-Fetch-Mode") or "").lower()
    if method == "POST":
        origin = headers.get("Origin")
        if origin and origin != "null" and not _own(origin.split("://", 1)[-1], port):
            return "Origin %s is not this app" % origin
        if origin == "null" or site == "cross-site":
            return "a cross-site POST"
    elif site == "cross-site" and mode != "navigate":
        return "a cross-site request"
    return None
