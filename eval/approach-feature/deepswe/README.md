# Benchmarking `approach+feature` on DeepSWE

This arm chains two pipelines in one trial. `approach` decides how to build the
ticket and publishes a `SPEC.md`. `feature` then builds from that spec, and the
ticket wins wherever the two disagree. It reuses the `feature` arm's adapter
rather than copying it, so setup, auth, flags and gotchas are all in
[`../../feature/deepswe/README.md`](../../feature/deepswe/README.md). This file
only covers what is different.

## Running it

Same as the `feature` arm, with `pipeline=approach+feature`:

```sh
pier run -p deep-swe/tasks/obsidian-linter-link-format-conversion \
  --agent-import-path eval.feature.deepswe.pier_agent:OrchestraEngineAgent \
  -m anthropic/claude-opus-5-5 \
  --agent-timeout-multiplier 2 \
  --ak pipeline=approach+feature \
  --ak permission_mode=bypassPermissions \
  --ak dangerously_skip_permissions=true \
  --ak agent_timeout_sec=21000 \
  --ae CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  -k 1 -n 1 -o jobs/obsidian-approach-feature
```

## How the chain behaves

- `approach` gets at most a third of the remaining `agent_timeout_sec`
  (`CHAIN_FIRST_SHARE`), and `feature` gets everything after that. Only the last
  pipeline writes the graded diff, so a slow upstream can't starve it.
- `--docs-dir` is set to `/logs/loop-run/docs`, so the published `SPEC.md`,
  `DECISION.md` and `REUSE.md` come home with the trial logs under
  `agent/loop-run/docs/<slug>/`.
- If `approach` fails or declines (no SPEC), `feature` runs on the bare ticket
  and the trial is still graded. `orchestra-engine-stdout.log` shows both pipelines'
  output, each under its own `===== <pipeline> =====` header.

## Results

| Task | Findings |
|---|---|
| `obsidian-linter-link-format-conversion` | [`FINDINGS-obsidian-linter.md`](FINDINGS-obsidian-linter.md): 1/1 on a task Opus 5 passes 1 in 20. A single-session Opus 5.5 baseline scored 59/60, failing the exact case `approach` had flagged |
| `meriyah-explicit-resource-declarations` | [`FINDINGS-meriyah.md`](FINDINGS-meriyah.md): 0/1, 47/49. It hit the suspect `using of of` test, and also a switch-case restriction the spec invented |
| `superjson-error-stack-serialization` | [`FINDINGS-superjson.md`](FINDINGS-superjson.md): 0/1, 79/80. `compare` merged two readings of an ambiguous rule into a union, and the hidden test asserts one of them. A single-session baseline also scored 79/80, failing a different test. Two prompt variants re-run: 78/80 and 79/80 |
