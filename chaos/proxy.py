#!/usr/bin/env python3
"""Small TCP chaos proxy (stdlib only). Several named listeners, one control API. Used instead of toxiproxy so that every
fault is deterministic and scripted from the test.

Listener modes
  pass       forward both ways
  block      accept and close at once (connection refused-like; new connections fail)
  flaky      per connection, decided by a seeded RNG: reset before forwarding (request never reaches the server),
             or 'lost' = forward the request, and when the first response byte comes back from the server, cut the
             client without relaying it (the server HAS processed the request: lost response after commit)
Control (HTTP on CONTROL_PORT)
  GET  /status
  POST /<name>/drop                       close every open connection of listener <name> (what a Wi-Fi drop looks like)
  POST /<name>/mode?mode=block&seconds=3  set a mode, optionally auto-revert to pass after N seconds
  POST /<name>/flaky?reset=0.15&lost=0.15&seed=42
  POST /<name>/pass
Env: LISTENERS="browser:8081:web:80,sim:8082:api:8000"  CONTROL_PORT=8099
"""
import asyncio
import json
import os
import random
import time
from urllib.parse import parse_qs, urlparse


class Listener:
    def __init__(self, name, port, host, tport):
        self.name, self.port, self.host, self.tport = name, port, host, tport
        self.mode, self.p_reset, self.p_lost = "pass", 0.0, 0.0
        self.rng = random.Random(0)
        self.conns: set = set()
        self.stats = {"accepted": 0, "dropped": 0, "blocked": 0, "reset": 0, "lost": 0}
        self._revert = None

    async def handle(self, creader, cwriter):
        self.stats["accepted"] += 1
        mode = self.mode
        if mode == "block":
            self.stats["blocked"] += 1
            cwriter.close()
            return
        action = "pass"
        if mode == "flaky":
            r = self.rng.random()
            action = "reset" if r < self.p_reset else "lost" if r < self.p_reset + self.p_lost else "pass"
        if action == "reset":
            self.stats["reset"] += 1
            cwriter.close()
            return
        try:
            ureader, uwriter = await asyncio.open_connection(self.host, self.tport)
        except OSError:
            cwriter.close()
            return
        pair = (cwriter, uwriter)
        self.conns.add(pair)

        async def pipe(src, dst, lost_check=False):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    if lost_check:
                        self.stats["lost"] += 1
                        break  # first response byte seen: the server finished; do not relay it
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                for w in pair:
                    try:
                        w.close()
                    except Exception:  # noqa: BLE001
                        pass

        await asyncio.gather(pipe(creader, uwriter), pipe(ureader, cwriter, lost_check=(action == "lost")))
        self.conns.discard(pair)

    def drop_all(self):
        n = len(self.conns)
        for cw, uw in list(self.conns):
            for w in (cw, uw):
                try:
                    w.transport.abort()
                except Exception:  # noqa: BLE001
                    pass
        self.stats["dropped"] += n
        return n

    def set_mode(self, mode, seconds=None, loop=None):
        self.mode = mode
        if self._revert:
            self._revert.cancel()
        if seconds:
            self._revert = loop.call_later(float(seconds), lambda: setattr(self, "mode", "pass"))


async def control(listeners, reader, writer):
    try:
        line = (await reader.readline()).decode()
        while (await reader.readline()).strip():
            pass
        method, target, _ = line.split(" ", 2)
        u = urlparse(target)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        status, body = 200, {}
        if parts == ["status"]:
            body = {n: {"mode": l.mode, "open": len(l.conns), **l.stats} for n, l in listeners.items()}
        elif len(parts) == 2 and parts[0] in listeners and method == "POST":
            l, action = listeners[parts[0]], parts[1]
            if action == "drop":
                body = {"dropped": l.drop_all()}
            elif action == "pass":
                l.set_mode("pass"); body = {"mode": "pass"}
            elif action == "mode":
                l.set_mode(q.get("mode", "pass"), q.get("seconds"), asyncio.get_running_loop()); body = {"mode": l.mode}
            elif action == "flaky":
                l.p_reset, l.p_lost = float(q.get("reset", 0)), float(q.get("lost", 0))
                l.rng = random.Random(int(q.get("seed", 0)))
                l.set_mode("flaky"); body = {"mode": "flaky", "reset": l.p_reset, "lost": l.p_lost}
            else:
                status, body = 404, {"error": "unknown action"}
        else:
            status, body = 404, {"error": "not found"}
        payload = json.dumps(body).encode()
        writer.write(f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
        await writer.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        writer.close()


async def main():
    listeners = {}
    for spec in os.environ["LISTENERS"].split(","):
        name, port, host, tport = spec.split(":")
        listeners[name] = Listener(name, int(port), host, int(tport))
    servers = [await asyncio.start_server(l.handle, "0.0.0.0", l.port) for l in listeners.values()]
    servers.append(await asyncio.start_server(lambda r, w: control(listeners, r, w), "0.0.0.0", int(os.environ.get("CONTROL_PORT", 8099))))
    print("chaos proxy up:", {n: (l.port, f"{l.host}:{l.tport}") for n, l in listeners.items()}, flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
