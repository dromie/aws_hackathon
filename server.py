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
RALLY      = (47.4860983, 19.0788411)  # Nokia Skypark (nearest road node)
WANDER_SEC = 6
NUM_GROUPS = 24
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


# Pre-compute rally node once
_RALLY_NODE = _nearest_node(*RALLY)


# --- Venue cells ---
# Each cell has a name, position, capacity, and dynamically computed occupancy
_VENUES = [
    {"id": 0, "name": "Corvin Plaza",         "lat": 47.4847519, "lng": 19.066757,  "capacity": 800},
    {"id": 1, "name": "Corvin Mozi",           "lat": 47.4859913, "lng": 19.0669404, "capacity": 400},
    {"id": 2, "name": "Teleki László tér",     "lat": 47.4883954, "lng": 19.0703167, "capacity": 300},
    {"id": 3, "name": "Mátyás tér",            "lat": 47.4897285, "lng": 19.0730806, "capacity": 250},
    {"id": 4, "name": "Corvin sétány",         "lat": 47.4863303, "lng": 19.0687093, "capacity": 500},
    {"id": 5, "name": "Szigony utca park",     "lat": 47.4835954, "lng": 19.0703666, "capacity": 150},
    {"id": 6, "name": "Lujza utca tér",        "lat": 47.4870619, "lng": 19.064957,  "capacity": 200},
    {"id": 7, "name": "Illés utca sarok",      "lat": 47.4833486, "lng": 19.0730985, "capacity": 120},
    {"id": 8, "name": "Futó utca tér",         "lat": 47.4845698, "lng": 19.0758385, "capacity": 180},
    {"id": 9, "name": "Nokia Skypark előtér",  "lat": 47.4860566, "lng": 19.0792738, "capacity": 600},
]
# Capture radius in metres: groups within this distance count toward occupancy
_VENUE_RADIUS_M = 80


def compute_assignment(groups, venues_list):
    """
    Assign each person (from groups) to a tower via min-cost flow.
    Groups supply count_i units; towers have capacity_j. Cost = distance.
    Returns (assignments, venue_occupied): assignments is list of
    { groupIndex, venueId, count } for each (group, tower) with count > 0;
    venue_occupied is list of per-venue assigned counts (same order as venues_list).
    """
    if not groups or not venues_list:
        return [], [0] * len(venues_list)

    total_people = sum(g.count for g in groups)
    total_capacity = sum(v["capacity"] for v in venues_list)

    G = nx.DiGraph()
    src, sink, overflow = "src", "sink", "overflow"

    # Nodes: source, sink, overflow (if needed), one per group, one per venue
    G.add_node(src, demand=-total_people)
    G.add_node(sink, demand=min(total_people, total_capacity))
    if total_people > total_capacity:
        G.add_node(overflow, demand=total_people - total_capacity)

    for i in range(len(groups)):
        G.add_node(("g", i), demand=0)
    for j in range(len(venues_list)):
        G.add_node(("v", j), demand=0)

    # Edges: source -> groups; groups -> towers; towers -> sink; groups -> overflow
    for i, g in enumerate(groups):
        G.add_edge(src, ("g", i), capacity=g.count, weight=0)
    for i, g in enumerate(groups):
        for j, v in enumerate(venues_list):
            d = int(round(_dist_m(g.lat, g.lng, v["lat"], v["lng"])))
            G.add_edge(("g", i), ("v", j), capacity=g.count, weight=d)
    for j, v in enumerate(venues_list):
        G.add_edge(("v", j), sink, capacity=v["capacity"], weight=0)
    if total_people > total_capacity:
        for i, g in enumerate(groups):
            G.add_edge(("g", i), overflow, capacity=g.count, weight=1000000)

    try:
        flow = nx.min_cost_flow(G)
    except nx.NetworkXUnfeasible:
        return [], [0] * len(venues_list)

    assignments = []
    venue_occupied = [0] * len(venues_list)
    for i in range(len(groups)):
        for j in range(len(venues_list)):
            f = flow.get(("g", i), {}).get(("v", j), 0)
            if f > 0:
                assignments.append({"groupIndex": i, "venueId": venues_list[j]["id"], "count": f})
                venue_occupied[j] += f

    return assignments, venue_occupied


def venues_snapshot(groups, venue_occupied=None):
    """
    Build venue list with id, name, lat, lng, capacity, occupied.
    If venue_occupied is provided (from compute_assignment), use it; else
    compute occupancy by distance (groups within _VENUE_RADIUS_M).
    """
    result = []
    for idx, v in enumerate(_VENUES):
        if venue_occupied is not None and idx < len(venue_occupied):
            occupied = venue_occupied[idx]
        else:
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
    def __init__(self, node_id=None, count=None):
        self.node  = node_id if node_id is not None else random.choice(_node_ids)
        self.count = count if count is not None else random.randint(3, 5)
        self.alive = True
        n = G.nodes[self.node]
        self.lat, self.lng = n['lat'], n['lng']
        self._path = []       # list of node ids to follow (rally mode)
        self._target_node = None
        self._pick_wander_target()

    def _pick_wander_target(self):
        neighbors = [n for n in G.neighbors(self.node) if n != self.node]
        self._target_node = random.choice(neighbors) if neighbors else self.node

    def _plan_rally_path(self):
        try:
            self._path = nx.shortest_path(G, self.node, _RALLY_NODE, weight='weight')[1:]
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
            # Plan rally paths at the moment of phase switch
            if not wander and g._path == [] and g._target_node == g.node:
                g._plan_rally_path()
            g.step(wander)

        # Merge groups whose circles overlap (radius converted to metres)
        alive = [g for g in self.groups if g.alive]
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                a, b = alive[i], alive[j]
                if not b.alive:
                    continue
                if _dist_m(a.lat, a.lng, b.lat, b.lng) < (a.radius + b.radius) * METERS_PER_PX:
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
            assignments, venue_occupied = compute_assignment(alive, _VENUES)
            return {
                "phase":        self.phase,
                "running":      self._running,
                "tick":        self._tick_count,
                "total":       sum(g.count for g in alive),
                "groups":      [g.to_dict() for g in alive],
                "venues":      venues_snapshot(alive, venue_occupied),
                "assignments": assignments,
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
