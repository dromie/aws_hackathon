#!/usr/bin/env python3.11
"""
Tile szerver:   http://localhost:8765/{z}/{x}/{y}.png
Szimuláció API: http://localhost:8765/api/groups
Statikus:       http://localhost:8765/
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

# --- Szimuláció konstansok ---
RALLY      = (47.5149, 18.5763)
BOUNDS_LAT = (47.503, 47.525)
BOUNDS_LNG = (18.563, 18.589)
WANDER_SEC = 6
NUM_GROUPS = 12
TICK_SEC   = 0.033   # ~30 fps


def _rand_latlng():
    return (
        BOUNDS_LAT[0] + random.random() * (BOUNDS_LAT[1] - BOUNDS_LAT[0]),
        BOUNDS_LNG[0] + random.random() * (BOUNDS_LNG[1] - BOUNDS_LNG[0]),
    )


class Group:
    def __init__(self):
        self.lat, self.lng = _rand_latlng()
        self.count = random.randint(3, 5)
        self.alive = True
        self.vlat = (random.random() - 0.5) * 0.00012
        self.vlng = (random.random() - 0.5) * 0.00016

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
        }


# --- Szimuláció szál ---
class Simulation:
    def __init__(self):
        self.groups = [Group() for _ in range(NUM_GROUPS)]
        self.phase = "wander"
        self.start = time.time()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            t0 = time.time()
            with self._lock:
                self._step()
            elapsed = time.time() - t0
            time.sleep(max(0, TICK_SEC - elapsed))

    def _step(self):
        if self.phase == "wander" and time.time() - self.start > WANDER_SEC:
            self.phase = "rally"

        alive = [g for g in self.groups if g.alive]
        for g in alive:
            g.wander() if self.phase == "wander" else g.rally()

        # Összeolvadás
        alive = [g for g in self.groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive:
                    continue
                # lat/lng távolságot pixelre becsüljük (1 fok lat ~ 111km, 1 fok lng ~ 74km ezen a szélességen)
                dy = (a.lat - b.lat) * 111000
                dx = (a.lng - b.lng) * 74000
                if math.hypot(dx, dy) < (a.radius + b.radius) * 0.00015 * 74000:
                    a.count += b.count
                    b.alive = False

    def snapshot(self):
        with self._lock:
            alive = [g for g in self.groups if g.alive]
            return {
                "phase":  self.phase,
                "total":  sum(g.count for g in alive),
                "groups": [g.to_dict() for g in alive],
            }


sim = Simulation()

# --- HTTP Handler ---
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

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    with http.server.HTTPServer(('', PORT), Handler) as srv:
        print(f'Szerver fut: http://localhost:{PORT}/')
        print(f'Nyisd meg:  http://localhost:{PORT}/crowd_map.html')
        print(f'API:        http://localhost:{PORT}/api/groups')
        print('Leállítás: Ctrl+C')
        srv.serve_forever()
