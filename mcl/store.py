"""
Storage for the Mine Context Layer.

Everything the agents share lives in one SQLite file: the events that source
agents publish, the proposals that function agents submit, the human decisions,
and the reference data (plan, fleet, stope inventory) loaded from the seed.
"""

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Folder where the synthetic mine dataset lives
SEED = Path(__file__).resolve().parent.parent / "data" / "seed"

# Table definitions, created once per run
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, type TEXT, summary TEXT, subject_ids TEXT, location TEXT,
    occurred_at TEXT, received_at TEXT, valid_until TEXT, author_agent TEXT,
    source TEXT, confidence REAL, raw_ref TEXT, raw_text TEXT, payload TEXT,
    unresolved TEXT);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, author_agent TEXT, created_at TEXT,
    recommendation TEXT, options TEXT, calculation TEXT, sources TEXT,
    confidence REAL, missing_info TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT, decision TEXT,
    decided_by TEXT, note TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, status TEXT, since TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, canonical TEXT);
CREATE TABLE IF NOT EXISTS ref (kind TEXT, id TEXT, data TEXT, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, tool TEXT, args TEXT,
    ok INTEGER, at TEXT);
"""


def now_wall():
    # Real clock time, used to measure how long the agents take
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(db_path):
    # Open the shared database; rows come back as dictionaries
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_meta(con, key, default=None):
    # Read one setting, such as the simulated shift clock
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    # Save one setting
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
    con.commit()


def next_id(con, table, prefix):
    # Build the next readable id, for example EV-0003 or PR-0002
    n = con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    return f"{prefix}-{n + 1:04d}"


def add_aliases(con, canonical, names):
    # Register the different ways people say the same thing on the radio
    for name in names:
        con.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)", (normalize(name), canonical))


def normalize(text):
    # Lowercase and squeeze spaces so "Scoop  7" and "scoop 7" match
    return re.sub(r"\s+", " ", text.strip().lower())


def resolve(con, mention):
    # Turn a mention like "scoop 7" into the system id "LH-07"; None if unknown
    row = con.execute("SELECT canonical FROM aliases WHERE alias=?", (normalize(mention),)).fetchone()
    return row["canonical"] if row else None


# Radio nicknames per equipment prefix (how operators call each machine)
NICKNAMES = {"LH": ["scoop", "lhd"], "PP": ["perforadora"], "JD": ["jumbo"],
             "CT": ["camion"], "BL": ["bolter", "empernador"]}


def seed(con):
    """Load the plan, fleet, stope inventory and name aliases into the layer."""
    plan = json.loads((SEED / "Plan_de_turno.json").read_text())
    fleet = json.loads((SEED / "Telemetria_flota.json").read_text())
    inv = json.loads((SEED / "Inventario_de_frentes.json").read_text())

    # Stopes and development headings from the shift plan
    for s in plan["tajos_produccion"]:
        s = {k: v for k, v in s.items() if k != "cronograma"}
        con.execute("INSERT OR REPLACE INTO ref VALUES ('stope',?,?)", (s["id"], json.dumps(s)))
    for f in plan["frentes_desarrollo"]:
        f = {k: v for k, v in f.items() if k != "cronograma"}
        con.execute("INSERT OR REPLACE INTO ref VALUES ('heading',?,?)", (f["id"], json.dumps(f)))
    # Inventory of all work places, including the available alternatives
    for f in inv["frentes"]:
        con.execute("INSERT OR REPLACE INTO ref VALUES ('workplace',?,?)", (f["id"], json.dumps(f)))
        # Places are called "1310-04" or "tajo 1310-04" on the radio
        short = f["id"].split("-", 1)[1]
        add_aliases(con, f["id"], [f["id"], short, f"tajo {short}", f"frente {short}"])
    # Fleet state at shift start (as exported by the fleet management system)
    for e in fleet["equipos_baseline"]:
        con.execute("INSERT OR REPLACE INTO ref VALUES ('equipment',?,?)", (e["equipo_id"], json.dumps(e)))
        prefix, num = e["equipo_id"].split("-")
        names = [e["equipo_id"], e["equipo_id"].replace("-", " ")]
        for nick in NICKNAMES.get(prefix, []):
            names += [f"{nick} {int(num)}", f"{nick} {num}", f"{nick}{int(num)}"]
        add_aliases(con, e["equipo_id"], names)
    # Shift-level facts the agents need for any calculation
    targets = plan["targets_por_funcion"]["produccion"]
    set_meta(con, "shift", json.dumps({
        "shift_id": plan["meta"].get("shift_id"), "date": plan["meta"].get("fecha"),
        "window": "06:00-18:00", "productive_window": "07:00-17:00",
        "target_tonnes": targets["toneladas_objetivo"], "target_oz": targets["onzas_objetivo"],
        "oz_formula": "oz = tonnes x grade_gpt / 31.1035",
        "note": "Every stope has exactly one loader and there is no spare loader on this shift."}))
    # Standing dispatch rules of the operation, shared with every agent
    rules = json.loads((SEED.parent / "site_rules.json").read_text())["rules"]
    set_meta(con, "rules", json.dumps(rules))
    # Both sources start online
    for name in ("radio", "cmms"):
        con.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?)", (name, "online", "06:00", ""))
    con.commit()


def load_cmms_rows():
    # The CMMS export, read only by the CMMS source agent
    rows = {}
    for name in ("CMMS_historial_mantenimiento.csv", "CMMS_plan_preventivo.csv"):
        with open(SEED / name) as fh:
            rows[name] = list(csv.DictReader(fh))
    return rows


def radio_feed(start, end, incident="INC-01"):
    # Radio lines heard between two shift times: routine chatter plus the incident
    radio = json.loads((SEED / "Radio_transcripciones.json").read_text())
    lines = [m for m in radio["mensajes_baseline"] if start <= m["timestamp"] <= end]
    for block in radio["mensajes_por_incidente"]:
        if block["incidente_id"] == incident:
            lines += [m for m in block["mensajes"] if start <= m["timestamp"] <= end]
    lines.sort(key=lambda m: m["timestamp"])
    # Give each line a stable reference so events can link back to the original text
    for m in lines:
        m["ref"] = f"radio://{m['canal']}/{m['timestamp']}/{normalize(m['indicativo']).replace(' ', '-')}"
        m.pop("incidente_id", None)
    return lines
