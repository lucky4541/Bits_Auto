"""Generate synthetic regression artifacts; no claim of original-PDF fidelity."""
from pathlib import Path
import argparse
import json
import sys
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(ROOT/'tests')]
from test_conversion import fixture
from bits_tool.config import Config
from bits_tool.converter import BookConverter
from preview_html import main as preview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pdf = out/'synthetic.pdf'
    fixture(pdf)
    cfg = Config.load(ROOT/'config/portable.yaml', overrides={
        'paths': {'cache_dir': str(out/'cache')}, 'mode':'development',
        'ocr': {'engine':'none'}, 'images': {'dpi':144, 'format':'png'}})
    result = BookConverter(cfg).convert(pdf, out/'converted')
    preview([result.xml_path, '--no-open'])
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            page.get_pixmap(matrix=pymupdf.Matrix(1.25,1.25)).save(out/f'original-synthetic-page-{i+1}.png')
    (out/'result.json').write_text(json.dumps({
        'fixture': 'synthetic; not the supplied screenshot PDF',
        'status':result.status, 'validation':result.validation,
        'issues':result.issues, 'failed_pages':result.failed_pages}, indent=2, default=str))
    print(f'Synthetic conversion: {result.status}; {out}')
    return 1 if result.status == 'FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
