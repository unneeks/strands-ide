"""IDE backend.

Serves a Monaco-based web code editor and a small set of file APIs, and proxies
chat requests to the local AgentCore runtime (../agent) over HTTP. The IDE and
the agent share the same workspace directory on disk, so edits made by the user
in the editor and edits made by the agent both land in the same files.

Run:
    python -m ide.server         (serves http://localhost:3000)

Env:
    WORKSPACE_DIR   workspace to edit (default ../workspace)
    AGENT_URL       agent runtime base URL (default http://localhost:8080)
    IDE_PORT        port to serve the IDE on (default 3000)
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("strands-ide")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

WORKSPACE_DIR = Path(
    os.environ.get(
        "WORKSPACE_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workspace")),
    )
).resolve()
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080").rstrip("/")
IDE_PORT = int(os.environ.get("IDE_PORT", "3000"))

# Directories hidden from the file explorer.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".eggs",
}
MAX_TEXT_SIZE = 2 * 1024 * 1024  # 2 MB guard on read/write payloads
CHAT_TIMEOUT = 600.0  # agent loops can run a while; keep the proxy generous

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Strands IDE")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_target(rel_path: str) -> Path:
    """Resolve a user/agent-supplied path strictly inside the workspace."""
    if rel_path in ("", ".", "./"):
        return WORKSPACE_DIR
    candidate = (WORKSPACE_DIR / rel_path).resolve()
    try:
        candidate.relative_to(WORKSPACE_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes the workspace")
    return candidate


def _should_skip(entry: Path) -> bool:
    return entry.name in SKIP_DIRS or entry.name.endswith(".egg-info")


def _scan_tree(root: Path) -> list[dict]:
    """Return a flat list of {path, type, size} entries under root."""
    entries: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if any(_should_skip(Path(part)) for part in path.parts):
            continue
        try:
            rel = path.relative_to(WORKSPACE_DIR).as_posix()
        except ValueError:
            continue
        if path.is_dir():
            entries.append({"path": rel, "type": "dir", "size": None})
        elif path.is_file():
            entries.append({"path": rel, "type": "file", "size": path.stat().st_size})
    return entries


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class FileWriteRequest(BaseModel):
    path: str
    content: str


class ChatRequest(BaseModel):
    prompt: str


class RunRequest(BaseModel):
    command: str
    timeout: float = 120.0


# ---------------------------------------------------------------------------
# File APIs (used by the editor UI)
# ---------------------------------------------------------------------------
@app.get("/api/workspace")
def workspace_info() -> dict:
    return {
        "root": str(WORKSPACE_DIR),
        "agent_url": AGENT_URL,
    }


@app.get("/api/tree")
def tree() -> dict:
    return {
        "root": str(WORKSPACE_DIR),
        "entries": _scan_tree(WORKSPACE_DIR),
    }


@app.get("/api/file")
def read_file(path: str = Query(...)):
    target = _safe_target(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="not a file")
    if target.stat().st_size > MAX_TEXT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than {MAX_TEXT_SIZE} bytes; edit it locally",
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"path": path, "content": text, "size": target.stat().st_size}


@app.put("/api/file")
def write_file(req: FileWriteRequest) -> dict:
    if len(req.content.encode("utf-8")) > MAX_TEXT_SIZE:
        raise HTTPException(status_code=413, detail="content too large")
    target = _safe_target(req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    log.info("wrote %s (%d bytes)", target, len(req.content))
    return {"path": req.path, "size": target.stat().st_size}


@app.delete("/api/file")
def delete_file(path: str = Query(...)) -> dict:
    target = _safe_target(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if target.is_file():
        target.unlink()
    else:
        # Only allow deleting an empty-ish dir here to avoid surprises.
        try:
            target.rmdir()
        except OSError:
            raise HTTPException(
                status_code=400,
                detail="directory not empty; remove files individually",
            )
    return {"path": path, "deleted": True}


# ---------------------------------------------------------------------------
# Chat proxy -> local AgentCore runtime
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict:
    """Forward a prompt to the agent runtime's POST /invocations."""
    payload = {"prompt": req.prompt}
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
            resp = await client.post(
                f"{AGENT_URL}/invocations",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not reach the agent runtime at {AGENT_URL}. "
                "Start it with `python -m agent.agent` first."
            ),
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="agent runtime timed out")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"agent runtime returned {resp.status_code}: {resp.text[:500]}",
        )
    try:
        return resp.json()
    except ValueError:
        return {"text": resp.text, "tool_calls": [], "files_changed": []}


@app.get("/api/health/agent")
async def agent_health() -> dict:
    """Ping the agent runtime to report whether it is up."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{AGENT_URL}/ping")
        return {"reachable": r.status_code == 200, "status_code": r.status_code}
    except httpx.HTTPError:
        return {"reachable": False, "status_code": None}


# ---------------------------------------------------------------------------
# Run a command in the workspace (the app's "Run"/test panel)
# ---------------------------------------------------------------------------
@app.post("/api/run")
def run(req: RunRequest) -> dict:
    """Run a shell command in the workspace and capture its output."""
    timeout = min(float(req.timeout), 600.0)
    log.info("run: %s (timeout=%ss)", req.command, timeout)
    try:
        proc = subprocess.run(
            req.command,
            shell=True,
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return {
            "command": req.command,
            "exit_code": 124,
            "stdout": out,
            "stderr": f"\n[timed out after {timeout}s]",
        }
    except OSError as exc:
        return {
            "command": req.command,
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "command": req.command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    log.info("IDE: http://localhost:%d  (workspace: %s, agent: %s)",
             IDE_PORT, WORKSPACE_DIR, AGENT_URL)
    uvicorn.run(app, host="0.0.0.0", port=IDE_PORT, log_level="info")
