# FarmNotary live demo

A notebook that runs a tiny consensus-style experiment and notarizes it, so a researcher can see the claim card, the allowlist, a scoped re-run, and a packaging mismatch that is **not a science failure**.

The simulation stays off-chain. This notebook uses the dry-run backend: no calendars, no pin, no network.

## Run it

From the repo root, with the project venv:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install jupyter   # only if you want the interactive UI
.venv/bin/jupyter notebook docs/demo/farmnotary_live_demo.ipynb
```

Or open the notebook in Cursor / VS Code and run all cells. Scratch output lands in `docs/demo/_live/` (gitignored).

`tests/test_demo.py` executes the same walkthrough without Jupyter, and also execs the notebook’s code cells so the live demo cannot drift from the library.

## What it will not claim

A passing card is not scientific correctness. Pending OpenTimestamps is not L0. A self-run receipt is not independently reproduced. Cross-hardware bitwise identity is not a claim.
