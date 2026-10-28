"""
Mine Context Layer v2 (HW7): MCP server over the calibrated synthetic mine (eval/mina_v2.py).

The benchmark harness writes the shift state at each decision moment into the shared SQLite
file (what the sources have reported, where each loader is, stockpiles, trucks, travel times).
Agents read it only through these tools and submit one decision. Same permission model as HW6:
function agents submit, nobody approves through MCP.

MCL_FOCUS (optional): a stope id. Used by the "one failure at a time" baseline: the agent only
sees the events about that stope and is asked to plan only for it.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

AGENT = os.environ.get("MCL_AGENT", "unknown-agent")
ROLE = os.environ.get("MCL_ROLE", "function")
FOCUS = os.environ.get("MCL_FOCUS", "")
DB = os.environ["MCL_DB"]

mcp = FastMCP("mine-context-layer-v2")


def db():
    # Open the shared database for one tool call
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def state(con):
    # The shift state the harness wrote for this decision moment
    return json.loads(con.execute("SELECT value FROM meta WHERE key='state'").fetchone()["value"])


def log_call(con, tool, args, ok=True):
    # Trace of every tool call: execution evidence for the benchmark
    con.execute("INSERT INTO tool_calls (agent, tool, args, ok, at) VALUES (?,?,?,?,?)",
                (AGENT, tool, json.dumps(args, ensure_ascii=False), int(ok),
                 datetime.now(timezone.utc).isoformat(timespec="milliseconds")))
    con.commit()


def visible_events(st):
    # In focus mode the agent only sees the events about its own stope
    ev = st["events"]
    return [e for e in ev if e.get("place") == FOCUS] if FOCUS else ev


@mcp.tool()
def get_shift_snapshot() -> dict:
    """Start here. Shift clock, value metric, operating rules, source health, open events,
    loans in force and the loader in preventive maintenance."""
    con = db(); st = state(con); log_call(con, "get_shift_snapshot", {})
    out = {k: st[k] for k in ("shift_time", "shift", "value_metric", "operating_rules", "sources",
                              "loans_in_force", "preventive_maintenance")}
    out["open_events"] = visible_events(st)
    if FOCUS:
        out["your_task"] = f"Plan only for the loader failure at {FOCUS}. Other incidents are handled by other planners."
    return out


@mcp.tool()
def get_places(mine: str = "") -> dict:
    """Production stopes, ready reserve stopes and development headings, with live figures:
    broken ore still in the stope (t), grade, phase, home loader, loader currently there,
    loading rate by loader model (t/h) and, for headings, the value of one loader hour (oz eq)."""
    con = db(); st = state(con); log_call(con, "get_places", {"mine": mine})
    f = (lambda x: not mine or x["mine"] == mine)
    return {"stopes": [x for x in st["stopes"] if f(x)], "reserves": [x for x in st["reserves"] if f(x)],
            "development_headings": [x for x in st["headings"] if f(x)]}


@mcp.tool()
def get_loaders() -> list[dict]:
    """Every loader: model, home place, where it is now, status (operating, down, preventive
    maintenance, on loan) and when it is available."""
    con = db(); st = state(con); log_call(con, "get_loaders", {})
    out = st["loaders"]
    if FOCUS:
        # The focused planner does not see other failures as failures
        out = [dict(x, status="operating", available_from=x["available_from"])
               if x["status"] == "down" and x["home"] != FOCUS else x for x in out]
    return out


@mcp.tool()
def get_haulage() -> list[dict]:
    """Per mine: level stockpile now (t and oz) and its capacity, trucks running, truck capacity
    to surface (t/h) and current loader inflow (t/h). If inflow exceeds truck capacity, the mine
    is truck-limited and an extra loader there adds little until the stockpile has room."""
    con = db(); st = state(con); log_call(con, "get_haulage", {})
    return st["haulage"]


@mcp.tool()
def get_travel_times(from_ids: list[str], to_ids: list[str]) -> dict:
    """Loader travel time in hours between places (shortest path through drifts, ramps and the
    surface road, plus a fixed manoeuvre time). Ids are stopes, reserves, headings or TALLER-564."""
    con = db(); st = state(con); log_call(con, "get_travel_times", {"from": from_ids, "to": to_ids})
    m = st["travel_h"]
    return {a: {b: m.get(a, {}).get(b) for b in to_ids} for a in from_ids}


@mcp.tool()
def get_repair_estimates() -> list[dict]:
    """Repair estimates from maintenance (CMMS work orders) for loaders that are down:
    low / likely / high hours from the failure time, where the repair happens, and updates."""
    con = db(); st = state(con); log_call(con, "get_repair_estimates", {})
    est = st["repair_estimates"]
    return [e for e in est if e["place"] == FOCUS] if FOCUS else est


@mcp.tool()
def submit_decision(loans: list[dict], reserve_activations: list[dict], confirmations_requested: list[dict],
                    expected_net_oz: float, rationale: str, confidence: float, sources: list[str]) -> dict:
    """Submit your decision for this moment. A human approves it outside MCP.

    loans: ALL loans that should be in force after this decision, including existing ones you keep,
           as [{"loader": "LH-07", "to": "TJ-C512-10"}]. A loader on loan that you do not list goes home.
    reserve_activations: [{"loader": ..., "reserve": "RS-..."}], only loaders freed by a stopped stope.
    confirmations_requested: [{"place": ..., "ask": "who to call and what to confirm"}].
    expected_net_oz: your estimate of ounces gained versus doing nothing.
    sources: event ids you relied on.
    """
    con = db()
    args = {"loans": loans, "reserve_activations": reserve_activations,
            "confirmations_requested": confirmations_requested}
    if ROLE != "function":
        log_call(con, "submit_decision", args, ok=False)
        return {"error": "permission_denied"}
    con.execute("INSERT INTO proposals (agent, focus, body, at) VALUES (?,?,?,?)",
                (AGENT, FOCUS, json.dumps({**args, "expected_net_oz": expected_net_oz, "rationale": rationale,
                                           "confidence": confidence, "sources": sources}, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec="milliseconds")))
    con.commit()
    log_call(con, "submit_decision", args)
    return {"status": "pending_approval"}


if __name__ == "__main__":
    mcp.run()
