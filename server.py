#!/usr/bin/env python3.11
"""
Tile server:      http://localhost:8765/{z}/{x}/{y}.png
Simulation API:   http://localhost:8765/api/groups
Control API:      http://localhost:8765/api/control  (POST {action: start|stop|step|rewind|reset})
Static files:     http://localhost:8765/
"""
import http.server
import os
import json
import threading
import time
import math
import random
import networkx as nx

PORT = 8765
BASE = os.path.dirname(os.path.abspath(__file__))

# --- Simulation constants ---
RALLY      = (47.4871, 19.0707)  # Nokia Skypark
WANDER_SEC = 6
NUM_GROUPS = 12
TICK_SEC   = 0.033  # ~30 fps

# At zoom 15, 1 pixel ~ 3.2 metres
PX_TO_LAT = 0.000029
PX_TO_LNG = 0.000043


# --- Build road graph ---
def _build_graph():
    path = os.path.join(BASE, 'road_network.json')
    with open(path) as f:
        data = json.load(f)
    G = nx.Graph()
    for lat, lng, nid in data['nodes']:
        G.add_node(nid, lat=lat, lng=lng)
    for u, v in data['edges']:
        if u in G and v in G:
            dlat = G.nodes[u]['lat'] - G.nodes[v]['lat']
            dlng = G.nodes[u]['lng'] - G.nodes[v]['lng']
            G.add_edge(u, v, weight=math.hypot(dlat * 111000, dlng * 74000))
    return G


G = _build_graph()
_node_ids = list(G.nodes())
_node_coords = [(G.nodes[n]['lat'], G.nodes[n]['lng'], n) for n in _node_ids]


def _nearest_node(lat, lng):
    """Return the node id closest to the given lat/lng."""
    best, best_d = None, float('inf')
    for nlat, nlng, nid in _node_coords:
        d = math.hypot((nlat - lat) * 111000, (nlng - lng) * 74000)
        if d < best_d:
            best_d, best = d, nid
    return best


# Pre-compute rally node once
_RALLY_NODE = _nearest_node(*RALLY)


class Group:
    def __init__(self, node_id=None, count=None, vlat=None, vlng=None):
        self.node    = node_id or random.choice(_node_ids)
        self.count   = count if count is not None else random.randint(3, 5)
        self.alive   = True
        # Smooth visual position interpolates between nodes
        n = G.nodes[self.node]
        self.lat, self.lng = n['lat'], n['lng']
        self._target_node = None
        self._pick_next_node(wander=True)

    def _pick_next_node(self, wander=True):
        neighbors = [n for n in G.neighbors(self.node) if n != self.node]
        if not neighbors:
            return
        if wander:
            self._target_node = random.choice(neighbors)
        else:
            def dist_to_rally(nid):
                n = G.nodes[nid]
                r = G.nodes[_RALLY_NODE]
                return math.hypot((n['lat'] - r['lat']) * 111000,
                                  (n['lng'] - r['lng']) * 74000)
            self._target_node = min(neighbors, key=dist_to_rally)

    @property
    def radius(self):
        return max(10, 8 + self.count * 1.6)

    def step(self, wander):
        if self._target_node is None or self._target_node == self.node:
            self._pick_next_node(wander)
            return

        tn = G.nodes[self._target_node]
        tlat, tlng = tn['lat'], tn['lng']
        dlat, dlng = tlat - self.lat, tlng - self.lng
        dist_px = math.hypot(dlat / PX_TO_LAT, dlng / PX_TO_LNG)

        speed_px = 1.5 + self.count * 0.04
        if dist_px <= speed_px:
            # Snap to target node and immediately pick the next one
            self.lat, self.lng = tlat, tlng
            self.node = self._target_node
            self._pick_next_node(wander)
        else:
            self.lat += (dlat / dist_px) * speed_px * PX_TO_LAT
            self.lng += (dlng / dist_px) * speed_px * PX_TO_LNG

    def to_dict(self):
        return {
            "lat":    round(self.lat, 6),
            "lng":    round(self.lng, 6),
            "count":  self.count,
            "radius": round(self.radius, 1),
            "node":   self.node,
        }

    @staticmethod
    def from_dict(d):
        g = Group(node_id=d['node'], count=d['count'])
        g.lat, g.lng = d['lat'], d['lng']
        return g


class Simulation:
    def __init__(self):
        self._lock       = threading.Lock()
        self._running    = False
        self._tick_count = 0
        self._history    = []
        self._init_groups()
        threading.Thread(target=self._run, daemon=True).start()

    def _init_groups(self):
        self.groups      = [Group() for _ in range(NUM_GROUPS)]
        self.phase       = "wander"
        self._tick_count = 0
        self._history.clear()
        self._save_history()

    def _run(self):
        while True:
            t0 = time.time()
            with self._lock:
                if self._running:
                    self._step()
            time.sleep(max(0, TICK_SEC - (time.time() - t0)))

    def _step(self):
        if self.phase == "wander" and self._tick_count >= int(WANDER_SEC / TICK_SEC):
            self.phase = "rally"

        wander = self.phase == "wander"
        alive  = [g for g in self.groups if g.alive]
        for g in alive:
            g.step(wander)

        # Merge groups whose circles overlap in pixel space
        alive = [g for g in self.groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive:
                    continue
                dpx = (a.lng - b.lng) / PX_TO_LNG
                dpy = (a.lat - b.lat) / PX_TO_LAT
                if math.hypot(dpx, dpy) < a.radius + b.radius:
                    a.count += b.count
                    b.alive  = False

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
        self.groups      = [Group.from_dict(d) for d in snapshot["groups"]]

    def start(self):
        with self._lock: self._running = True

    def stop(self):
        with self._lock: self._running = False

    def step(self):
        with self._lock: self._step()

    def rewind(self):
        with self._lock:
            self._running = False
            if len(self._history) > 1:
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


print("Building road graph...")
sim = Simulation()
print("Ready.")


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
            self.send_header('Access-Control-Allow-Origin', '*'  )
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
