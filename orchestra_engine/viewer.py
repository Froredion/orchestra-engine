"""Every run, one page: the home folder that records runs, and the one viewer
process that serves them.

    ~/.orchestra-engine/            (ORCHESTRA_ENGINE_HOME overrides; 0700)
    ├── runs/<run-id>/         <- a run's channel, when it didn't name one
    ├── index/<run-id>.json    <- {"dir": ...}: every run's channel, wherever it is
    └── viewer.json            <- {pid, port, token} of the viewer now serving

A run's page used to live inside the run's own process: one tab and one port
per run, and a tab that went dead the moment its run ended. Five runs were five
tabs. Now each run only registers its channel here, and a single long-lived
viewer serves all of them on one page — started by the first run that needs
it, reused by every run after, and gone once nothing has run or looked for a
while.

The index is the only way a run's directory reaches the server. A request
names a run id, the id is checked against a strict pattern, and the directory
is read from that run's index file — never from the request — so no request
can point the server at a path.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import channel

HOME_ENV = "ORCHESTRA_ENGINE_HOME"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
VIEWER_FILE = "viewer.json"
MAX_RUNS_LISTED = 100

# The viewer exits after this long with no run in flight and no page open.
IDLE_EXIT_SECONDS = 600
# A page polls every few hundred ms; one heard from this recently is "open".
PAGE_OPEN_SECONDS = 10
# How long a run waits for a viewer it started to answer. Usually well under a
# second, but a busy machine has been seen to take 35s, and a run that gave up
# at 8s never opened the page at all.
VIEWER_START_SECONDS = 60


def home() -> Path:
    root = Path(os.environ.get(HOME_ENV) or Path.home() / ".orchestra-engine").expanduser().resolve()
    for d in (root, root / "runs", root / "index"):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return root


def new_run_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _write_private(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def register(run_id: str, channel_dir: Path) -> None:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"bad run id {run_id!r}")
    _write_private(home() / "index" / f"{run_id}.json", {"dir": str(channel_dir.resolve())})


def run_dir(run_id: str) -> Path | None:
    """A run's channel directory, from its index entry — or None."""
    if not RUN_ID_RE.match(run_id or ""):
        return None
    try:
        entry = json.loads((home() / "index" / f"{run_id}.json").read_text(encoding="utf-8"))
        return Path(entry["dir"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---- summaries: one line per run in the sidebar ------------------------------

_cache: dict[str, tuple[tuple[int, float], dict]] = {}


def summarize(events: list[dict]) -> dict:
    """What the sidebar shows for a run, from its events alone."""
    out = {"task": None, "pipeline": None, "started": None, "ended": None,
           "status": "starting", "escalations": False, "cost_usd": 0.0, "stage": None,
           "pid": None}
    for e in events:
        kind = e.get("event")
        if kind == "engine_started":
            out["started"], out["pid"] = e.get("ts"), e.get("pid")
        elif kind == "run_started":
            out["task"], out["pipeline"] = e.get("task"), e.get("pipeline")
            out["status"] = "running"
        elif kind == "stage_started":
            out["stage"] = e.get("stage")
        elif kind == "stage_finished":
            out["cost_usd"] += e.get("cost_usd") or 0
        elif kind == "run_complete":
            out["status"], out["ended"] = "finished", e.get("ts")
            out["escalations"] = bool(e.get("escalations"))
        elif kind == "engine_exited" and out["status"] != "finished":
            out["status"], out["ended"] = "stopped", e.get("ts")
    if out["status"] in ("starting", "running") and channel.pending_prompt(events):
        out["status"] = "waiting"
    return out


def list_runs() -> list[dict]:
    entries = sorted((home() / "index").glob("*.json"), key=lambda p: p.name, reverse=True)
    runs = []
    for entry in entries[:MAX_RUNS_LISTED]:
        run_id = entry.stem
        d = run_dir(run_id)
        if d is None:
            continue
        events_path = d / channel.EVENTS_FILE
        try:
            st = events_path.stat()
        except OSError:
            continue
        key = (st.st_size, st.st_mtime)
        cached = _cache.get(run_id)
        if cached and cached[0] == key:
            summary = dict(cached[1])
        else:
            summary = summarize(channel.read_events(d))
            _cache[run_id] = (key, dict(summary))
        # A run whose engine is gone without saying so crashed or was killed.
        if summary["status"] in ("starting", "running", "waiting") and summary["pid"] \
                and not channel._alive(summary["pid"]):
            summary["status"] = "stopped"
        summary["id"] = run_id
        summary.pop("pid", None)
        runs.append(summary)
    return runs


# ---- the one viewer ---------------------------------------------------------


def _read_viewer() -> dict | None:
    try:
        info = json.loads((home() / VIEWER_FILE).read_text(encoding="utf-8"))
        return info if {"pid", "port", "token"} <= set(info) else None
    except (OSError, ValueError, TypeError):
        return None


def _ping(info: dict) -> dict | None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/api/ping", headers={"X-Orchestra-Token": info["token"]}
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def viewer_url(info: dict, run_id: str | None = None) -> str:
    url = f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    return url + (f"#run={run_id}" if run_id else "")


def ensure_viewer(timeout: float = VIEWER_START_SECONDS) -> tuple[dict, bool]:
    """The running viewer, starting one if there's none. Returns its info and
    whether a page is already open on it (so the caller needn't open another)."""
    info = _read_viewer()
    if info and channel._alive(info["pid"]):
        pong = _ping(info)
        if pong and pong.get("ok"):
            return info, bool(pong.get("page_open"))
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-m", "orchestra_engine", "--serve-viewer"], **kwargs)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Whichever viewer answers, not necessarily the one just started: two
        # runs launched together each start one, one of them wins viewer.json,
        # and the other leaves on its own.
        info = _read_viewer()
        pong = _ping(info) if info else None
        if pong and pong.get("ok"):
            return info, bool(pong.get("page_open"))
        if proc.poll() is not None and not info:
            break
        time.sleep(0.1)
    raise RuntimeError("the live view didn't start (see `./orchestra --serve-viewer`)")


def serve_forever() -> None:
    """The viewer process: serve every indexed run until idle."""
    from . import webui

    root = home()
    server = webui.LiveView()
    _write_private(root / VIEWER_FILE, {
        "pid": os.getpid(), "port": server.server_address[1], "token": server.token,
    })
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    idle_since = time.monotonic()
    try:
        while True:
            time.sleep(5)
            # Two runs starting at once can each start a viewer; the one
            # viewer.json doesn't name is the spare, and leaves.
            current = _read_viewer()
            if not current or current.get("pid") != os.getpid():
                break
            busy = server.page_open() or any(
                r["status"] in ("starting", "running", "waiting") for r in list_runs()
            )
            if busy:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since > IDLE_EXIT_SECONDS:
                break
    finally:
        server.shutdown()
        info = _read_viewer()
        if info and info.get("pid") == os.getpid():
            (root / VIEWER_FILE).unlink(missing_ok=True)
