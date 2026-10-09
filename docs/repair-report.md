# BITS conversion repair and automatic QC

## Inputs and result

Source: connected repository at `6d8a2737a4f271e623a2f6d258a602f433aeea0d`,
with the supplied chapter 9 (21 pages) and chapter 7 (29 pages) PDFs. No separate
source ZIP or previous failing XML was supplied. Initial synthetic-only results
were superseded by actual source-PDF conversion and representative comparison.

Both chapters: **PASS WITH WARNINGS**, valid BITS DTD, no failed pages, duplicate
IDs, broken emitted links, missing assets, or unreadable assets. **60 tests pass**
(52 pipeline/geometry/actual-output tests plus 8 equation tests; no skips).

The reported Figure 7-2 clipping defect and three equation-image fallbacks are
resolved on these PDFs. Uncaptioned chemistry is detected and preserved as SVG;
its chemical interpretation remains explicitly marked REVIEW. Complete semantic
fidelity across arbitrary PDFs or every region of all 50 pages is not certified.

## Architecture inspected

- `bits_cli.py` and `run_gui.py` / `ui/` are CLI and PySide6 entry points.
- `services.py`, `batch_processor.py`, `converter.py` orchestrate conversion.
- `pdf_loader.py`, `text_extractor.py`, `ocr_engine.py` handle PDFs, span/bounding
  box extraction, raster objects, vector drawings, caches and optional OCR.
- `layout_analyzer.py`, `reading_order.py`, figure/caption/table/sidebar/footnote
  detectors classify regions before `structure_builder.py` creates the Node tree.
- `paragraph_detector.py` retains inline styles and reconstructs paragraphs.
- `zoning.py` saves editable ownership of extracted lines and rebuilds edits.
- `placement_engine.py`, IDs/target registry/citation/xref modules finalize links.
- `bits_generator.py` / `xml_generator.py` serialize BITS with MathML and XLink.
- Bundled BITS 1.0 DTD, `validators.py` and the converter quality gate check output.
- `preview_html.py` renders an HTML review aid; there is no separate XHTML/EPUB
  publication pipeline or EPUBCheck configuration in this checkout.
- Requirements already include PyMuPDF, lxml, PyYAML, PySide6, Pillow and optional
  OCR bindings. No repository knowledge-base, AGENTS.md or test selector existed.

## Confirmed causes and repairs

1. **Column rejection:** reading order required every column to occupy 30% of
   page width, rejecting narrow and 4-column layouts. A column containing only
   one figure could not establish a gutter. Removed that width gate; regions
   count as column evidence. Gutter partitioning and spanning bands now include
   a recursive whitespace partition for changing layouts. Diagnostics retain
   predecessor links, coordinates, column assignments and stable ordering.
2. **Relocation after analysis:** the saved/default `learned` placement policy
   moved figures to their first citation. Default is now `physical`; explicitly
   selected learned placement remains available for prior vendor conventions.
3. **Text loss/duplication:** no native overprint suppression existed; OCR
   replaced all native lines. Identical coincident spans are now deduplicated,
   OCR/native overlap is suppressed geometrically, and native text is retained.
4. **Overbroad furniture removal:** isolated small boundary lines and matching
   typography could be removed without corroborated repetition. Removal now
   requires repeated position/style plus margin separation; numeric folios
   require page progression. Unique boundary content stays in the body.
5. **Figure selection/crops:** caption candidates were expanded across inferred
   columns; text-art fallback scanned unrelated columns. First-page, trim-edge,
   size and aspect-ratio rules also discarded potential figures. Those broad
   filters were removed, caption alignment narrowed, and source crop export
   no longer erases text, whitens backgrounds or trims pixels by default.
   Unverified guessed crops are no longer emitted for missing artwork.
6. **Equation classification:** short identifiers/italic fragments and an
   eight-em collection gap could consume unrelated lines across columns.
   Equations are now spatial regions before reading order, constrained by
   columns/prose barriers, relation operators, real fraction rules and nearby
   vector evidence. Uncertain structures retain a single crop and review flag.
7. **False fractions and scripts:** two overlapping baselines were assumed to
   be a fraction; left-hand sides could become numerators. A fraction now needs
   a visible rule and appropriately placed components. Real glyph origins are
   retained because MuPDF's synthetic spaces can carry misleading baselines.
   Explicit/spatial scripts and arrow labels produce structured MathML.
8. **Edited-zone staleness:** equation edits updated text while retaining stale
   MathML. Both are now rebuilt from current line ownership and page geometry.
   Inline MathML is protected during paragraph rebuilding.
9. **Merged cells:** existing reconstruction inferred columns from text and
   discarded row spans. The existing PyMuPDF ruled-table geometry is now reused
   through cached cell bounds, grid reconstruction and BITS rowspan/colspan.
10. **Preview/coverage:** browser testing exposed missing italic MathML glyphs
    on a system without math fonts. A pinned, unmodified SIL OFL STIX font is
    bundled for offline preview. Coverage checks now respect MathML token
    boundaries instead of reporting separate identifiers as missing words.
11. **Tests were not runnable:** imports referenced nonexistent modules and
    functions; Span constructors omitted required arguments. Replaced these
    with real pipeline tests and negative cases, including actual serialization.

## Changed files

| File | Reason |
| --- | --- |
| `.gitignore` | Exclude disposable Python caches, environments and conversion outputs. |
| `README.md` | Installation, portable CLI commands, migration and test instructions. |
| `assets/fonts/OFL.txt` | Matching STIX 2.0.2 license text. |
| `assets/fonts/README.md` | Pinned font provenance and checksum. |
| `assets/fonts/STIX2Math.woff2` | Unmodified STIX math font for offline glyph rendering. |
| `config/config.yaml` | Default to physical float placement; normalize line endings. |
| `config/portable.yaml` | Portable path overrides without rewriting saved machine paths. |
| `docs/repair-report.md` | Root causes, scope, observed verification and remaining limitations. |
| `preview_html.py` | Native namespaced MathML, equation-image fallback and offline math font. |
| `scripts/regression_demo.py` | Reproducible synthetic PDF, XML, image and preview artifacts. |
| `src/bits_tool/bits_generator.py` | Shared MathML emission, inline formulas, equation graphic fallback and rowspan. |
| `src/bits_tool/config.py` | Physical placement default; version bump invalidates completed conversion checkpoints. |
| `src/bits_tool/converter.py` | Source-faithful crops, equation assets, OCR missing-engine failure reporting. |
| `src/bits_tool/document_tree.py` | Glyph origins, table geometry and page diagnostics; inline math text preservation. |
| `src/bits_tool/equation_detector.py` | Spatial grouping, evidence-based MathML, ambiguous-structure fallback. |
| `src/bits_tool/figure_detector.py` | Column-local association, text-art bounds, restrained background filtering, unaltered crop default. |
| `src/bits_tool/layout_analyzer.py` | Contextual recurring furniture and sequential folio classification. |
| `src/bits_tool/ocr_engine.py` | Preserve native lines; suppress spatially overlapping OCR words. |
| `src/bits_tool/paragraph_detector.py` | Inline formula runs and safe paragraph rebuild/merging. |
| `src/bits_tool/reading_order.py` | Narrow columns, region columns, changing bands, equation regions and diagnostics. |
| `src/bits_tool/structure_builder.py` | Consume equation regions, avoid guessed figure crops, retain table spans. |
| `src/bits_tool/table_detector.py` | Use ruled table cells and preserve row/column spanning. |
| `src/bits_tool/test_equation_pipeline.py` | Replace broken tests with eight runnable mathematical regressions. |
| `src/bits_tool/text_extractor.py` | Raw glyph coordinates, deduplication, table geometry and cache versioning. |
| `src/bits_tool/validators.py` | Count MathML token text without concatenating distinct identifiers. |
| `src/bits_tool/zoning.py` | Recompute edited equation ASTs and initialize legacy page metadata. |
| `tests/test_conversion.py` | Twenty-three pipeline/layout/serialization regression tests, including clipped vectors, soft-hyphen joins and column-aware coverage. |


## Additional causes and corrections from actual PDFs

| Source files | Cause and repair |
| --- | --- |
| `pdf_geometry.py` (new), `text_extractor.py` | Image placement ignored internal PDF clips. A MuPDF paint device follows clip push/pop and transparency-mask state, matched to image occurrences. Full/visible bounds and verification status survive extraction. Compound path endpoints omitted by the library's reported rectangle are included before clipping. Cache 19 invalidates old extraction. |
| `document_tree.py`, `text_extractor.py` | Font runs discarded individual glyph positions. Glyph geometry now survives extraction, rotations and cache roundtrips, preserving interleaved brackets/variables. |
| `math_geometry.py` (new), `equation_detector.py` | Vector arrows were unknown shapes; multiple fraction rules were not represented together. Actual shaft/head paths support directed/equilibrium arrows and rate labels. Independent bars own disjoint numerator/denominator sets. Overlaps and unsupported structures request review. XML-invalid control glyphs are removed before token creation, fixing empty operators found by QC. |
| `chemical_detector.py` (new), `reading_order.py`, `structure_builder.py` | Atom labels and bond strokes escaped as body paragraphs. Spatial components group uncaptioned drawings, including rectangular bonds and annotation arrows. Prose intersections and omitted crossing paths request review. Evidence is retained without inventing chemical connectivity. |
| `figure_detector.py`, `converter.py`, `validators.py` | Original vectors and outlined glyphs export as SVG and are independently validated. Rotation matches raster export. Figure 9-11's bold panel B label was mistaken for intervening prose; both panels now stay with their caption. |
| `recognition_qc.py` (new), `converter.py`, `report_generator.py` | New independent QC audits parsed MathML, source geometry, clipping/body overlap and preserved representations. JSON findings and an HTML report provide source crops and preview links. |
| `config.py`, `config/config.yaml` | Version 1.2.0; optional `qa.fail_on_recognition_review` fails conversion for unresolved recognition. Default preserves warnings; structural failures fail normally. |
| `tests/test_recognition_qc.py` (new) | Twelve positive/negative geometry and QC cases: nested clips, soft masks, repeated occurrences, rotations, vector export, arrow geometry, unsupported math and strict gating. |
| `tests/test_supplied_chapters.py` | Seventeen actual-output checks: source-manifest match, Figure 7-2, reactions, chained fractions, chemistry, Figure 9-11 panels, QC, columns/tables/captions/assets. |
| `scripts/verify_chapters.py`, `README.md`, this report | Reproducible chapter conversions, representative originals, commands and measured limitations. |

Earlier real-PDF repairs also addressed side captions (Figure 7-4), multi-panel
figures, rotated axis labels, repeated edge strips, Table 7-1's next-heading
boundary, soft-hyphen joins and column-aware coverage normalization. No content
threshold was reduced. Existing conversion interfaces, BITS namespaces, IDs,
paths and desktop/manual-zoning features are retained.

## Source traces and comparisons

| Original PDF page | Confirmed converted result |
| --- | --- |
| Chapter 7 p3 / Figure 7-2 | Placement begins at x423.479; active clip begins at x437.211 and ends at x598.243. Verified visible bounds exclude neighboring prose. Caption and aspect ratio retained. |
| Chapter 9 p3 | `E + S ⇄ ES → E + P` uses `munderover` for k1/k2, `mover` for k3 and genuine subscripts. The Michaelis–Menten fraction uses `mfrac` and two `msub` nodes. Neither display formula uses an image fallback. |
| Chapter 7 p10 | `L + P ⇄ LP` has k1 above and k2 below. Equilibrium chain has three fractions: k1/k2, [LP]/([L][P]), and 1/Kd. Interleaved glyphs preserve [L][P]. Figure 7-10 still contains all three panels. |
| Chapter 7 p27 | One uncaptioned peptide SVG contains 15 label lines and 27 stroke objects, including R1/R2 and A/B/C arrows. Loose atom paragraphs are eliminated. Source crop and SVG compared; chemical meaning remains in review. |
| Chapter 9 p11 / Figure 9-11 | Molecular panel A and protein panel B stay with their caption; no partial molecular crop or duplicate atom paragraphs. |
| Chapter 9 p8; chapter 7 p4,18,22,26 | Allosteric figure, side-caption helix, multi-panel reaction, clipped vector figure and table regressions continue passing. Prior visual comparisons retained; the affected cases above were freshly compared. |

The comparison HTML and browser captures are in the verification archive.
Preview content reflows in logical reading order; it is not a PDF facsimile.

## Actual validation results

| Check | Chapter 9 | Chapter 7 |
| --- | --- | --- |
| Pages / failed pages |21 / 0|29 / 0|
| BITS DTD |valid|valid|
| Valid unique IDs |460|506|
| Readable graphic references |19|33|
| Display equations |6, all MathML|2, all MathML|
| Figure nodes / tables |16 / 1|30 / 2|
| Measured token coverage |99.05%|99.22%|
| Recognition QC |32 PASS, 0 REVIEW, 0 FAILED|9 PASS, 1 REVIEW, 0 FAILED|
| Conversion status |PASS WITH WARNINGS|PASS WITH WARNINGS|
| Warm-cache conversion time |5.4s|9.5s|

Coverage is approximate, not proof of completeness. Timings include recognition
and region-crop QC; they exclude the verification script's full-page renders.
Native PDF text was used, without OCR or external recognition services. Browser
checks loaded all 17 and 31 displayed images, respectively. Targeted MathML
rendered correctly; no empty math operators remained.

Final commands (runtime venv Python 3.9.25, repository root):

```sh
python scripts/verify_chapters.py --pdf-root /path/to/pdfs --out /path/to/chapter-verification
BITS_CHAPTER_RESULTS=/path/to/chapter-verification PYTHONPATH=src:. python -m unittest discover -s tests -v
PYTHONPATH=src:. python -m unittest discover -s src/bits_tool -p 'test_*.py' -v
git diff --check
```

Exact environment paths, source/PDF hashes and observed results are recorded in
the validation record and packaged manifests. During development, QC found empty
MathML operators and source comparison found a partial molecular panel; both
were corrected before the final passing checks. Ambiguous synthetic formulas
still preserve a crop and a review warning.

## Automatic QC workflow

Normal conversion creates `qa/recognition_qc.json` and `.html`, linked from the
main report. Findings record status, page/bounds, output target, evidence and
review reason. Display regions have source crops and preview links. Existing
DTD/ID/link/image/content gates remain active. Set
`qa.fail_on_recognition_review: true` to fail the quality gate while recognition
reviews remain unresolved. Default is false.

PASS covers the stated structural/geometric checks. Molecular identity, bond
order and stereochemistry are never inferred from region grouping. If PyMuPDF's
low-level bindings are unavailable, original placement bounds are preserved and
clipping is marked unverified. Tested PyMuPDF: 1.26.5. No new dependency or API
credentials required. Install/run commands are in README.md / RUNNING.md.

## Remaining limitations

- Chapter 7 p27 chemistry is visually preserved and grouped; chemical meaning
  requires human review. Its source crop and output link are in the QC queue.
- Unknown vector operators, nested/overlapping fractions, radicals, integrals,
  matrices and ambiguous baselines can remain intact review images. This is
  conservative layout recognition, not general mathematical OCR.
- All answer-box/list/heading conventions, every reading-order transition and
  remaining coverage differences have not been exhaustively certified. General
  RTL reading order is not implemented.
- References into absent chapters and some equation-label targets remain
  unresolved; author metadata was not invented. These cause other conversion
  warnings beyond the recognition queue.
- Real scanned-page OCR accuracy, full GUI interactions and large-book benchmarks
  were not exercised. Existing OCR merge and manual-zone tests pass.
- Existing outputs are BITS XML and an HTML preview. This project has no separate
  XHTML/EPUB exporter or EPUBCheck gate; neither is claimed validated.

## Deliverables

Complete project ZIP; modified-files ZIP with original directories; chapter
verification ZIP containing XML/HTML, referenced assets, diagnostics, QC reports,
test logs, source hashes and source/preview comparisons. Original PDFs remain
external fixtures. SHA256SUMS.txt identifies the archives.
