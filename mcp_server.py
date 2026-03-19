#!/usr/bin/env python3
"""
MCP server exposing the crowd simulation's cell-tower assignment as tools.
No input: each tool uses the current simulation state from the running HTTP server.
Requires the simulation server to be running (e.g. python server.py).
"""
import os
import requests
from fastmcp import FastMCP

# Base URL of the simulation HTTP server (default: same host, port 8765)
SIMULATION_URL = os.environ.get("SIMULATION_URL", "http://localhost:8765").rstrip("/")


def _fetch_snapshot():
    """GET current snapshot from the simulation API. Raises on error."""
    r = requests.get(f"{SIMULATION_URL}/api/groups", timeout=5)
    r.raise_for_status()
    return r.json()


mcp = FastMCP(
    "Crowd Simulation Assignment",
    description="Cell-tower assignment for the Budapest crowd simulation (persons → towers).",
)


@mcp.tool()
def get_assignment_group_level() -> list[dict]:
    """
    Return the current cell-tower assignment at group level.
    No input: uses the live simulation state.
    Each item has groupIndex (int), venueId (int), and count (int): how many persons
    from that group are assigned to that tower.
    """
    data = _fetch_snapshot()
    return data.get("assignments", [])


@mcp.tool()
def get_assignment_person_level() -> list[dict]:
    """
    Return the current cell-tower assignment at person level (one entry per person).
    No input: uses the live simulation state.
    Each item has personId (int, unique ID assigned when the person was created in the simulation)
    and venueId (int). Person IDs are stable across timesteps until groups merge or simulation resets.
    """
    data = _fetch_snapshot()
    return data.get("person_assignments", [])


if __name__ == "__main__":
    mcp.run()
