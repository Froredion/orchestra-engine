"""Execution layer: runs pipelines as chains of agent sessions.

Module map:
  session.py   one SDK agent session (the only engine module touching the SDK)
  shapes.py    how a stage's sessions are arranged: repeat chains, fan-out
  executor.py  run_loop — walks the stages, threads handoffs
  runlog.py    JSON run log
"""

from .executor import run_loop
from .session import DEFAULT_SETTING_SOURCES, run_stage

__all__ = ["DEFAULT_SETTING_SOURCES", "run_loop", "run_stage"]
