# BITS Conversion Tool

Existing Python/PyMuPDF converter with a PySide6 desktop GUI, BITS 1.0 XML,
editable zones, image assets, validation reports and an HTML review preview.

Verified against the two supplied chapter PDFs (50 pages) and synthetic fixtures.
The current suite has 60 passing tests. Figure clipping, supported equation
recognition and automatic QC run during normal conversion. See
[the repair report](docs/repair-report.md) for measured results and limitations.

## Install and run

Python 3.9+ (tested with 3.9.25):

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bits_cli.py --config config/portable.yaml status
python bits_cli.py --config config/portable.yaml convert --no-resume /path/to/book.pdf --out output
python bits_cli.py --config config/portable.yaml validate output/book/book.xml
python preview_html.py output/book/book.xml --no-open
# Desktop GUI (requires Qt/display system dependencies):
python run_gui.py
```

Use the actual output directory/XML name printed by conversion (ISBN takes
precedence over the input filename). `config/portable.yaml` overrides the
saved Windows-specific paths. The GUI continues to load `config/config.yaml`;
set its paths in Settings for your machine. OCR requires a separate Tesseract
installation and the configured `spa` / `eng` language data, or the existing
optional Paddle engine. No new Python dependency was added.

The preview copies its bundled SIL OFL math font into `preview-assets/`.
Keep that directory and `images/` beside the HTML. A browser supporting native
MathML is required. XML uses the existing BITS/MathML/XLink conventions;
the repository's validator loads the bundled DTD independently of the
serialized legacy DOCTYPE system identifier.

Physical page reading order is now the default placement policy. Set
`placement.policy: learned` explicitly to retain citation-based float relocation.
Use `--no-resume` for the first conversion after upgrading; the extraction
cache version and tool version have changed. Saved zones remain supported,
but regenerating from the PDF is necessary to repair an old extraction.

## Tests and reproducible artifacts

From the repository root (PowerShell: set `$env:PYTHONPATH="src;."` first):

```sh
PYTHONPATH=src:. python -m unittest discover -s tests -v
PYTHONPATH=src:. python -m unittest discover -s src/bits_tool -p 'test_*.py' -v
python scripts/regression_demo.py --out /path/to/synthetic-verification
```

The demo produces a synthetic input PDF, page renders, converted XML, images,
HTML preview, diagnostics and `result.json`. It uses generated fixtures. An ambiguous equation deliberately generates a
review warning and an intact source crop.

## Diagnostics

`intermediate/document_structure.json` records page/region bounds, classification,
reading-order predecessors, figure/caption associations, math reconstruction
reasons and confidence. Development mode adds individual line records.
Extraction cache records also retain span bounds and real glyph baselines.
QA reports expose missing artwork, unsupported math and page failures.


## Supplied chapter verification

Put the two original `*_ch009.pdf` and `*_ch007.pdf` files in a local directory.
They are external fixtures, not bundled in this repository. The complete runs
use separate output roots because both chapters carry the same ISBN:

```sh
python scripts/verify_chapters.py --pdf-root /path/to/pdfs --out /path/to/chapter-verification
BITS_CHAPTER_RESULTS=/path/to/chapter-verification PYTHONPATH=src:. python -m unittest discover -s tests -v
PYTHONPATH=src:. python -m unittest discover -s src/bits_tool -p 'test_*.py' -v
```

PowerShell: set `$env:BITS_CHAPTER_RESULTS` and `$env:PYTHONPATH="src;."` before
the test command. Without the external results, the seventeen source-specific
tests explicitly skip; they are not reported as successful source verification.
The runner records source and PDF hashes, full results, representative page
renders, XML/HTML/assets, and per-page diagnostic geometry. It uses PNG at
144 dpi for review; the application's normal output settings remain configurable.
The cached extraction version is now 19. See `docs/repair-report.md` for actual
results and the remaining cases requiring review. Passing XML validation is
not proof of complete source fidelity.

## Automatic recognition and QC (version 1.2.0)

Every conversion writes `qa/recognition_qc.html` and `qa/recognition_qc.json`.
The HTML report links source crops to converted regions; JSON records pages,
bounds, output IDs, checks, reasons, and PASS/REVIEW/FAILED status. The main
conversion report links this report. Generate the preview with `preview_html.py`
to follow the converted-region links.

- PDF painting state supplies image clip bounds, including nested clips and
  transparency masks. This uses the existing PyMuPDF dependency (tested 1.26.5).
  Unavailable bindings or unmatched image occurrences trigger review.
- Vector arrow shafts/heads, glyph positions and fraction rules support reaction
  labels and chained fractions. Unsupported structures retain one source crop.
- Uncaptioned atom/bond drawings become intact SVG regions with glyph outlines.
  Recognition does not assign molecular identity, bond order or stereochemistry;
  those interpretations are explicitly queued for human review.
- QC checks MathML namespaces, token content and child counts, retained geometry,
  image clipping and figure/body overlap. Existing DTD/assets/IDs/content gates
  still apply. PASS means those checks passed, not universal source fidelity.

To make unresolved recognition fail conversion, set in your YAML configuration:

```yaml
qa:
  fail_on_recognition_review: true
```

Default `false` preserves the existing PASS WITH WARNINGS workflow. Unsupported
math remains visible and reviewable; it is never declared reconstructed MathML.
No recognition service, API key, or new package is required.
