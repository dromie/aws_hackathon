# Crowd Simulation – Budapest

A web app that simulates crowds moving toward event venues on an OpenStreetMap map of Budapest. It shows moving groups of people, fixed cell-tower (venue) hexagons with capacity and occupancy, and a **dynamic cell-tower assignment** that assigns each person to the best tower by capacity and distance. The assignment is recalculated every simulation tick and can be consumed via HTTP or **Model Context Protocol (MCP)**.

---

## Overview

- **Map**: OpenStreetMap (Leaflet), centered on Budapest, with tiles served from a local cache.
- **Simulation**: Groups of people move along a road graph; they wander for a few seconds, then rally toward one of two rally points (e.g. Nokia Skypark, Ericsson). Groups can merge when their circles overlap.
- **Cell towers (venues)**: Fixed hexagons with name, position, capacity, and current occupancy. Occupancy is driven by the **assignment** (not by a fixed radius).
- **Assignment**: Each tick, every person is assigned to a tower via a **min-cost flow** (groups supply people, towers have capacity; cost = distance). Result is shown as edges on the map (group → tower, thickness by count) and is available via the HTTP API and MCP tools.

---

## Running the app

### 1. Dependencies

```bash
pip install -r requirements.txt
```

### 2. HTTP server (map + simulation API)

Start the simulation and web server:

```bash
python server.py
```

- **Map UI**: open **http://localhost:8765/** (serves `crowd_map.html` and static files).
- **Tiles**: `http://localhost:8765/{z}/{x}/{y}.png` (from local `tiles/` cache).
- **Port**: 8765 (configurable via code).

Use the control bar to **Start** / **Pause**, **Step**, **Rewind**, and **Reset** the simulation.

### 3. MCP server (assignment as tools)

The assignment is also exposed as **MCP tools** via FastMCP. The MCP server does **not** run the simulation; it reads the current state from the HTTP server.

**Prerequisites**

- The **simulation server must be running** (`python server.py`) so the MCP server can call its API.

**Start the MCP server**

```bash
python mcp_server.py
```

Or with the FastMCP CLI:

```bash
fastmcp run mcp_server.py:mcp
```

By default the MCP server uses **http://localhost:8765** as the simulation API. To point at another host/port, set:

```bash
set SIMULATION_URL=http://host:8765
python mcp_server.py
```

(On Unix/macOS use `export SIMULATION_URL=...`.)

**Tools (no input)**

| Tool | Description |
|------|-------------|
| **`get_assignment_group_level`** | Returns the current assignment at **group level**: a list of `{ "groupIndex", "venueId", "count" }` — one entry per (group, tower) pair with at least one person assigned. |
| **`get_assignment_person_level`** | Returns the current assignment at **person level**: a list of `{ "groupIndex", "venueId" }` with **one entry per person**. Same assignment as group-level, just expanded (each `count` becomes that many identical group–venue pairs). |

Both tools use the **live** simulation state at the time of the call (latest snapshot from `GET /api/groups`).

---

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/groups` | Current snapshot: `phase`, `running`, `tick`, `total`, `groups`, `venues`, `assignments`. |
| POST | `/api/control` | Body: `{ "action": "start" \| "stop" \| "step" \| "rewind" \| "reset" }`. Returns the snapshot after the action. |
| GET | `/api/venues` | List of venues with occupancy (distance-based if called without going through the main snapshot). |

Snapshot `assignments` is the same structure as the MCP **group-level** tool: list of `{ "groupIndex", "venueId", "count" }`.

---

## Assignment model

- **Person-level**: Each person is assigned to exactly one tower (or to an overflow sink if total people exceed total capacity).
- **Cost**: Distance (metres) from the person’s position (group centre) to the tower.
- **Method**: Min-cost flow: groups supply `count` units, towers have `capacity`; edge cost = distance. Implemented in `server.py` with NetworkX (`nx.min_cost_flow`).
- **Output**: 
  - **Group-level**: list of (group index, venue id, count) for each pair with count &gt; 0.
  - **Person-level**: same assignment expanded to one entry per person (group index + venue id).

Venue **occupancy** in the snapshot is the sum of assigned counts to that venue.

---

## Tower network (`tower_network.json`)

Towers are assumed to be linked by a **fixed network** (topology only; not used for assignment, which is distance-based). The map draws these links between venue positions.

- **Format**: `{ "edges": [ { "from": <venue id>, "to": <venue id>, "distance_m": <float> }, ... ] }`.
- **Generating the file**: Run from this folder:

  ```bash
  python build_tower_network.py
  ```

  The script reads `_VENUES` from `server.py` (no need to import the running server), builds a **Delaunay triangulation** of tower `(lat, lng)` positions (via **SciPy**), sets each edge’s `distance_m` using the same planar distance formula as the server, then **randomly removes a limited number of edges** (up to about one quarter of the Delaunay edges, capped) while keeping the graph **connected** (**NetworkX**). It writes `tower_network.json`. Run it again for a new random subset of removable edges.

- If `tower_network.json` is missing, run the command above once before relying on the tower-link overlay in the browser.

---

## Project structure

| File | Purpose |
|------|--------|
| `server.py` | HTTP server, simulation loop, road graph, assignment (`compute_assignment`), venues. |
| `crowd_map.html` | Leaflet map, canvas overlay for groups, venue hexagons, assignment edges, tower network lines. |
| `mcp_server.py` | FastMCP server; tools `get_assignment_group_level` and `get_assignment_person_level` (no input). |
| `road_network.json` | Road graph (nodes, edges) for movement. |
| `tower_network.json` | Fixed tower-to-tower network (edges + distances); generated by `build_tower_network.py`. |
| `build_tower_network.py` | Builds `tower_network.json` from `_VENUES` in `server.py`. |
| `requirements.txt` | Python deps (e.g. `networkx`, `requests`, `fastmcp`). |
| `Dockerfile` / `deploy.py` / `teardown.py` | Build and deployment. |

---

## Dependencies

- **networkx** – road graph and min-cost flow.
- **requests** – used by the MCP server to call the simulation API.
- **fastmcp** – MCP server and tool definitions.

See `requirements.txt` for versions.
