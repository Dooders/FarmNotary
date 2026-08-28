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
  `reproduce()` against the current FarmNotary API; `notarize()` picks up the
  command and config from `run_config.json` automatically. farm-notary stays
  an optional dependency.
- **CI machine reproduction** (`.github/workflows/reproduce-consensus.yml`):
  on every change to the experiment, CI runs it, verifies the derived
  artifacts, notarizes, re-runs the recorded command, and fails unless every
  artifact is byte-identical.
- **Tests**: same-seed bitwise reproducibility of all outputs, portable
  command recording, derivation verification (including tamper detection),
  and adapter tests that skip when farm-notary is not installed.

All of it was executed against the real experiment in this session:
8/8 artifacts reproduced bitwise with no exclusions.
