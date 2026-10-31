# Benchmark harnesses

Code that *measures* the engine, not code the engine ships — kept out of the
installed package (`pyproject.toml`'s `packages.find` includes only
`orchestra_engine*`).

## Layout: `eval/<pipeline>/<benchmark>/`

| | |
|---|---|
| [`feature/deepswe/`](feature/deepswe/) | DeepSWE via Pier, `--ak pipeline=feature` |
| [`approach-feature/deepswe/`](approach-feature/deepswe/) | DeepSWE via Pier, `--ak pipeline=approach+feature`, using the adapter in `feature/deepswe/` |

Pipeline comes first because it is the variable under test. The engine is not
one agent, it is whatever pipeline you point at a task, and a benchmark measures
the pair — a `feature`-pipeline number on DeepSWE says nothing about how
`bugfix` would do on the same tasks. Nesting keeps that visible in the path
instead of buried in an `--ak` flag, and gives the same benchmark room to appear
under a second pipeline without either run pretending to be *the* result.

Each leaf is a package with its own `README.md` covering setup, the two arms,
and how to read the output.
