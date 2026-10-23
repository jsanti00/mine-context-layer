"""
Mine Context Layer: MCP server.

Each agent starts its own copy of this server, with its identity in environment
variables. All copies share the same SQLite database, so whatever one agent
publishes, the others can read. Permissions follow the HW5 design:
  - source agents (radio, CMMS) can only publish events from their own source;
  - function agents (maintenance, planning) can read everything and submit proposals;
  - nobody approves through MCP: only the human, from the terminal.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Let this file import its sibling module when launched directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from mcl import store

# Who is connected: name, role and (for sources) which system it speaks for
AGENT = os.environ.get("MCL_AGENT", "unknown-agent")
ROLE = os.environ.get("MCL_ROLE", "function")
SOURCE = os.environ.get("MCL_SOURCE", "")
DB = os.environ["MCL_DB"]

mcp = FastMCP("mine-context-layer")

# Types of events the layer accepts, so agents speak the same vocabulary
EVENT_TYPES = ["equipment_failure", "equipment_status", "production_update", "safety",
               "maintenance_record", "maintenance_plan", "personnel", "other"]


def db():
    # Open the shared database for one tool call
    return store.connect(DB)


def log_call(con, tool, args, ok=True):
    # Keep a trace of every tool call: this is the execution evidence for the demo
    con.execute("INSERT INTO tool_calls (agent, tool, args, ok, at) VALUES (?,?,?,?,?)",
                (AGENT, tool, json.dumps(args, ensure_ascii=False), int(ok), store.now_wall()))
    con.commit()


def shift_time(con):
    # Current time on the simulated shift clock (HH:MM)
    return store.get_meta(con, "sim_now", "06:00")


def add_minutes(hhmm, minutes):
    # Add minutes to a HH:MM shift time
    t = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def event_dict(row):
    # Turn a database row into the JSON shape that agents receive
    d = dict(row)
    for k in ("subject_ids", "payload", "unresolved"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def proposal_dict(row):
    # Same for proposals
    d = dict(row)
    for k in ("recommendation", "options", "sources", "missing_info"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


@mcp.tool()
def publish_event(type: str, summary: str, occurred_at: str, mentions: list[str],
                  location: str = "", confidence: float = 0.8, valid_minutes: int = 240,
                  raw_ref: str = "", raw_text: str = "", payload: dict | None = None) -> dict:
    """Publish an event from your source system into the shared shift context.

    Only source agents may call this. `type` must be one of: equipment_failure,
    equipment_status, production_update, safety, maintenance_record,
    maintenance_plan, personnel, other. `mentions` lists the equipment or places
    as they appear in the source (e.g. "scoop 7", "1310-04"); the layer resolves
    them to system ids and tells you which ones it could not resolve.
    `occurred_at` is the shift time HH:MM. `raw_ref` and `raw_text` link the event
    to the original record so anyone can check it. `payload` holds extra fields.
    """
    con = db()
    args = {"type": type, "summary": summary, "mentions": mentions, "location": location}
    # Permission check: only sources publish, and only as themselves
    if ROLE != "source":
        log_call(con, "publish_event", args, ok=False)
        return {"error": "permission_denied", "detail": "Only source agents publish events. Use submit_proposal."}
    if type not in EVENT_TYPES:
        log_call(con, "publish_event", args, ok=False)
        return {"error": "invalid_type", "allowed": EVENT_TYPES}
    # Resolve every mention to a system id; keep the ones we cannot resolve
    resolved, unresolved = [], []
    for m in mentions + ([location] if location else []):
        cid = store.resolve(con, m)
        if cid and cid not in resolved:
            resolved.append(cid)
        elif not cid:
            unresolved.append(m)
    loc_id = store.resolve(con, location) if location else None
    eid = store.next_id(con, "events", "EV")
    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        eid, type, summary, json.dumps(resolved), loc_id or location, occurred_at,
        shift_time(con), add_minutes(occurred_at, valid_minutes), AGENT, SOURCE,
        confidence, raw_ref, raw_text, json.dumps(payload or {}), json.dumps(unresolved)))
    con.commit()
    log_call(con, "publish_event", args)
    return {"event_id": eid, "resolved_ids": resolved, "unresolved_mentions": unresolved,
            "location_id": loc_id}


@mcp.tool()
def get_shift_snapshot() -> dict:
    """Overview of the shift: clock, targets, the mine's operating rules, source health,
    open events and pending proposals.

    Call this first. If a source is not 'online', information from it is missing.
    """
    con = db()
    log_call(con, "get_shift_snapshot", {})
    now = shift_time(con)
    events = [event_dict(r) for r in con.execute("SELECT * FROM events ORDER BY occurred_at")]
    open_events = [e for e in events if e["valid_until"] >= now]
    return {
        "shift_time": now,
        "shift": json.loads(store.get_meta(con, "shift", "{}")),
        "operating_rules": json.loads(store.get_meta(con, "rules", "[]")),
        "sources": [dict(r) for r in con.execute("SELECT * FROM sources")],
        "open_events": [{k: e[k] for k in ("id", "type", "summary", "subject_ids", "location",
                                           "occurred_at", "source", "confidence")} for e in open_events],
        "proposals": [{"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"],
                       "author_agent": r["author_agent"]}
                      for r in con.execute("SELECT * FROM proposals")],
    }


@mcp.tool()
def get_open_events(type: str = "", subject_id: str = "") -> list[dict]:
    """Full detail of events that are still valid, optionally filtered by type or by a system id (e.g. LH-07)."""
    con = db()
    log_call(con, "get_open_events", {"type": type, "subject_id": subject_id})
    now = shift_time(con)
    out = []
    for r in con.execute("SELECT * FROM events ORDER BY occurred_at"):
        e = event_dict(r)
        if e["valid_until"] < now:
            continue
        if type and e["type"] != type:
            continue
        if subject_id and subject_id not in (e["subject_ids"] or []) and e["location"] != subject_id:
            continue
        out.append(e)
    return out


@mcp.tool()
def get_equipment_state(equipment_id: str = "", equipment_type_prefix: str = "") -> list[dict]:
    """Current state of equipment: shift-start fleet data plus any open events about it.

    Filter by one id (e.g. "LH-07") or by type prefix (e.g. "LH" for all loaders).
    `current_status` reflects the latest open failure or status event, if any.
    """
    con = db()
    log_call(con, "get_equipment_state", {"equipment_id": equipment_id, "prefix": equipment_type_prefix})
    now = shift_time(con)
    events = [event_dict(r) for r in con.execute(
        "SELECT * FROM events WHERE type IN ('equipment_failure','equipment_status') ORDER BY occurred_at")]
    out = []
    for r in con.execute("SELECT * FROM ref WHERE kind='equipment' ORDER BY id"):
        if equipment_id and r["id"] != equipment_id:
            continue
        if equipment_type_prefix and not r["id"].startswith(equipment_type_prefix):
            continue
        d = json.loads(r["data"])
        # Shift-start figures are planned totals for the full shift, not live values
        state = {"id": r["id"], "type": d.get("tipo"), "assigned_location": d.get("ubicacion"),
                 "planned_tonnes_shift": d.get("toneladas_movidas_turno"),
                 "avg_cycle_min": d.get("tiempo_ciclo_prom_min"),
                 "availability_pct": d.get("disponibilidad_pct"),
                 "current_status": "operating (no open events)", "status_event": None}
        for e in events:
            if r["id"] in (e["subject_ids"] or []) and e["valid_until"] >= now:
                state["current_status"] = f"{e['type']}: {e['summary']}"
                state["status_event"] = e["id"]
        out.append(state)
    return out


@mcp.tool()
def get_plan_slice(level: int = 0, place_id: str = "", include_available_places: bool = True) -> dict:
    """The shift plan: stopes with tonnes, grade, ounces, assigned loader, level and priority.

    Filter by mine level (e.g. 1310) or place id (e.g. "TP-1310-04").
    With include_available_places, also lists places that are ready but idle.
    """
    con = db()
    log_call(con, "get_plan_slice", {"level": level, "place_id": place_id})
    stopes = []
    for r in con.execute("SELECT * FROM ref WHERE kind='stope' ORDER BY id"):
        d = json.loads(r["data"])
        if level and d.get("nivel") != level:
            continue
        if place_id and r["id"] != place_id:
            continue
        stopes.append({"id": r["id"], "level": d.get("nivel"), "phase": d.get("fase"),
                       "target_tonnes": d.get("toneladas_objetivo"), "grade_gpt": d.get("ley_plan_gt"),
                       "planned_oz": d.get("onzas_plan"), "loader": d.get("equipo_carguio"),
                       "drill": d.get("equipo_perforacion"), "priority": d.get("prioridad")})
    available = []
    if include_available_places:
        for r in con.execute("SELECT * FROM ref WHERE kind='workplace' ORDER BY id"):
            d = json.loads(r["data"])
            if d.get("estado_preparacion") == "disponible":
                available.append({"id": r["id"], "level": d.get("nivel"), "grade_gpt": d.get("ley_gt"),
                                  "tonnes_available": d.get("toneladas_disponibles"),
                                  "ready_for": d.get("listo_para"),
                                  "activation_h": d.get("tiempo_activacion_h"), "notes": d.get("observaciones")})
    return {"shift": json.loads(store.get_meta(con, "shift", "{}")), "stopes": stopes,
            "available_places": available}


@mcp.tool()
def submit_proposal(kind: str, title: str, recommendation: dict, options: list[dict],
                    calculation: str, sources: list[str], confidence: float,
                    missing_info: list[str] | None = None) -> dict:
    """Submit a proposal to the layer. Only function agents may call this.

    kind: "assessment" (information other agents can use, no approval needed)
          or "action" (a change to the operation; a human must approve it).
    recommendation: the core answer as structured fields.
    options: alternatives considered, each with its numbers.
    calculation: how the numbers were obtained, step by step.
    sources: ids of events or proposals used (e.g. ["EV-0001", "PR-0001"]); all must exist.
    confidence: 0 to 1.
    missing_info: what is missing and where a human could get it.
    """
    con = db()
    args = {"kind": kind, "title": title}
    if ROLE != "function":
        log_call(con, "submit_proposal", args, ok=False)
        return {"error": "permission_denied", "detail": "Only function agents submit proposals."}
    if kind not in ("assessment", "action"):
        log_call(con, "submit_proposal", args, ok=False)
        return {"error": "invalid_kind", "allowed": ["assessment", "action"]}
    # Every cited source must exist: proposals have to be checkable
    known = {r["id"] for r in con.execute("SELECT id FROM events")} | \
            {r["id"] for r in con.execute("SELECT id FROM proposals")}
    bad = [s for s in sources if s not in known]
    if bad:
        log_call(con, "submit_proposal", args, ok=False)
        return {"error": "unknown_sources", "unknown": bad}
    pid = store.next_id(con, "proposals", "PR")
    status = "pending_approval" if kind == "action" else "published"
    con.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        pid, kind, title, AGENT, shift_time(con), json.dumps(recommendation, ensure_ascii=False),
        json.dumps(options, ensure_ascii=False), calculation, json.dumps(sources),
        confidence, json.dumps(missing_info or [], ensure_ascii=False), status))
    con.commit()
    log_call(con, "submit_proposal", args)
    return {"proposal_id": pid, "status": status}


@mcp.tool()
def get_proposals(kind: str = "") -> list[dict]:
    """Full detail of proposals in the layer, optionally only 'assessment' or 'action'."""
    con = db()
    log_call(con, "get_proposals", {"kind": kind})
    rows = con.execute("SELECT * FROM proposals ORDER BY id").fetchall()
    return [proposal_dict(r) for r in rows if not kind or r["kind"] == kind]


@mcp.tool()
def get_decisions() -> list[dict]:
    """Decision log: what the human approved or rejected, when and why."""
    con = db()
    log_call(con, "get_decisions", {})
    return [dict(r) for r in con.execute("SELECT * FROM decisions ORDER BY id")]


if __name__ == "__main__":
    # Talk to the agent over standard input and output
    mcp.run()
