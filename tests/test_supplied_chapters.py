"""Source-specific regressions; run scripts/verify_chapters.py first.

Set BITS_CHAPTER_RESULTS to that script's --out directory. No supplied PDF is
bundled in the repository. These assertions cover selected structures; they do
not certify complete fidelity or successful recognition of image fallback math.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest
from lxml import etree
from PIL import Image
from bits_tool.validators import validate_ids, validate_images, validate_links

M = {'m': 'http://www.w3.org/1998/Math/MathML'}
XLINK = '{http://www.w3.org/1999/xlink}href'
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('BITS_CHAPTER_RESULTS'), 'External chapter results not supplied')
class SuppliedChapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = {}
        for ch in ('009', '007'):
            base = Path(os.environ['BITS_CHAPTER_RESULTS']) / f'ch{ch}'
            result = json.loads((base / 'result.json').read_text())
            out = base / '9788418892974'
            cls.data[ch] = {'base': base, 'out': out, 'result': result,
                            'xml': etree.parse(str(out / '9788418892974.xml')),
                            'pages': json.loads((out / 'intermediate/document_structure.json').read_text())['pages']}

    def figure(self, ch, label):
        return self.data[ch]['xml'].xpath('//fig[label=$label]', label=label)[0]

    def region(self, ch, pno, label):
        return next(r for r in self.data[ch]['pages'][pno-1]['regions'] if r['meta'].get('label') == label)

    def test_results_match_current_pipeline_source(self):
        path = Path(os.environ['BITS_CHAPTER_RESULTS']) / 'verification-inputs.json'
        manifest = json.loads(path.read_text())
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)

    def test_question_table_values_and_two_column_order(self):
        p = self.data['007']['pages'][26]
        reg = next(r for r in p['regions'] if r['kind'] == 'table')
        grid = reg['meta']['grid']
        values = [[" ".join("".join(s['text'] for s in l['spans']).strip() for l in c['lines'])
                   for c in row] for row in grid['rows']]
        self.assertEqual(values[-6:], [['A', 'Sí', 'No', 'Sí', 'No'], ['B', 'Sí', 'Sí', 'No', 'No'],
                                     ['C', 'Sí', 'No', 'Sí', 'Sí'], ['D', 'No', 'Sí', 'No', 'Sí'],
                                     ['E', 'No', 'No', 'Sí', 'No'], ['F', 'No', 'Sí', 'No', 'Sí']])
        left = [l['order'] for l in p['lines'] if l['role']=='body' and l['bbox'][2] < 335]
        right = [l['order'] for l in p['lines'] if l['role']=='body' and l['bbox'][0] > 340]
        self.assertLess(max(left), min(right))

    def test_all_50_pages_and_xml_integrity(self):
        for ch, count in [('009', 21), ('007', 29)]:
            d = self.data[ch]
            self.assertEqual(len(d['pages']), count)
            self.assertFalse(d['result']['failed_pages'])
            self.assertNotEqual(d['result']['status'], 'FAILED')
            self.assertTrue(d['result']['validation']['dtd']['valid'])
            self.assertFalse(validate_ids(d['xml'], 'book')['duplicates'])
            self.assertFalse(validate_links(d['xml'])['broken'])
            self.assertFalse(validate_images(d['xml'], d['out'])['missing'])
            self.assertFalse(d['xml'].xpath('//p/disp-formula'))

    def test_michaelis_menten_fraction(self):
        formulas = self.data['009']['xml'].xpath('//disp-formula[m:math//m:mfrac]', namespaces=M)
        eq = next(f for f in formulas if 'máx' in ''.join(f.itertext()))
        frac = eq.find('.//m:mfrac', M)
        self.assertEqual(''.join(frac[0].itertext()), 'Vmáx[S]')
        self.assertEqual(''.join(frac[1].itertext()), 'Km+[S]')
        self.assertEqual(len(frac.findall('.//m:msub', M)), 2)
        self.assertEqual(''.join(eq.find('m:math/m:mrow', M).itertext()), 'v=Vmáx[S]Km+[S]')
        p = self.data['009']['pages'][2]
        fragments = [l for l in p['lines'] if 520 < l['bbox'][1] < 547 and 225 < l['bbox'][0] < 275]
        self.assertTrue(fragments)
        self.assertTrue(all(l['role'] == 'equation-text' for l in fragments))

    def test_vector_reactions_are_structured_mathml(self):
        for ch, pno in [('009', 3), ('007', 10)]:
            p = self.data[ch]['pages'][pno-1]
            eqs = [r for r in p['regions'] if r['kind'] == 'equation' and
                   'arrow' in r['meta'].get('reason', '')]
            self.assertEqual(len(eqs), 1)
            self.assertTrue(eqs[0]['meta']['mathml'])
            self.assertFalse(eqs[0]['meta']['fallback_image'])
            formulas = self.data[ch]['xml'].xpath('//disp-formula[m:math//m:munderover]', namespaces=M)
            reaction = next(f for f in formulas if 'ES' in ''.join(f.itertext()) or 'LP' in ''.join(f.itertext()))
            arrow = reaction.find('.//m:munderover', M)
            self.assertEqual(''.join(arrow[0].itertext()), '⇄')
            self.assertEqual(''.join(arrow[1].itertext()), 'k2')
            self.assertEqual(''.join(arrow[2].itertext()), 'k1')
            self.assertIsNone(reaction.find('graphic'))
            if ch == '009':
                forward = reaction.find('.//m:mover', M)
                self.assertEqual(''.join(forward[0].itertext()), '→')
                self.assertEqual(''.join(forward[1].itertext()), 'k3')

    def test_three_chained_fractions_preserve_interleaved_brackets(self):
        equations = self.data['007']['xml'].xpath('//disp-formula[count(.//m:mfrac)=3]', namespaces=M)
        self.assertEqual(len(equations), 1)
        fracs = equations[0].findall('.//m:mfrac', M)
        self.assertEqual([[''.join(side.itertext()) for side in f] for f in fracs],
                         [['k1', 'k2'], ['[LP]', '[L][P]'], ['1', 'Kd']])

    def test_bitmap_figure_7_2_uses_verified_clip(self):
        reg = self.region('007', 3, 'FIGURA 7-2')
        self.assertGreaterEqual(reg['meta']['art'][0], 437)
        self.assertLessEqual(reg['meta']['art'][2], 599)
        qc = self.data['007']['result']['validation']['recognition']
        fig_id = self.figure('007', 'FIGURA 7-2').get('id')
        item = next(i for i in qc['items'] if i['target'] == fig_id and i['kind'] == 'figure-clip')
        self.assertEqual(item['status'], 'PASS')
        self.assertTrue(all(i['clip_status'] == 'verified' for i in item['images']))
        self.assertTrue(any(i['full'][0] < 424 and i['bbox'][0] > 437 for i in item['images']))

    def test_uncaptioned_chemistry_preserved_as_one_reviewable_vector(self):
        p = self.data['007']['pages'][26]
        regions = [r for r in p['regions'] if r['meta'].get('diagram')]
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]['meta']['diagram']['labels']), 15)
        labels = regions[0]['meta']['diagram']['labels']
        self.assertTrue(any('A' in l['text'] and 'B' in l['text'] and 'C' in l['text'] for l in labels))
        qc = self.data['007']['result']['validation']['recognition']
        item = next(i for i in qc['items'] if i['kind'] == 'chemical-diagram' and i['pdf_page'] == 27)
        self.assertEqual(item['status'], 'REVIEW')
        self.assertTrue(item['geometry_enclosed'])
        self.assertFalse(item['semantic_bond_assignment'])
        self.assertTrue(item['asset'].endswith('.svg'))
        svg = etree.parse(str(self.data['007']['out'] / 'images' / item['asset']))
        self.assertEqual(etree.QName(svg.getroot()).namespace, 'http://www.w3.org/2000/svg')
        self.assertTrue(svg.xpath('//*[local-name()="path"]'))
        self.assertFalse(any(l['role']=='body' and 410 < l['bbox'][0] < 545 and
                             642 < l['bbox'][1] < 724 for l in p['lines']))

    def test_molecular_panel_stays_with_captioned_figure(self):
        region = self.region('009', 11, 'FIGURA 9-11')
        self.assertLess(region['meta']['art'][1], 320)
        self.assertGreater(region['meta']['art'][3], 620)
        p = self.data['009']['pages'][10]
        self.assertFalse(any(r['meta'].get('diagram') for r in p['regions']))
        self.assertTrue(all(l['role'] == 'figure-text' for l in p['lines']
                            if 452 < l['bbox'][0] < 560 and 319 < l['bbox'][1] < 450))

    def test_recognition_qc_reports_exist_and_contain_no_failures(self):
        for d in self.data.values():
            qc = json.loads((d['out'] / 'qa/recognition_qc.json').read_text())
            self.assertEqual(qc, d['result']['validation']['recognition'])
            self.assertNotEqual(qc['status'], 'FAILED')
            self.assertTrue((d['out'] / 'qa/recognition_qc.html').is_file())
            for item in qc['items']:
                if item.get('source_crop'):
                    self.assertTrue((d['out'] / 'qa' / item['source_crop']).is_file())

    def test_allosteric_figure_order_and_caption(self):
        reg = self.region('009', 8, 'FIGURA 9-7')
        art = reg['meta']['art']
        self.assertLess(art[1], 100)
        self.assertGreater(art[3], 400)
        self.assertLess(art[2], 235)
        p = self.data['009']['pages'][7]
        self.assertFalse(any(l['role'] == 'body' and l['bbox'][0] < 225 and l['bbox'][1] < 405 for l in p['lines']))
        fig = self.figure('009', 'FIGURA 9-7')
        self.assertIsNotNone(fig.find('graphic'))
        self.assertIn('alost', ''.join(fig.find('caption').itertext()).lower())
        self.assertLess(reg['meta']['order'], min(l['order'] for l in p['lines'] if l['role']=='body' and l['bbox'][0] > 235))

    def test_side_caption_helix_art(self):
        reg = self.region('007', 4, 'FIGURA 7-4')
        self.assertEqual(reg['meta']['direction'], 'beside')
        art = reg['meta']['art']
        self.assertLess(art[0], 240)
        self.assertGreater(art[2], 380)
        self.assertGreater(art[3], 760)
        self.assertLess(art[2], 395)
        fig = self.figure('007', 'FIGURA 7-4')
        self.assertIsNotNone(fig.find('graphic'))
        self.assertIn('hélice', ''.join(fig.find('caption').itertext()))
        self.assertNotIn('hé lice', ''.join(fig.find('caption').itertext()))

    def test_multi_object_panels_not_duplicate_fragments(self):
        for pno, label, xmax in [(10, 'FIGURA 7-10', 570), (18, 'FIGURA 7-23', 530)]:
            reg = self.region('007', pno, label)
            art = reg['meta']['art']
            self.assertLess(art[0], 130)
            self.assertGreater(art[2], xmax)
            extras = [r for r in self.data['007']['pages'][pno-1]['regions']
                      if r['kind'] in ('inline-graphic', 'figure') and r is not reg and r['bbox'][1] >= 500]
            self.assertEqual(extras, [])

    def test_pdf_vector_clipping_prevents_neighbor_capture(self):
        reg = self.region('007', 22, 'FIGURA 7-27')
        self.assertLess(reg['meta']['art'][2], 225)
        self.assertGreater(reg['meta']['art'][1], 505)
        reg = self.region('009', 3, 'FIGURA 9-2')
        self.assertLess(reg['meta']['art'][0], 440)  # rotated axis label retained
        p = self.data['009']['pages'][2]
        self.assertTrue(any(l['role']=='body' and 196 < l['bbox'][1] < 198 and l['bbox'][0] > 439 for l in p['lines']))

    def test_table_rows_columns_and_next_heading(self):
        table = self.data['007']['xml'].xpath('//table-wrap[label="TABLA 7-1"]')[0]
        rows = table.xpath('.//tr')
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(len(r) == 3 for r in rows))
        self.assertIn('Infarto del miocardio', ''.join(rows[1][0].itertext()))
        self.assertIn('Enfermedades priónicas', ''.join(rows[5][0].itertext()))
        self.assertNotIn('P R E G U N', ''.join(table.itertext()))
        self.assertLess(self.region('007', 26, 'TABLA 7-1')['bbox'][3], 430)

    def test_recurring_edge_art_excluded_with_diagnostics(self):
        for d in self.data.values():
            for p in d['pages'][1:]:
                self.assertTrue(any(x['action']=='exclude-graphic-furniture' for x in p['diagnostics']))
                self.assertFalse(any(r['kind']=='inline-graphic' and r['bbox'][1] < 30 for r in p['regions']))

    def test_referenced_figure_assets_are_readable_and_proportional(self):
        for ch, pno, label in [('009', 8, 'FIGURA 9-7'), ('007', 4, 'FIGURA 7-4'), ('007', 10, 'FIGURA 7-10')]:
            box = self.region(ch, pno, label)['meta']['art']
            href = self.figure(ch, label).find('graphic').get(XLINK)
            path = self.data[ch]['out'] / 'images' / href
            with Image.open(path) as im:
                im.load()
                self.assertAlmostEqual(im.width / im.height, (box[2]-box[0])/(box[3]-box[1]), delta=.02)
