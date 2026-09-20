#!/usr/bin/env python3
"""
Serve the dashboard on the local network so other machines can open it.

    python serve.py                     # anyone on this network, port 8765
    python serve.py --port 8080
    python serve.py --local-only        # this machine only
    python serve.py --sync-every 15     # also pull new logs every 15 minutes

Prints the URLs to hand to other people. Opening vehicle.html by double-clicking
works on this machine, but a second machine needs a server -- and a browser
opened on a file:// path will not load data.js on some configurations anyway.

Two things this deliberately does NOT do:

  * No authentication. Anyone who can reach this machine on this port can see
    the dashboard. That is usually fine for a site office and is not fine on an
    open network -- there is no login to add later, so if the audience matters,
    put it behind something that does authenticate.
  * It does not touch the firewall. Windows will ask for permission the first
    time Python binds a port; if you miss that prompt, the command to open the
    port is printed below and you run it yourself, as administrator.
"""

import argparse
import http.server
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MINE = os.path.dirname(HERE)
SYNC = os.path.join(MINE, "sync")


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files, with two changes that matter.

    No-cache: a browser that has cached data.js will keep showing yesterday's
    shift after a sync, and the symptom -- correct-looking numbers that are
    simply old -- is the hardest kind of wrong to notice. This cost real time
    during development.

    No directory listing: `/` serves the dashboard rather than an index of the
    folder.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_head(self):
        # Compare without the query string: a link someone pasted with `?v=2`
        # on the end should still land on the dashboard, not a 404.
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/", "/index.html"):
            self.path = "/vehicle.html"
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404, "No listing here")
        return None

    def log_message(self, fmt, *args):
        sys.stdout.write("  %s  %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


class Server(socketserver.ThreadingTCPServer):
    # A phone on the far side of the site office holding a socket open must not
    # stop everyone else loading the page.
    daemon_threads = True
    allow_reuse_address = True


def addresses():
    """Every IPv4 this machine answers on, best guess first.

    The outbound address is the one that actually routes, so it is the one to
    hand out. Others are usually virtual adapters (VirtualBox, WSL, Hyper-V)
    that no other machine can reach, so they are listed but marked.
    """
    primary = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()

    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except socket.gaierror:
        pass
    if primary and primary not in found:
        found.insert(0, primary)
    found.sort(key=lambda ip: (ip != primary, ip))
    return found, primary


def sync_loop(minutes):
    """Run the data sync on an interval, in the background.

    Optional, and separate from the scheduled task on purpose: this is for
    "one window open on my laptop while people look at it", the scheduled task
    is for leaving it running. Do not run both against the same folder.
    """
    script = os.path.join(SYNC, "blueice_sync.py")
    if not os.path.isfile(os.path.join(SYNC, "config.json")):
        print("  sync: no sync/config.json -- skipping (run sync/install.ps1 first)")
        return
    while True:
        try:
            r = subprocess.run([sys.executable, script, "update"], cwd=SYNC,
                               capture_output=True, text=True, timeout=1800)
            tail = [l for l in r.stdout.strip().splitlines() if l.strip()]
            print("  sync: " + (tail[-1] if tail else "ran") +
                  ("" if r.returncode == 0 else "  (exit %d)" % r.returncode))
        except Exception as exc:
            print("  sync: failed -- %s: %s" % (type(exc).__name__, exc))
        time.sleep(minutes * 60)


def main():
    ap = argparse.ArgumentParser(description="Serve the dashboard on the local network.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--local-only", action="store_true",
                    help="bind 127.0.0.1 -- this machine only, nobody else")
    ap.add_argument("--sync-every", type=int, metavar="MINUTES", default=0,
                    help="also pull new logs on this interval")
    args = ap.parse_args()

    # Line buffering, so `python serve.py > log` shows the URLs immediately
    # instead of holding them until the buffer fills or the process exits.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:                       # pragma: no cover
        pass

    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    for needed in ("vehicle.html", "data.js", "track.js"):
        if not os.path.isfile(os.path.join(HERE, needed)):
            print("Missing %s -- run build_data.py and build_track.py first." % needed)
            return 1

    try:
        httpd = Server((host, args.port), Handler)
    except OSError as exc:
        print("Cannot bind %s:%d -- %s" % (host, args.port, exc))
        print("Something else is probably using that port. Try --port %d." % (args.port + 1))
        return 1

    print("BlueICE fleet dashboard")
    if args.local_only:
        print("  http://127.0.0.1:%d/   (this machine only)" % args.port)
    else:
        ips, primary = addresses()
        print("  On this machine:  http://localhost:%d/" % args.port)
        if ips:
            print("  Share this:")
            for ip in ips:
                tag = "" if ip == primary else "   <- virtual adapter, probably not reachable"
                print("      http://%s:%d/%s" % (ip, args.port, tag))
        else:
            print("  No network address found -- this machine may be offline.")
        print()
        print("  No password. Anyone who can reach this machine on port %d can open it."
              % args.port)
        print("  If another machine cannot connect, Windows Firewall is the usual reason.")
        print("  In an ADMIN prompt:")
        print('      netsh advfirewall firewall add rule name="BlueICE dashboard %d" '
              "dir=in action=allow protocol=TCP localport=%d" % (args.port, args.port))
    print()
    print("  Viewers need internet access: the map library and the map tiles come")
    print("  from public CDNs. Without it the page still draws the data over blank")
    print("  ground and says so.")

    if args.sync_every:
        print("  Syncing every %d minute(s) in the background." % args.sync_every)
        threading.Thread(target=sync_loop, args=(args.sync_every,), daemon=True).start()

    print("\n  Ctrl-C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
