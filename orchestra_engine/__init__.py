"""Orchestra Engine: chain agent sessions, handing each one's summary and
session ID to the next.

The engine is pipeline-agnostic — a pipeline is any folder of numbered prompt
files (see orchestra_engine/pipeline.py). Several ship built-in under
orchestra_engine/pipelines/ (bugfix, feature, research, …); `./orchestra --list`
describes the live set.
"""

__version__ = "0.2.0"
