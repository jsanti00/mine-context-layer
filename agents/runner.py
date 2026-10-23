"""
Runs one agent as a headless Claude Code session connected only to the layer.

The agent gets no built-in tools (no file reading, no shell): its only way to see
or change the shift is the Mine Context Layer over MCP. Every tool call is printed
live, so the terminal shows the real execution.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Colors so each agent is easy to follow in the terminal
COLORS = {"radio-agent": "\033[36m", "cmms-agent": "\033[35m", "maintenance-agent": "\033[33m",
          "planning-agent": "\033[32m", "single-agent": "\033[34m"}
RESET, DIM = "\033[0m", "\033[2m"


def claude_bin():
    # Find the Claude Code executable
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def short(obj, n=110):
    # Shorten long tool arguments so one call fits on one line
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "...}"


def run_agent(name, role, system_prompt, user_prompt, db_path, source="", model="sonnet",
              builtin_tools="", workdir=None, use_layer=True, log_dir=None):
    """Start the agent, stream its tool calls, and return cost, time and final message."""
    color = COLORS.get(name, "")
    cfg_dir = Path(tempfile.mkdtemp(prefix="mcl-"))
    cmd = [claude_bin(), "-p", user_prompt, "--system-prompt", system_prompt,
           "--model", model, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--setting-sources", "project",
           "--tools", builtin_tools]
    if use_layer:
        # The MCP server knows who this agent is through environment variables
        cfg = {"mcpServers": {"mcl": {
            "command": str(ROOT / ".venv/bin/python"), "args": [str(ROOT / "mcl/server.py")],
            "env": {"MCL_DB": str(db_path), "MCL_AGENT": name, "MCL_ROLE": role, "MCL_SOURCE": source}}}}
        (cfg_dir / "mcp.json").write_text(json.dumps(cfg))
        cmd += ["--mcp-config", str(cfg_dir / "mcp.json"), "--strict-mcp-config",
                "--allowedTools", "mcp__mcl__*"]
    else:
        cmd += ["--strict-mcp-config"]
        if builtin_tools:
            cmd += ["--allowedTools", builtin_tools]
    # Keep the agent away from any personal Claude settings or memory files
    env = dict(os.environ, CLAUDE_CODE_DISABLE_CLAUDE_MDS="1")
    print(f"{color}[{name}] waking up{RESET}")
    start = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=workdir or cfg_dir, env=env)
    result, transcript = {}, []
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        transcript.append(msg)
        # Print each tool the agent calls, with its arguments
        if msg.get("type") == "assistant":
            for block in msg["message"].get("content", []):
                if block.get("type") == "tool_use":
                    tool = block["name"].replace("mcp__mcl__", "")
                    print(f"{color}[{name}] -> {tool}{RESET} {DIM}{short(block['input'])}{RESET}")
        # Print the layer's short answer for writes, so ids are visible
        if msg.get("type") == "user":
            for block in msg["message"].get("content", []):
                if block.get("type") == "tool_result":
                    text = block.get("content")
                    text = text[0].get("text", "") if isinstance(text, list) and text else str(text)
                    if '"event_id"' in text or '"proposal_id"' in text or '"error"' in text:
                        print(f"{color}[{name}] <- {RESET}{DIM}{text[:160]}{RESET}")
        if msg.get("type") == "result":
            result = msg
    proc.wait()
    elapsed = time.time() - start
    if proc.returncode != 0 and not result:
        print(proc.stderr.read(), file=sys.stderr)
    if log_dir:
        # Save the full transcript as evidence, minus the header with local install metadata
        Path(log_dir, f"{name}.jsonl").write_text("\n".join(
            json.dumps(m, ensure_ascii=False) for m in transcript if m.get("subtype") != "init"))
    shutil.rmtree(cfg_dir, ignore_errors=True)
    final = (result.get("result") or "").strip()
    cost = result.get("total_cost_usd") or 0.0
    print(f"{color}[{name}] done in {elapsed:.0f}s, ${cost:.3f}: {RESET}{final[:200]}")
    return {"agent": name, "seconds": round(elapsed, 1), "cost_usd": round(cost, 4),
            "turns": result.get("num_turns"), "final": final, "ok": not result.get("is_error", False)}
