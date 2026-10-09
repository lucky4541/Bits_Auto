"""Command-line interface (same services as the GUI).

  python bits_cli.py analyze                       analyze Samples/ -> analysis/*.json + mapping/*.json
  python bits_cli.py convert [PDF|FOLDER ...]      convert (default: every book in inputs/) -> output/
  python bits_cli.py convert --no-resume ...       ignore checkpoints and redo every book
  python bits_cli.py validate FILE.xml             DTD / IDs / links / images / structure
  python bits_cli.py zone PDF|FOLDER [--manual]    create a zoning project only (review it in the GUI)
  python bits_cli.py zone-xml output/<book>        generate XML from the saved (edited) zones
  python bits_cli.py status                        DTD + mapping profile status
Options: --config config/config.yaml  --out output_dir
Exit code: 0 PASS / PASS WITH WARNINGS, 1 FAILED, 2 usage or setup error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from bits_tool import services  # noqa: E402
from bits_tool.config import Config  # noqa: E402


def _progress(**kw):
    stage = kw.get("stage") or kw.get("message") or ""
    frac = kw.get("frac")
    book = kw.get("current_file") or kw.get("book") or ""
    if kw.get("detail") and stage == "Batch":
        stage = f"Batch: {kw['detail']}"
    if stage:
        pct = f"{frac * 100:5.1f}%" if isinstance(frac, (int, float)) else "      "
        print(f"[{pct}] {book} {stage}".rstrip(), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bits_cli", description="BITS PDF -> XML conversion tool")
    ap.add_argument("--config", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("analyze")
    c = sub.add_parser("convert")
    c.add_argument("inputs", nargs="*", type=Path)
    c.add_argument("--out", type=Path, default=None)
    c.add_argument("--no-resume", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("xml", type=Path)
    sub.add_parser("status")
    z = sub.add_parser("zone")
    z.add_argument("input", type=Path)
    z.add_argument("--manual", action="store_true")
    z.add_argument("--out", type=Path, default=None)
    zx = sub.add_parser("zone-xml")
    zx.add_argument("book_dir", type=Path)
    a = ap.parse_args(argv)
    cfg = Config.load(a.config)

    if a.cmd == "status":
        print(json.dumps({"dtd": services.dtd_status(cfg), "mapping": services.mapping_status(cfg)}, indent=1, default=str))
        return 0
    if a.cmd == "analyze":
        res = services.analyze_samples(cfg, progress=_progress)
        print(json.dumps(res, indent=1, default=str)[:4000])
        return 0
    if a.cmd == "validate":
        res = services.validate_xml(cfg, a.xml, progress=_progress)
        print(json.dumps({k: res.get(k) for k in ("status", "critical", "dtd", "links", "ids", "images")}, indent=1, default=str)[:6000])
        return 1 if res.get("status") == "FAILED" else 0
    if a.cmd == "zone":
        d = services.start_zoning(cfg, a.input, a.out or cfg.path("output_dir"), manual=a.manual, progress=_progress)
        print(f"zoning project: {d}")
        return 0
    if a.cmd == "zone-xml":
        ed = services.load_zoning(cfg, a.book_dir)
        r = services.zoning_generate(cfg, ed, progress=_progress)
        print(f"{r['book']}: {r['status']}  {r['xml']}")
        return 1 if r["status"] == "FAILED" else 0
    if a.cmd == "convert":
        if services.mapping_status(cfg).get("state") == "missing":
            print("No mapping profile yet - run:  python bits_cli.py analyze", file=sys.stderr)
            return 2
        inputs = a.inputs or [cfg.path("input_dir")]
        out = a.out or cfg.path("output_dir")
        results = services.convert(cfg, inputs, out, progress=_progress, resume=not a.no_resume)
        failed = 0
        for r in results:
            st = r.get("status")
            failed += st == "FAILED"
            print(f"{r.get('book') or r.get('isbn') or '?'}: {st}  {r.get('xml') or ''}")
        return 1 if failed else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
