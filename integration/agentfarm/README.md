# AgentFarm integration patch

The Cloud Agent's credentials cannot push to `Dooders/AgentFarm`, so the
AgentFarm side of the provenance work ships here as a git patch. It applies
on top of AgentFarm PR #985's branch
(`cursor/political-consensus-experiment-2c46`):

```bash
cd AgentFarm
git checkout cursor/political-consensus-experiment-2c46
git am path/to/0001-Verifiable-provenance-for-the-consensus-experiment-r.patch
```

FarmNotary's hardware matrix
(`.github/workflows/reproduce-consensus-matrix.yml`) does **not** apply this
patch at runtime. It checkouts AgentFarm at a reviewed SHA
(`c98a476eaf1f9d3100383787fa34ec352e896dff`) that already has the portable
command and `run_config.json` work.

Depend on the 0.2 line (`farm-notary>=0.2,<0.3`), not `>=0.1,<0.2`.
Install from PyPI: `pip install "farm-notary[ots]"`.

## What the patch contains

- **Reproducible reports**: `run_experiment.py` records the run command with a
  `{run_dir}` placeholder instead of the literal output path, so `REPORT.md`
  is byte-identical across re-runs into different directories (this was the
  only non-reproducing artifact) and the record is directly usable by
  `farm-notary reproduce`.
- **No more manifest collision**: the experiment's own metadata file is now
  `run_config.json`; `manifest.json` is reserved for the FarmNotary manifest.
- **Verified derivations**: `python run_experiment.py verify-report --results
  <dir>` recomputes `summary.csv`, `allocation_means.csv`, and `REPORT.md`
  from `trials.csv` and byte-compares them (using pandas
  `float_precision="round_trip"`, since the default parser is off by one ulp).
- **FarmNotary adapter** (`farm/provenance/`): `notarize()`, `verify()`, and
  `reproduce()` against the FarmNotary 0.2 API (`publish_profile="consensus"`,
  dry-run until a backend is passed). `notarize()` picks up the command and
  config from `run_config.json` automatically. farm-notary stays an optional
  dependency.
- **CI machine reproduction** (`.github/workflows/reproduce-consensus.yml` on
  the AgentFarm side): on every change to the experiment, CI runs it, verifies
  the derived artifacts, notarizes, re-runs the recorded command, and fails
  unless every artifact is byte-identical. That job is x86-64 Linux only.
- **FarmNotary hardware matrix**
  (`.github/workflows/reproduce-consensus-matrix.yml` in this repo): produce
  on x86-64 Linux, reproduce on Linux x86 / Linux ARM / macOS ARM. The tool
  may emit *byte-identical on x86-64 Linux in a pinned environment* and
  nothing wider until ARM receipts exist. See `docs/CLAIMS.md`.
- **Tests**: same-seed bitwise reproducibility of all outputs, portable
  command recording, derivation verification (including tamper detection),
  and adapter tests that skip when farm-notary is not installed.

All of it was executed against the real experiment: 8/8 artifacts reproduced
bitwise with no exclusions on the original lab machine. The matrix smoke cell
(12×50, seed 0) is the demonstrated CI cell — see `docs/CLAIMS.md`.
