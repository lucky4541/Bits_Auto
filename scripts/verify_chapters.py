"""Convert the supplied chapter fixtures, keeping each chapter's assets separate.

The copyrighted PDFs are provided externally; no network or OCR is required.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
import pymupdf
from bits_tool.config import Config
from bits_tool.converter import BookConverter
from preview_html import main as preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    tracked_inputs = sorted((ROOT / 'src').rglob('*.py')) + [ROOT / 'preview_html.py',
                      ROOT / 'config/portable.yaml', ROOT / 'requirements.txt']
    manifest_path = args.out / 'verification-inputs.json'
    manifest_path.unlink(missing_ok=True)
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tracked_inputs}
    failed = False
    for ch in ('009', '007'):
        matches = sorted(args.pdf_root.glob(f'*_ch{ch}.pdf'))
        if len(matches) != 1:
            parser.error(f'Expected one *_ch{ch}.pdf under --pdf-root; got {len(matches)}')
        pdf = matches[0]
        out = args.out / f'ch{ch}'
        out.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(pdf) as doc:
            inventory = [{'page': i + 1, 'chars': len(p.get_text()),
                          'images': len(p.get_image_info()), 'drawings': len(p.get_drawings())}
                         for i, p in enumerate(doc)]
            representatives = (3, 6, 8, 11) if ch == '009' else (3, 4, 10, 18, 22, 26, 27)
            for pno in representatives:
                doc[pno - 1].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(out / f'original-{pno:02d}.png')
        (out / 'input-inventory.json').write_text(json.dumps({
            'filename': pdf.name, 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
            'pages': inventory}, indent=2), encoding='utf-8')
        cfg = Config.load(ROOT / 'config/portable.yaml', overrides={
            'mode': 'development', 'paths': {'cache_dir': str(args.out / 'cache')},
            'images': {'format': 'png', 'dpi': 144}})
        result = BookConverter(cfg).convert(pdf, out)
        (out / 'result.json').write_text(json.dumps(asdict(result), indent=2, default=str), encoding='utf-8')
        if result.xml_path:
            preview([result.xml_path, '--no-open'])
        print(ch, result.status, 'pages:', len(inventory), 'seconds:', round(result.seconds, 2), flush=True)
        failed |= result.status == 'FAILED' or bool(result.failed_pages)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
