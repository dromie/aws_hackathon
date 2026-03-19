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
RALLY_POINTS = [
    (47.4860983, 19.0788411),  # Nokia Skypark
    (47.4713629, 19.0632207),  # Ericsson
]
WANDER_SEC = 6
NUM_GROUPS        = 24
DEFAULT_PEOPLE    = 28800   # 300x the original ~96
DEFAULT_GROUPS    = 720     # 30x the original 24
TICK_SEC   = 0.033  # ~30 fps

# Conversion factors (approximate at this latitude)
METERS_PER_LAT = 111000
METERS_PER_LNG = 74000
METERS_PER_PX  = 3.2  # at zoom 15


def _dist_m(lat1, lng1, lat2, lng2):
    return math.hypot((lat1 - lat2) * METERS_PER_LAT,
                      (lng1 - lng2) * METERS_PER_LNG)


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


# Pre-compute rally nodes once
_RALLY_NODES = [_nearest_node(*rp) for rp in RALLY_POINTS]


# --- Venue cells ---
# Each cell has a name, position, capacity, and dynamically computed occupancy
_VENUES = [
    {"id":  0, "name": "Kopaszi gát déli csúcs",  "lat": 47.4644356, "lng": 19.052092,  "capacity": 200},
    {"id":  1, "name": "Kopaszi gát északi rész", "lat": 47.46944,   "lng": 19.0566328, "capacity": 150},
    {"id":  2, "name": "Ipar utca sarok",          "lat": 47.483705,  "lng": 19.0474799, "capacity": 120},
    {"id":  3, "name": "Bercsényi utca park",      "lat": 47.4823197, "lng": 19.0550799, "capacity": 150},
    {"id":  4, "name": "Lágymányosi híd lába",    "lat": 47.4798053, "lng": 19.0502945, "capacity": 200},
    {"id":  5, "name": "Boráros tér",             "lat": 47.4822406, "lng": 19.0617938, "capacity": 350},
    {"id":  6, "name": "Lujza utca tér",           "lat": 47.4870619, "lng": 19.064957,  "capacity": 200},
    {"id":  7, "name": "Corvin Plaza",             "lat": 47.4847519, "lng": 19.066757,  "capacity": 800},
    {"id":  8, "name": "Teleki László tér",        "lat": 47.4884021, "lng": 19.0703932, "capacity": 300},
    {"id":  9, "name": "Mátyás tér",               "lat": 47.4897285, "lng": 19.0730806, "capacity": 250},
    {"id": 10, "name": "Illatos út sarok",         "lat": 47.4832,    "lng": 19.0850,    "capacity": 600},
    {"id": 11, "name": "Orczy tér",               "lat": 47.4893472, "lng": 19.0821294, "capacity": 400},
    {"id": 12, "name": "Dandár utca",             "lat": 47.4760,    "lng": 19.0680,    "capacity": 300},
    {"id": 13, "name": "Gubacsi út sarok",         "lat": 47.4657381, "lng": 19.0817743, "capacity": 120},
    {"id": 14, "name": "Sorokssári út park",       "lat": 47.472192,  "lng": 19.0778036, "capacity": 180},
]
# Capture radius in metres: groups within this distance count toward occupancy
_VENUE_RADIUS_M = 80

def compute_assignment(groups, venues_list):
    if not groups or not venues_list:
        return [], [0] * len(venues_list)
    total_people   = sum(g.count for g in groups)
    total_capacity = sum(v["capacity"] for v in venues_list)
    DG = nx.DiGraph()
    src, sink = "src", "sink"
    DG.add_node(src,  demand=-total_people)
    DG.add_node(sink, demand=min(total_people, total_capacity))
    if total_people > total_capacity:
        DG.add_node("overflow", demand=total_people - total_capacity)
    for i in range(len(groups)):      DG.add_node(("g", i), demand=0)
    for j in range(len(venues_list)): DG.add_node(("v", j), demand=0)
    for i, g in enumerate(groups):
        DG.add_edge(src, ("g", i), capacity=g.count, weight=0)
    for i, g in enumerate(groups):
        for j, v in enumerate(venues_list):
            d = int(round(_dist_m(g.lat, g.lng, v["lat"], v["lng"])))
            DG.add_edge(("g", i), ("v", j), capacity=g.count, weight=d)
    for j, v in enumerate(venues_list):
        DG.add_edge(("v", j), sink, capacity=v["capacity"], weight=0)
    if total_people > total_capacity:
        for i, g in enumerate(groups):
            DG.add_edge(("g", i), "overflow", capacity=g.count, weight=1000000)
    try:
        flow = nx.min_cost_flow(DG)
    except nx.NetworkXUnfeasible:
        return [], [0] * len(venues_list)
    assignments    = []
    venue_occupied = [0] * len(venues_list)
    for i in range(len(groups)):
        for j in range(len(venues_list)):
            f = flow.get(("g", i), {}).get(("v", j), 0)
            if f > 0:
                assignments.append({"groupIndex": i, "venueId": venues_list[j]["id"], "count": f})
                venue_occupied[j] += f
    return assignments, venue_occupied



    result = []
    for v in _VENUES:
        occupied = sum(
            g.count for g in groups
            if g.alive and _dist_m(g.lat, g.lng, v["lat"], v["lng"]) <= _VENUE_RADIUS_M
        )
        result.append({
            "id":       v["id"],
            "name":     v["name"],
            "lat":      v["lat"],
            "lng":      v["lng"],
            "capacity": v["capacity"],
            "occupied": occupied,
        })
    return result


class Group:
    def __init__(self, node_id=None, count=None, rally_node=None):
        self.node        = node_id if node_id is not None else random.choice(_node_ids)
        self.count       = count if count is not None else random.randint(3, 5)
        self.rally_node  = rally_node if rally_node is not None else (_RALLY_NODES[0] if random.random() < 0.65 else _RALLY_NODES[1])
        self.alive       = True
        n = G.nodes[self.node]
        self.lat, self.lng = n['lat'], n['lng']
        self._path        = []
        self._target_node = None
        self._pick_wander_target()

    def _pick_wander_target(self):
        neighbors = [n for n in G.neighbors(self.node) if n != self.node]
        self._target_node = random.choice(neighbors) if neighbors else self.node

    def _plan_rally_path(self):
        try:
            self._path = nx.shortest_path(G, self.node, self.rally_node, weight='weight')[1:]
        except nx.NetworkXNoPath:
            self._path = []
        self._target_node = self._path.pop(0) if self._path else self.node

    @property
    def radius(self):
        return max(10, 8 + self.count * 1.6)

    def step(self, wander):
        if wander:
            if self._target_node is None or self._target_node == self.node:
                self._pick_wander_target()
        else:
            # Plan or continue rally path
            if not self._path and self._target_node == self.node:
                self._plan_rally_path()

        if self._target_node is None or self._target_node == self.node:
            return

        tn = G.nodes[self._target_node]
        tlat, tlng = tn['lat'], tn['lng']
        dlat   = tlat - self.lat
        dlng   = tlng - self.lng
        dist_m = math.hypot(dlat * METERS_PER_LAT, dlng * METERS_PER_LNG)
        speed_m = (1.5 + self.count * 0.04) * METERS_PER_PX * 3

        if dist_m <= speed_m:
            self.lat, self.lng = tlat, tlng
            self.node = self._target_node
            if wander:
                self._pick_wander_target()
            else:
                self._target_node = self._path.pop(0) if self._path else self.node
        else:
            ratio = speed_m / dist_m
            self.lat += dlat * ratio
            self.lng += dlng * ratio

    def to_dict(self):
        return {
            "lat":        round(self.lat, 6),
            "lng":        round(self.lng, 6),
            "count":      self.count,
            "radius":     round(self.radius, 1),
            "node":       self.node,
            "rally_node": self.rally_node,
        }

    @staticmethod
    def from_dict(d):
        g = Group(node_id=d['node'], count=d['count'], rally_node=d['rally_node'])
        g.lat, g.lng = d['lat'], d['lng']
        return g


class Simulation:
    def __init__(self):
        self._lock          = threading.Lock()
        self._running       = False
        self._tick_count    = 0
        self._history       = []
        self._total_spawned = 0
        self._arrived       = {}
        self._num_groups    = DEFAULT_GROUPS
        self._num_people    = DEFAULT_PEOPLE
        self._init_groups()
        threading.Thread(target=self._run, daemon=True).start()

    def _init_groups(self):
        per_group = max(1, self._num_people // self._num_groups)
        remainder = self._num_people - per_group * self._num_groups
        self.groups = []
        for i in range(self._num_groups):
            c = per_group + (1 if i < remainder else 0)
            self.groups.append(Group(count=c))
        self.phase          = "wander"
        self._tick_count    = 0
        self._total_spawned = sum(g.count for g in self.groups)
        self._arrived       = {str(n): 0 for n in _RALLY_NODES}
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
            # Plan rally paths at the moment of phase switch
            if not wander and g._path == [] and g._target_node == g.node:
                g._plan_rally_path()
            g.step(wander)

        # Groups that reached their rally node: absorb into arrived count
        for g in alive:
            if not wander and g.node == g.rally_node:
                self._arrived[str(g.rally_node)] = self._arrived.get(str(g.rally_node), 0) + g.count
                g.alive = False

        # Merge groups whose circles overlap (radius converted to metres)
        alive = [g for g in self.groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive:
                    continue
                if _dist_m(a.lat, a.lng, b.lat, b.lng) < 15:
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

    def reset(self, num_people=None, num_groups=None):
        with self._lock:
            self._running = False
            if num_people is not None:
                self._num_people = max(1, int(num_people))
            if num_groups is not None:
                self._num_groups = max(1, int(num_groups))
            self._init_groups()

    def snapshot(self):
        with self._lock:
            alive = [g for g in self.groups if g.alive]
            assignments, venue_occupied = compute_assignment(alive, _VENUES)
            venues = []
            for idx, v in enumerate(_VENUES):
                venues.append({
                    "id": v["id"], "name": v["name"],
                    "lat": v["lat"], "lng": v["lng"],
                    "capacity": v["capacity"],
                    "occupied": venue_occupied[idx],
                })
            return {
                "phase":         self.phase,
                "running":       self._running,
                "tick":          self._tick_count,
                "total":         sum(g.count for g in alive),
                "total_spawned": self._total_spawned,
                "arrived":       dict(self._arrived),
                "groups":        [g.to_dict() for g in alive],
                "venues":        venues,
                "assignments":   assignments,
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

        if self.path == '/api/venues':
            with sim._lock:
                alive = [g for g in sim.groups if g.alive]
                body  = json.dumps(venues_snapshot(alive), ensure_ascii=False).encode()
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
            elif action == 'reset':  sim.reset(data.get('people'), data.get('groups'))
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
