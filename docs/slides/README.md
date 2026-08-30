# FarmNotary intro deck

A short talk for solo researchers and labs: what a research notary is, what FarmNotary will and will not claim, and how to hash one run this afternoon.

- **PDF (16:9, 18 pages):** [`farmnotary.pdf`](farmnotary.pdf)
- **Live deck:** open [`index.html`](index.html) in a browser. Typefaces are vendored under `fonts/` (Fraunces, Source Serif 4, IBM Plex Mono — SIL OFL via Google Fonts) so the talk presents offline.

The talk is four acts: **Why**, **Record**, **Evidence**, **Start**.

| Key | Action |
|---|---|
| click right / `→` `space` | Next |
| click left / `←` | Previous |
| `Home` / `End` | First / last |
| `F` | Fullscreen |
| `N` | Speaker notes |
| `?` | Shortcuts |
| `P` | Print / Save as PDF |

A checked-in PDF is regenerated with Chrome or Chromium:

```bash
python3 docs/slides/export_pdf.py
```

That writes a 13.333in × 7.5in (16:9) file with backgrounds and no header/footer. Chrome → Print → Save as PDF still works: landscape, **Background graphics** on.

The deck is locked to the claim card in `tests/test_docs.py`. If the talk starts promising scientific correctness, independently reproduced, or cross-hardware bitwise identity, the test fails.
