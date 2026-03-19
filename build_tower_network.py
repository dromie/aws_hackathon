#!/usr/bin/env python3
"""
Generate tower_network.json from tower (venue) locations.
- Builds edges from a 2D Delaunay triangulation of tower (lat, lng) positions.
- Assigns each edge the distance (m) between towers (same formula as server.py).
- Randomly removes a modest number of edges while keeping the graph connected.
- Writes tower_network.json (same format as expected by crowd_map.html).
Run from aws_hackathon: python build_tower_network.py
"""
import json
import math
import os
import random

import numpy as np
from scipy.spatial import Delaunay

# Same constants as server.py (approximate at Budapest latitude)
METERS_PER_LAT = 111000
METERS_PER_LNG = 74000


def dist_m(lat1, lng1, lat2, lng2):
    return math.hypot(
        (lat1 - lat2) * METERS_PER_LAT,
        (lng1 - lng2) * METERS_PER_LNG,
    )


# Tower locations: read from server.py _VENUES (id, lat, lng)
def get_venues_from_server():
    """Read _VENUES from server.py without running the simulation."""
    import re
    base = os.path.dirname(os.path.abspath(__file__))
    server_path = os.path.join(base, "server.py")
    with open(server_path, encoding="utf-8") as f:
        content = f.read()
    start = content.find("_VENUES = [")
    if start < 0:
        return None
    i = content.find("[", start) + 1
    depth = 1
    while i < len(content) and depth:
        if content[i] == "[":
            depth += 1
        elif content[i] == "]":
            depth -= 1
        i += 1
    block = content[start:i]
    venues = []
    for m in re.finditer(
        r'"id"\s*:\s*(\d+).*?"lat"\s*:\s*([\d.]+).*?"lng"\s*:\s*([\d.]+)',
        block,
        re.DOTALL,
    ):
        venues.append({"id": int(m.group(1)), "lat": float(m.group(2)), "lng": float(m.group(3))})
    return venues if venues else None


def delaunay_edges(venues):
    """
    Return list of (tower_id_a, tower_id_b, distance_m) from Delaunay triangulation.
    Vertex order: sorted by tower id; point index i maps to ids[i].
    """
    by_id = {v["id"]: v for v in venues}
    ids = sorted(by_id.keys())
    if len(ids) < 2:
        return []
    coords = np.array([[by_id[i]["lat"], by_id[i]["lng"]] for i in ids], dtype=float)
    tri = Delaunay(coords)
    edge_pairs = set()
    for simplex in tri.simplices:
        for k in range(3):
            ia, ib = simplex[k], simplex[(k + 1) % 3]
            a, b = ids[ia], ids[ib]
            if a > b:
                a, b = b, a
            edge_pairs.add((a, b))
    out = []
    for a, b in edge_pairs:
        va, vb = by_id[a], by_id[b]
        d = round(dist_m(va["lat"], va["lng"], vb["lat"], vb["lng"]), 1)
        out.append((a, b, d))
    return out


def _is_connected(edge_list, node_ids):
    """Undirected connectivity from list of (u, v, d)."""
    if not node_ids:
        return True
    adj = {i: [] for i in node_ids}
    for u, v, _ in edge_list:
        adj[u].append(v)
        adj[v].append(u)
    start = node_ids[0]
    seen = {start}
    stack = [start]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == len(node_ids)


def remove_random_edges_keep_connected(all_edges, ids, target_remove):
    """Remove up to target_remove edges in random order if graph stays connected."""
    random.shuffle(all_edges)
    weights = {(min(a, b), max(a, b)): d for a, b, d in all_edges}
    kept = list(all_edges)
    removed = 0
    for e in list(all_edges):
        if removed >= target_remove:
            break
        a, b, d = e
        kept.remove(e)
        if _is_connected(kept, ids):
            removed += 1
        else:
            kept.append(e)
    out = []
    for a, b, d in kept:
        key = (min(a, b), max(a, b))
        out.append((a, b, round(weights[key], 1)))
    return out


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    venues = get_venues_from_server()
    if not venues:
        raise SystemExit("Could not read _VENUES from server.py")

    by_id = {v["id"]: v for v in venues}
    ids = sorted(by_id.keys())
    n = len(ids)

    all_edges = delaunay_edges(venues)
    # How many edges we can drop and still stay connected: at most |E| - (n-1)
    max_redundant = max(0, len(all_edges) - (n - 1))
    # "A few" — remove up to ~1/4 of Delaunay edges, capped, never below a spanning tree
    target_remove = min(max_redundant, max(3, len(all_edges) // 4))
    target_remove = min(target_remove, 18)  # hard cap so the map stays readable

    final_edges = remove_random_edges_keep_connected(all_edges, ids, target_remove)

    out = {
        "edges": [
            {"from": u, "to": v, "distance_m": d}
            for u, v, d in sorted(final_edges, key=lambda x: (x[0], x[1]))
        ]
    }
    out_path = os.path.join(base, "tower_network.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(
        f"Wrote {len(out['edges'])} edges to {out_path} "
        f"(Delaunay had {len(all_edges)} edges; removed up to {target_remove}, kept connected)."
    )


if __name__ == "__main__":
    main()
