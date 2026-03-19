#!/usr/bin/env python3.11
"""
Tile server:      http://localhost:8765/{z}/{x}/{y}.png
Simulation API:   http://localhost:8765/api/groups
Control API:      http://localhost:8765/api/control  (POST {action: start|stop|step|rewind})
Static files:     http://localhost:8765/
"""
import http.server
import os
import json
import threading
import time
import math
import random

PORT = 8765
BASE = os.path.dirname(os.path.abspath(__file__))

# --- Simulation constants ---
RALLY      = (47.4871, 19.0707)  # Nokia Skypark
BOUNDS_LAT = (47.479, 47.496)
BOUNDS_LNG = (19.058, 19.080)
WANDER_SEC = 6
NUM_GROUPS = 12
TICK_SEC   = 0.033  # ~30 fps


def _rand_latlng():
    return (
        BOUNDS_LAT[0] + random.random() * (BOUNDS_LAT[1] - BOUNDS_LAT[0]),
        BOUNDS_LNG[0] + random.random() * (BOUNDS_LNG[1] - BOUNDS_LNG[0]),
    )


class Group:
    def __init__(self, lat=None, lng=None, count=None, vlat=None, vlng=None):
        self.lat   = lat   if lat   is not None else _rand_latlng()[0]
        self.lng   = lng   if lng   is not None else _rand_latlng()[1]
        self.count = count if count is not None else random.randint(3, 5)
        self.vlat  = vlat  if vlat  is not None else (random.random() - 0.5) * 0.00012
        self.vlng  = vlng  if vlng  is not None else (random.random() - 0.5) * 0.00016
        self.alive = True

    @property
    def radius(self):
        return max(10, 8 + self.count * 1.6)

    def wander(self):
        if random.random() < 0.03:
            self.vlat = (random.random() - 0.5) * 0.00012
            self.vlng = (random.random() - 0.5) * 0.00016
        self.lat += self.vlat
        self.lng += self.vlng
        if self.lat <= BOUNDS_LAT[0] or self.lat >= BOUNDS_LAT[1]: self.vlat *= -1
        if self.lng <= BOUNDS_LNG[0] or self.lng >= BOUNDS_LNG[1]: self.vlng *= -1
        self.lat = max(BOUNDS_LAT[0], min(BOUNDS_LAT[1], self.lat))
        self.lng = max(BOUNDS_LNG[0], min(BOUNDS_LNG[1], self.lng))

    def rally(self):
        dlat = RALLY[0] - self.lat
        dlng = RALLY[1] - self.lng
        dist = math.hypot(dlat, dlng)
        if dist > 0.00005:
            speed = 0.00008 + self.count * 0.000002
            self.lat += dlat / dist * speed
            self.lng += dlng / dist * speed

    def to_dict(self):
        return {
            "lat":    round(self.lat, 6),
            "lng":    round(self.lng, 6),
            "count":  self.count,
            "radius": round(self.radius, 1),
            "vlat":   self.vlat,
            "vlng":   self.vlng,
        }

    @staticmethod
    def from_dict(d):
        g = Group(d["lat"], d["lng"], d["count"], d["vlat"], d["vlng"])
        return g


class Simulation:
    def __init__(self):
        self._lock    = threading.Lock()
        self._running = False
        self._tick_count = 0
        self._history = []          # list of snapshots for rewind
        self._init_groups()
        threading.Thread(target=self._run, daemon=True).start()

    def _init_groups(self):
        self.groups = [Group() for _ in range(NUM_GROUPS)]
        self.phase  = "wander"
        self._tick_count = 0
        self._history.clear()
        self._save_history()

    def _run(self):
        while True:
            t0 = time.time()
            with self._lock:
                if self._running:
                    self._step()
            elapsed = time.time() - t0
            time.sleep(max(0, TICK_SEC - elapsed))

    def _step(self):
        # Advance phase after WANDER_SEC worth of ticks
        if self.phase == "wander" and self._tick_count >= int(WANDER_SEC / TICK_SEC):
            self.phase = "rally"

        alive = [g for g in self.groups if g.alive]
        for g in alive:
            g.wander() if self.phase == "wander" else g.rally()

        # Merge groups that overlap (distance in metres approximation)
        alive = [g for g in self.groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive:
                    continue
                dy = (a.lat - b.lat) * 111000
                dx = (a.lng - b.lng) * 74000  # approx at this latitude
                if math.hypot(dx, dy) < (a.radius + b.radius) * 0.00015 * 74000:
                    a.count += b.count
                    b.alive = False

        self._tick_count += 1
        self._save_history()

    def _save_history(self):
        # Keep at most 10 seconds of history (~300 frames)
        self._history.append({
            "phase":  self.phase,
            "tick":   self._tick_count,
            "groups": [g.to_dict() for g in self.groups if g.alive],
        })
        if len(self._history) > 300:
            self._history.pop(0)

    def _restore(self, snapshot):
        self.phase       = snapshot["phase"]
        self._tick_count = snapshot["tick"]
        self.groups = [Group.from_dict(d) for d in snapshot["groups"]]

    # --- Public controls ---
    def start(self):
        with self._lock:
            self._running = True

    def stop(self):
        with self._lock:
            self._running = False

    def step(self):
        with self._lock:
            self._step()

    def rewind(self):
        with self._lock:
            self._running = False
            if len(self._history) > 1:
                # Step back 30 frames (~1 second)
                target = max(0, len(self._history) - 31)
                self._history = self._history[:target + 1]
                self._restore(self._history[-1])

    def reset(self):
        with self._lock:
            self._running = False
            self._init_groups()

    def snapshot(self):
        with self._lock:
            alive = [g for g in self.groups if g.alive]
            return {
                "phase":   self.phase,
                "running": self._running,
                "tick":    self._tick_count,
                "total":   sum(g.count for g in alive),
                "groups":  [g.to_dict() for g in alive],
            }


sim = Simulation()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def do_GET(self):
        if self.path == '/api/groups':
            body = json.dumps(sim.snapshot(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
            return

        # Serve map tiles from local cache
        parts = self.path.lstrip('/').split('/')
        if len(parts) == 3 and parts[2].endswith('.png'):
            tile_path = os.path.join(BASE, 'tiles', *parts)
            if os.path.exists(tile_path):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(tile_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            self.send_error(404)
            return

        super().do_GET()

    def do_POST(self):
        if self.path == '/api/control':
            length = int(self.headers.get('Content-Length', 0))
            data   = json.loads(self.rfile.read(length))
            action = data.get('action')
            if   action == 'start':  sim.start()
            elif action == 'stop':   sim.stop()
            elif action == 'step':   sim.step()
            elif action == 'rewind': sim.rewind()
            elif action == 'reset':  sim.reset()
            body = json.dumps(sim.snapshot()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    with http.server.HTTPServer(('', PORT), Handler) as srv:
        print(f'Server running: http://localhost:{PORT}/')
        print(f'Open:           http://localhost:{PORT}/crowd_map.html')
        print(f'API:            http://localhost:{PORT}/api/groups')
        print('Stop: Ctrl+C')
        srv.serve_forever()
