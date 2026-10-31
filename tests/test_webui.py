"""The live view: one viewer for every run — who can reach it, which runs it
can see, and that answers go through the channel's own checks."""

import http.client
import json
import os
import threading

import pytest

from orchestra_engine import channel, viewer, webui


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(viewer.HOME_ENV, str(tmp_path / "home"))
    viewer._cache.clear()
    return viewer.home()


@pytest.fixture
def run(tmp_path):
    """One registered run with an open channel."""
    ch = channel.open_channel(tmp_path / "chan", forbidden={})
    viewer.register("r1", ch.dir)
    yield ch
    channel.close_channel()


@pytest.fixture
def view():
    v = webui.LiveView()
    threading.Thread(target=v.serve_forever, daemon=True).start()
    yield v
    v.shutdown()


def _req(v, method, path, body=None, headers=None, host=None):
    port = v.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    headers = dict(r.getheaders())
    conn.close()
    return r.status, data, headers


def _auth(v):
    return {webui.TOKEN_HEADER: v.token}


def _get(v, path):
    status, data, _ = _req(v, "GET", path, headers=_auth(v))
    return status, json.loads(data)


def _post(v, payload, headers=None):
    h = {"Content-Type": "application/json", **_auth(v), **(headers or {})}
    return _req(v, "POST", "/api/answer", body=json.dumps(payload), headers=h)[:2]


def _gate(ch):
    ch.emit("prompt", id="p1", kind="gate", mode="select", message="Next stage",
            options=[{"label": "run", "value": "run"}, {"label": "skip", "value": "skip"}],
            allow_back=False)


# ---- who can reach it -------------------------------------------------------


def test_page_needs_the_token(view):
    assert _req(view, "GET", "/")[0] == 403
    assert _req(view, "GET", "/?t=wrong")[0] == 403
    status, body, headers = _req(view, "GET", f"/?t={view.token}")
    assert status == 200 and b"<title>Loop</title>" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_page_carries_its_markdown_libraries_inline(view):
    # The CSP admits no script host, so marked and DOMPurify must be in the page itself.
    body = _req(view, "GET", f"/?t={view.token}")[1]
    assert b"<!--VENDOR-->" not in body
    assert b"marked v18" in body and b"DOMPurify 3" in body
    assert body.index(b"DOMPurify 3") < body.index(b"const TOKEN =")


def test_foreign_host_is_refused_even_with_the_token(view):
    # What a DNS-rebinding page would send: the right port, someone else's name.
    assert _req(view, "GET", f"/?t={view.token}", host="evil.example:80")[0] == 403
    assert _req(view, "GET", "/api/runs", headers=_auth(view), host="evil.example")[0] == 403


def test_every_api_route_needs_the_token(view, run):
    for path in ("/api/ping", "/api/runs", "/api/events?run=r1"):
        assert _req(view, "GET", path)[0] == 403


# ---- runs and their events --------------------------------------------------


def test_runs_lists_every_registered_run(view, run, tmp_path):
    run.emit("run_started", task="fix it", pipeline="bugfix", run_dir="/r", stages=["a"])
    other = tmp_path / "other"
    other.mkdir()
    (other / channel.EVENTS_FILE).write_text(
        json.dumps({"event": "run_complete", "ts": 1, "run_dir": "/x"}) + "\n")
    viewer.register("r0", other)
    status, body = _get(view, "/api/runs")
    by_id = {r["id"]: r for r in body["runs"]}
    assert status == 200
    assert by_id["r1"]["task"] == "fix it" and by_id["r1"]["status"] == "running"
    assert by_id["r0"]["status"] == "finished"


def test_events_are_per_run_from_an_offset(view, run):
    run.emit("stage_started", stage="plan")
    status, body = _get(view, "/api/events?run=r1&from=1")
    assert status == 200
    assert [e["event"] for e in body["events"]] == ["stage_started"]
    assert body["next"] == 2


def test_a_run_is_named_by_id_never_by_path(view, run):
    for bad in ("../r1", "..%2Fr1", "/etc", "r1/../r1", "nope"):
        assert _get(view, f"/api/events?run={bad}")[0] == 404


def test_ping_reports_whether_a_page_is_open(view):
    assert _get(view, "/api/ping")[1] == {"ok": True, "page_open": False}
    _get(view, "/api/runs")  # what an open page does every second or so
    assert _get(view, "/api/ping")[1]["page_open"] is True


# ---- answers ----------------------------------------------------------------


def test_answer_goes_through_the_channel_checks(view, run):
    _gate(run)
    status, body = _post(view, {"run": "r1", "id": "p2", "value": "1"})
    assert status == 409 and b"not the waiting prompt" in body
    assert _post(view, {"run": "r1", "id": "p1", "value": "9"})[0] == 409
    assert _post(view, {"run": "nope", "id": "p1", "value": "1"})[0] == 404
    assert _post(view, {"run": "r1", "id": "p1", "value": "2"})[0] == 200
    assert (run.answers / "p1").read_text() == "2"


def test_answer_must_be_json_with_the_token(view, run):
    _gate(run)
    body = json.dumps({"run": "r1", "id": "p1", "value": "1"})
    # A cross-site form post: no token, a form content type.
    assert _req(view, "POST", "/api/answer", body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 403
    assert _req(view, "POST", "/api/answer", body=body,
                headers={"Content-Type": "text/plain", **_auth(view)})[0] == 415
    assert not (run.answers / "p1").exists()


def test_oversized_answer_is_refused(view):
    status, _ = _post(view, {"run": "r1", "id": "p1", "value": "x" * (webui.MAX_BODY_BYTES + 1)})
    assert status == 413


# ---- the index and summaries ------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Windows has ACLs, not POSIX mode bits")
def test_home_and_index_are_private(home, run):
    assert home.stat().st_mode & 0o077 == 0
    assert (home / "index" / "r1.json").stat().st_mode & 0o077 == 0


def test_register_refuses_a_bad_id(tmp_path):
    with pytest.raises(ValueError):
        viewer.register("../x", tmp_path)


def test_summary_statuses():
    s = viewer.summarize
    started = [{"event": "engine_started", "pid": 1, "ts": 1},
               {"event": "run_started", "task": "t", "pipeline": "p"}]
    assert s(started)["status"] == "running"
    prompt = {"event": "prompt", "id": "p1"}
    assert s(started + [prompt])["status"] == "waiting"
    assert s(started + [prompt, {"event": "answered", "id": "p1"}])["status"] == "running"
    done = started + [{"event": "stage_finished", "cost_usd": 0.5},
                      {"event": "run_complete", "ts": 9, "escalations": True},
                      {"event": "engine_exited", "ts": 10}]
    assert s(done)["status"] == "finished" and s(done)["escalations"] and s(done)["cost_usd"] == 0.5
    assert s(started + [{"event": "engine_exited", "ts": 3}])["status"] == "stopped"


def test_a_run_whose_engine_died_reads_as_stopped(run):
    run.emit("run_started", task="t", pipeline="p")
    with open(run.events, "a") as f:  # an engine pid that can't exist
        f.write(json.dumps({"event": "engine_started", "pid": 999999999}) + "\n")
    viewer._cache.clear()
    assert viewer.list_runs()[0]["status"] == "stopped"


# ---- the one viewer ---------------------------------------------------------


def test_ensure_viewer_starts_one_and_reuses_it():
    info, page_open = viewer.ensure_viewer()
    try:
        assert page_open is False
        again, _ = viewer.ensure_viewer()
        assert again["pid"] == info["pid"] and again["port"] == info["port"]
        if os.name != "nt":  # Windows has ACLs, not POSIX mode bits
            assert (viewer.home() / viewer.VIEWER_FILE).stat().st_mode & 0o077 == 0
    finally:
        import signal
        os.kill(info["pid"], signal.SIGTERM)


def test_two_runs_starting_together_share_one_viewer():
    import os
    import signal
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.map(lambda _: viewer.ensure_viewer()[0], range(2))
    try:
        assert a["port"] == b["port"]
    finally:
        for pid in {a["pid"], b["pid"]}:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_a_driven_run_opens_the_page_when_the_viewer_answers_late(monkeypatch):
    # A run another process drives must not give up on a slow viewer: it
    # returns at once and opens the page whenever the viewer is finally up.
    import webbrowser
    from orchestra_engine import main

    ready, opened, waits = threading.Event(), threading.Event(), []

    def slow_viewer(timeout):
        waits.append(timeout)
        ready.wait(5)
        return {"pid": 1, "port": 1, "token": "t"}, False

    monkeypatch.setattr(viewer, "ensure_viewer", slow_viewer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.set())
    assert main.open_view("r1", background=True) is True
    assert not opened.is_set()
    ready.set()
    assert opened.wait(5)
    assert waits == [main.BACKGROUND_VIEWER_SECONDS]


def test_the_page_opens_in_its_own_window_when_one_can(monkeypatch):
    # A tab gets closed along with the rest of the browser; the window is the
    # default, and the tab only the fallback.
    import webbrowser
    from orchestra_engine import main, window

    windows, tabs = [], []
    monkeypatch.setattr(viewer, "ensure_viewer", lambda timeout: ({"pid": 1, "port": 1, "token": "t"}, False))
    monkeypatch.setattr(window, "open_window", lambda run_id=None: windows.append(run_id) or True)
    monkeypatch.setattr(webbrowser, "open", tabs.append)
    assert main.open_view("r1") is True
    assert windows == ["r1"] and tabs == []

    monkeypatch.setattr(window, "open_window", lambda run_id=None: False)
    assert main.open_view("r2") is True
    assert tabs == ["http://127.0.0.1:1/?t=t#run=r2"]


def test_no_window_is_opened_over_a_page_already_open(monkeypatch):
    import webbrowser
    from orchestra_engine import main, window

    opened = []
    monkeypatch.setattr(viewer, "ensure_viewer", lambda timeout: ({"pid": 1, "port": 1, "token": "t"}, True))
    monkeypatch.setattr(window, "open_window", lambda run_id=None: opened.append(run_id) or True)
    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert main.open_view("r1") is True
    assert opened == []


def test_the_window_is_never_handed_the_token_on_its_command_line(monkeypatch):
    # argv is readable by every process on the machine; the token isn't.
    import subprocess
    from orchestra_engine import window

    monkeypatch.undo()  # the conftest stub stands in for open_window itself
    cmds = []
    monkeypatch.setattr(window, "available", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: cmds.append(cmd))
    # Plain Python, and (macOS) from the window's own app bundle.
    monkeypatch.setattr(window, "_mac_app", lambda: None)
    assert window.open_window("r1") is True
    assert cmds[-1][-2:] == ["--app-window", "r1"]
    monkeypatch.setattr(window.sys, "platform", "darwin")
    monkeypatch.setattr(window, "_mac_app", lambda: "/x/Orchestra Engine.app/Contents/MacOS/Orchestra Engine")
    assert window.open_window("r1") is True
    assert cmds[-1][0].endswith("Orchestra Engine") and cmds[-1][-1] == "r1"
    assert not any("t=" in part for cmd in cmds for part in cmd)
