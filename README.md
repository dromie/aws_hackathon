# Crowd Simulation – Budapest

A web app that simulates crowds moving toward event venues on an OpenStreetMap map of Budapest. It shows moving groups of people, fixed cell-tower (venue) hexagons with capacity and occupancy, and a **dynamic cell-tower assignment** that assigns each person to the best tower by capacity and distance. The assignment is recalculated every simulation tick and can be consumed via HTTP or **Model Context Protocol (MCP)**.

---

## Overview

- **Map**: OpenStreetMap (Leaflet), centered on Budapest, with tiles served from a local cache.
- **Simulation**: Each **person** has a unique integer ID assigned at creation. Persons belong to **groups**; each group keeps a list of its member person IDs. Groups move along a road graph (wander, then rally toward one of two rally points); when two groups overlap they **merge** (one group’s members are moved into the other). Person IDs are stable across timesteps until merge or reset.
- **Cell towers (venues)**: Fixed hexagons with name, position, capacity, and current occupancy. Occupancy is driven by the **assignment** (not by a fixed radius).
- **Assignment**: Each tick, every person is assigned to a tower via a **min-cost flow** solved at **person level** (one node per person; cost = distance from person’s position to tower). Result is shown as edges on the map (group → tower, thickness by count) and is available as group-level or person-level via the HTTP API and MCP tools.

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
| **`get_assignment_person_level`** | Returns the current assignment at **person level**: a list of `{ "personId", "venueId" }` with **one entry per person**. `personId` is the unique integer ID assigned when the person was created in the simulation. |

Both tools use the **live** simulation state at the time of the call (latest snapshot from `GET /api/groups`).

---

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/groups` | Current snapshot: `phase`, `running`, `tick`, `total`, `groups`, `venues`, `assignments`. |
| POST | `/api/control` | Body: `{ "action": "start" \| "stop" \| "step" \| "rewind" \| "reset" }`. Returns the snapshot after the action. |
| GET | `/api/venues` | List of venues with occupancy (distance-based if called without going through the main snapshot). |

Snapshot includes `assignments` (group-level: `{ "groupIndex", "venueId", "count" }`) and `person_assignments` (person-level: `{ "personId", "venueId" }` with real person IDs).

---

## Assignment model

- **Person-level**: Each person (unique ID) is assigned to exactly one tower (or to an overflow sink if total people exceed total capacity). The min-cost flow is solved with **one node per person**.
- **Cost**: Distance (metres) from the person’s position (their group’s centre) to the tower.
- **Method**: Min-cost flow in `server.py` with NetworkX: person nodes (supply 1), venue nodes (capacity), edge cost = distance.
- **Output**: 
  - **Group-level**: list of (group index, venue id, count) for visualization and edges on the map.
  - **Person-level**: list of (personId, venueId) with the simulation’s real person IDs.

Venue **occupancy** in the snapshot is the sum of assigned counts to that venue.

---

## Project structure

| File | Purpose |
|------|--------|
| `server.py` | HTTP server, simulation loop, road graph, assignment (`compute_assignment`), venues. |
| `crowd_map.html` | Leaflet map, canvas overlay for groups, venue hexagons, and assignment edges. |
| `mcp_server.py` | FastMCP server; tools `get_assignment_group_level` and `get_assignment_person_level` (no input). |
| `road_network.json` | Road graph (nodes, edges) for movement. |
| `requirements.txt` | Python deps (e.g. `networkx`, `requests`, `fastmcp`). |
| `Dockerfile` / `deploy.py` / `teardown.py` | Build and deployment. |

---

## Dependencies

- **networkx** – road graph and min-cost flow.
- **requests** – used by the MCP server to call the simulation API.
- **fastmcp** – MCP server and tool definitions.

See `requirements.txt` for versions.
