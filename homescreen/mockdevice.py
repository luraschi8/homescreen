"""A screen that isn't there yet.

Impersonates a display client against a real server, so the whole device-facing
contract -- registration, capability declaration, assignment, scene delivery,
conditional fetches, poll cadence -- is exercisable from a laptop before any
hardware exists, and stays exercisable when hardware is on a shelf.

    python -m homescreen.mockdevice --server http://192.168.1.116:8080 \
        --hw aabb00112233 --kind epaper --once --out /tmp/screen.png

Pixel-push devices decode the framebuffer back to a PNG you can look at, which
is the only honest way to check what a panel would actually show. Data-push
devices print the components they were handed.

This is a TEST CLIENT, not a device simulator: it deliberately does no
rendering, no interpolation and no partial refresh. Its job is to prove the
server's half of the contract, and to fail loudly when the server breaks it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KINDS = {
    # name -> capabilities a real board of that class would declare
    "epaper": {"w": 800, "h": 480, "depth": 1, "layouts": "fill",
               "components": "text"},
    "round": {"w": 240, "h": 240, "depth": 16, "layouts": "fill",
              "components": "text,radar,rings,markers"},
}


class MockDevice:
    def __init__(self, server: str, hw: str, kind: str, fw: str = "mock-0.1"):
        self.server = server.rstrip("/")
        self.hw = hw
        self.caps = dict(KINDS[kind])
        self.kind = kind
        self.fw = fw
        self.etag: str | None = None
        self.errors = 0
        self.started = time.time()

    def _url(self, path: str) -> str:
        query = {**self.caps, "fw": self.fw, "errors": str(self.errors),
                 "uptime": str(int(time.time() - self.started)), "rssi": "-58"}
        return f"{self.server}{path}?{urllib.parse.urlencode(query)}"

    def _post(self, path: str, payload: dict):
        """Operator-side call. No capability query string -- this is not the
        device speaking."""
        req = urllib.request.Request(
            f"{self.server}{path}", method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except OSError as exc:
            raise SystemExit(f"cannot reach {self.server}: {exc}")

    def _get(self, path: str, *, conditional: bool = False):
        req = urllib.request.Request(self._url(path))
        if conditional and self.etag:
            req.add_header("If-None-Match", self.etag)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, dict(exc.headers), b""
            self.errors += 1
            return exc.code, dict(exc.headers), exc.read()
        except OSError as exc:
            self.errors += 1
            raise SystemExit(f"cannot reach {self.server}: {exc}")

    def approve(self) -> None:
        """Admit this device, the way a human would from the dashboard.

        A real device cannot do this -- that is the point of the gate. This is
        the mock wearing the operator's hat so one command exercises the whole
        contract.
        """
        status, _, body = self._post(f"/api/devices/{self.hw}/approval",
                                     {"approved": True})
        if status != 200:
            raise SystemExit(f"approve -> HTTP {status}: "
                             f"{body[:200].decode(errors='replace')}")

    def scene(self) -> dict:
        status, headers, body = self._get(f"/api/device/{self.hw}/scene")
        if status != 200:
            raise SystemExit(f"scene -> HTTP {status}: {body[:200].decode(errors='replace')}")
        out = json.loads(body)
        out["_poll_seconds"] = headers.get("X-Poll-Seconds")
        return out

    def frame(self) -> tuple[int, dict, bytes]:
        status, headers, body = self._get(f"/api/device/{self.hw}/frame",
                                          conditional=True)
        if status == 200:
            self.etag = headers.get("ETag")
            expected = self.caps["w"] * self.caps["h"] // 8
            if len(body) != expected:
                raise SystemExit(f"CONTRACT VIOLATION: expected {expected} bytes, "
                                 f"got {len(body)} -- a device would render garbage")
        return status, headers, body

    def to_png(self, packed: bytes, out_path: str) -> None:
        """Decode the wire format back to something a human can look at.

        1 = black on the wire, MSB leftmost. Getting this backwards is the
        classic bring-up bug, so decoding here is also a polarity check: if
        the image comes out inverted, the server is wrong, not this.
        """
        from PIL import Image
        w, h = self.caps["w"], self.caps["h"]
        img = Image.frombytes("1", (w, h), bytes(b ^ 0xFF for b in packed))
        img.save(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Impersonate a display client.")
    ap.add_argument("--server", default="http://127.0.0.1:8080")
    ap.add_argument("--hw", required=True, help="hardware id to register as")
    ap.add_argument("--kind", choices=sorted(KINDS), default="epaper")
    ap.add_argument("--fw", default="mock-0.1")
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    ap.add_argument("--out", help="write the decoded frame here (pixel push)")
    ap.add_argument("--cycles", type=int, default=0, help="0 = forever")
    ap.add_argument("--approve", action="store_true",
                    help="also act as the operator and admit this device, so a "
                         "run exercises assignment and scene delivery rather "
                         "than stopping at the pending screen")
    args = ap.parse_args(argv)

    dev = MockDevice(args.server, args.hw, args.kind, args.fw)
    print(f"device {args.hw} ({args.kind}) -> {dev.server}")
    if args.approve:
        # Registration has to happen first: approval names a record, and this
        # tool is the only thing that has created one.
        dev.scene()
        dev.approve()
    cycles = 1 if args.once else args.cycles

    n = 0
    while True:
        n += 1
        scene = dev.scene()
        poll = scene.get("_poll_seconds") or "5"
        line = (f"[{n}] scene={scene['scene']} assigned={scene['assigned']} "
                f"poll={poll}s")
        if scene.get("message"):
            line += f" | {scene['message']}"
        print(line)

        if args.kind == "round":
            for comp in scene.get("components", []):
                kind = comp.get("c")
                extra = (f" items={len(comp['items'])}" if isinstance(
                    comp.get("items"), list) else "")
                print(f"      component {kind}{extra}")
            if not scene.get("components"):
                print("      (no components -- this scene is pixel-push only)")
        else:
            status, headers, body = dev.frame()
            if status == 304:
                print("      frame unchanged (304) -- panel left alone")
            elif status == 200:
                ink = sum(bin(b).count("1") for b in body)
                px = dev.caps["w"] * dev.caps["h"]
                print(f"      frame {len(body):,}B scene={headers.get('X-Scene')} "
                      f"ink={100*ink/px:.2f}%")
                if args.out:
                    dev.to_png(body, args.out)
                    print(f"      decoded -> {args.out}")
            else:
                print(f"      frame -> HTTP {status}: "
                      f"{body[:160].decode(errors='replace')}")

        if cycles and n >= cycles:
            return 0
        time.sleep(float(poll))


if __name__ == "__main__":
    sys.exit(main())
