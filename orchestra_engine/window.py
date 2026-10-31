"""The live view in a window of its own, not a browser tab.

A tab sits among everything else in the browser and gets closed with the rest.
This puts the same page in a native window (pywebview: WebKit on macOS,
WebView2 on Windows) with its own Dock / taskbar entry. It runs as a detached
process of its own, because a GUI loop wants the main thread and a run's main
thread is busy running the run — and so the window outlives the run that
opened it, as a tab did.

pywebview is installed on macOS and Windows only; elsewhere (and wherever the
window can't start) the page opens in the browser as before.

On macOS the Dock and Cmd+Tab name an app after the bundle its executable sits
in, and Python's is Python.app — so a window run by the venv's Python is
"Python". The window instead runs from a small bundle of its own in the engine
home, around a copy of Python.app's launcher binary (33 KB; it loads the same
framework by absolute path), and adds the venv's site-packages itself, since a
copied interpreter doesn't know it belongs to a venv.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import struct
import shutil
import subprocess
import sys
import sysconfig
import webbrowser
from pathlib import Path

from . import viewer

TITLE = "Orchestra Engine"
BUNDLE_ID = "com.orchestra-engine.liveview"
ICON = Path(__file__).with_name("icon.png")  # 1024x1024, on the macOS icon grid

# Run by the bundle's interpreter: argv[1] is the venv's site-packages.
_BOOT = (
    "import site, sys; site.addsitedir(sys.argv[1]); "
    "from orchestra_engine import window; window.run_window(sys.argv[2] if len(sys.argv) > 2 else None)"
)


def available() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


def open_window(run_id: str | None = None) -> bool:
    """Start the window process. False if there's no window to start, so the
    caller opens a tab instead. The URL isn't passed: it carries the viewer's
    token, and a command line is readable by every process on the machine."""
    if not available():
        return False
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    cmd = [sys.executable, "-m", "orchestra_engine", "--app-window"]
    app = _mac_app() if sys.platform == "darwin" else None
    if app is not None:
        cmd = [str(app), "-c", _BOOT, sysconfig.get_paths()["purelib"]]
    try:
        subprocess.Popen(cmd + ([run_id] if run_id else []), **kwargs)
    except OSError:
        return False
    return True


def run_window(run_id: str | None = None) -> None:
    """The window process: show the running viewer's page until closed."""
    info = viewer._read_viewer()
    if not info:
        sys.exit("no live view is running (`./orchestra --view` starts one)")
    url = viewer.viewer_url(info, run_id)
    try:
        import webview

        _name_app()
        webview.create_window(TITLE, url, width=1280, height=860, min_size=(640, 480))
        webview.start()
    except Exception:
        # No usable GUI backend after all: the page still has to show somewhere.
        webbrowser.open(url)


def _mac_app() -> Path | None:
    """The bundle's executable, built (or rebuilt, when Python changed) in the
    engine home. None if it can't be: the window then runs as Python."""
    src = Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    if not src.is_file():
        return None  # not a framework build: no launcher to copy
    contents = viewer.home() / f"{TITLE}.app" / "Contents"
    exe = contents / "MacOS" / TITLE
    plist = {
        "CFBundleName": TITLE,
        "CFBundleDisplayName": TITLE,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": exe.name,
        "CFBundlePackageType": "APPL",
        "NSHighResolutionCapable": True,
        # What it was built from, so a Python upgrade or a new icon rebuilds it.
        "OrchestraEngineSource": str(src),
    }
    try:
        icon = ICON.read_bytes()
        plist["CFBundleIconFile"] = "AppIcon"
        plist["OrchestraEngineIcon"] = hashlib.sha256(icon).hexdigest()[:16]
    except OSError:
        icon = None  # a generic icon is no reason to go without the name
    try:
        current = plistlib.loads((contents / "Info.plist").read_bytes())
    except (OSError, ValueError):
        current = None
    if current == plist and exe.is_file():
        return exe
    try:
        exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, exe)
        if icon is not None:
            (contents / "Resources").mkdir(exist_ok=True)
            (contents / "Resources" / "AppIcon.icns").write_bytes(_icns(icon))
        (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
        # The Dock caches a bundle's icon; a newer mtime makes it look again.
        os.utime(contents.parent)
    except OSError:
        return None
    return exe


def _icns(png: bytes) -> bytes:
    """An .icns holding one 1024px PNG ('ic10'), which macOS scales for every
    size it shows — no iconutil, no iconset of a dozen files."""
    entry = b"ic10" + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(entry)) + entry


def _name_app() -> None:
    """On macOS, call the app Orchestra Engine in the menu bar, not Python —
    a fallback for when it runs without its bundle."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle

        bundle_info = NSBundle.mainBundle().infoDictionary()
        if bundle_info is not None:
            bundle_info["CFBundleName"] = TITLE
    except Exception:
        pass
