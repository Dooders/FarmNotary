# FarmNotary slide decks

Two short talks. Typefaces are vendored under `fonts/` (Fraunces, Source Serif 4, IBM Plex Mono — SIL OFL via Google Fonts) so either deck presents offline.

| Deck | HTML | PDF | For |
|---|---|---|---|
| Intro | [`index.html`](index.html) | [`farmnotary.pdf`](farmnotary.pdf) | Researchers and labs: what a notary is, what it will not claim |
| Consensus walkthrough | [`consensus.html`](consensus.html) | [`consensus.pdf`](consensus.pdf) | A room that wants to see one toy run: allowlist, card, re-run, packaging bug |

The walkthrough is the worked example. It runs `docs/demo/experiment.py` (12 voters, seed 0, profile `consensus`) with the **dry-run** backend. Pair it with [`docs/demo/farmnotary_live_demo.ipynb`](../demo/farmnotary_live_demo.ipynb) if a laptop is live.

Both talks stay inside the claim card. If either starts promising scientific correctness, independently reproduced, or cross-hardware bitwise identity, `tests/test_docs.py` fails.

## Keys

| Key | Action |
|---|---|
| click right / `→` `space` | Next |
| click left / `←` | Previous |
| `Home` / `End` | First / last |
| `F` | Fullscreen |
| `N` | Speaker notes |
| `?` | Shortcuts |
| `P` | Print / Save as PDF |

## PDF

Regenerate either PDF with Chrome or Chromium:

```bash
python3 docs/slides/export_pdf.py
```

That writes a 13.333in × 7.5in (16:9) file with backgrounds and no header/footer. Chrome → Print → Save as PDF still works: landscape, **Background graphics** on.
