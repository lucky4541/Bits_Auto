"""Synthetic PDF regressions across extraction, regions, XML and previews."""
import json
from pathlib import Path
import tempfile
import unittest
import pymupdf
from lxml import etree
from PIL import Image, ImageChops
from bits_tool.config import Config, DEFAULTS
from bits_tool.converter import BookConverter
from bits_tool.document_tree import PageInfo, Region, Line, Span, Node, IssueLog
from bits_tool.figure_detector import export_region, figure_region_for_caption
from bits_tool.layout_analyzer import BookStyle, detect_columns, mark_headers_footers
from bits_tool.reading_order import order_items
from bits_tool.text_extractor import extract_page, page_drawings
from bits_tool.test_equation_pipeline import line
from preview_html import Renderer, main as preview

ROOT = Path(__file__).resolve().parents[1]


def page(lines=(), regions=()):
    return PageInfo(0, 'synthetic.pdf', 0, 600, 800, (0, 0, 600, 800), lines=list(lines), regions=list(regions))


def fixture(path):
    """Deterministic, generated input with known logical content and coordinates."""
    doc = pymupdf.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((50, 85), 'Synthetic conversion regression', fontsize=18)
    for i, text in enumerate(['The first paragraph starts here and continues',
                              'onto this second line with normal prose.',
                              'A second paragraph follows after a gap.']):
        p.insert_text((50, 130 + i * 14 + (14 if i == 2 else 0)), text, fontsize=11, fontname='tiro')
    # Fraction with left-hand side at the rule's baseline.
    p.insert_text((95, 240), 'v =', fontsize=12, fontname='tiro')
    p.insert_text((150, 224), 'V', fontsize=12, fontname='tiit')
    p.insert_text((160, 228), 'max', fontsize=8, fontname='tiro')
    p.insert_text((180, 224), '[S]', fontsize=12, fontname='tiro')
    p.draw_line((145, 238), (208, 238))
    p.insert_text((149, 258), 'K', fontsize=12, fontname='tiit')
    p.insert_text((160, 262), 'm', fontsize=8, fontname='tiro')
    p.insert_text((173, 258), '+ [S]', fontsize=12, fontname='tiro')
    p.insert_text((50, 300), 'The prose after the equation is preserved.', fontsize=11, fontname='tiro')
    p = doc.new_page(width=600, height=800)
    p.insert_text((50, 80), 'Columns with an allosteric-style diagram', fontsize=18)
    for i in range(4):
        y = 145 + i * 80
        p.draw_circle((120, y), 22, color=(0, .3, 0), fill=(.5, .8, .3))
        if i < 3:
            p.draw_line((120, y + 22), (120, y + 58))
        p.insert_text((150, y), 'S', fontsize=10)
    p.insert_text((50, 450), 'FIGURE 1. Synthetic stacked diagram.', fontsize=9, fontname='hebo')
    for i in range(10):
        p.insert_text((285, 130 + i * 17), f'Right column sentence number {i:02d}.', fontsize=11, fontname='tiro')
    p = doc.new_page(width=600, height=800)
    p.insert_text((50, 85), 'Tables and inline mathematics', fontsize=18)
    x = 50
    for text, font in [('The rate ', 'tiro'), ('v', 'tiit'), (' = ', 'tiro'), ('k', 'tiit'), (' varies with concentration.', 'tiro')]:
        p.insert_text((x, 130), text, fontname=font, fontsize=11)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=11)
    # Ruled table: first row spans both columns; first data cell spans two rows.
    for y in (180, 220, 300):
        p.draw_line((50, y), (450, y))
    p.draw_line((250, 260), (450, 260))
    for x in (50, 450):
        p.draw_line((x, 180), (x, 300))
    p.draw_line((250, 220), (250, 300))
    for pos, text in [((65,205),'Shared heading'), ((65,245),'Spanning row cell'),
                      ((265,245),'Upper value'), ((265,285),'Lower value')]:
        p.insert_text(pos, text, fontsize=11, fontname='tiro')
    p.insert_text((50, 350), 'Text after the table.', fontsize=11, fontname='tiro')
    # Two aligned equations have no fraction bar: preserve together for review.
    p.insert_text((150, 410), 'x = 1', fontsize=12, fontname='tiro')
    p.insert_text((150, 434), 'y = 2', fontsize=12, fontname='tiro')
    p.insert_text((50, 480), 'Text after the reviewed equation.', fontsize=11, fontname='tiro')
    doc.save(path)
    doc.close()


class LayoutTests(unittest.TestCase):
    def test_coverage_joins_soft_hyphens_within_column(self):
        from bits_tool.validators import content_coverage
        ls = [line('La hé\u00ad', (20, 20, 100, 30)),
              line('Other column', (300, 20, 430, 30)),
              line('lice permanece.', (20, 35, 120, 45))]
        xml = etree.ElementTree(etree.fromstring('<book><p>La hélice permanece.</p><p>Other column</p></book>'))
        result = content_coverage([page(ls)], xml)
        self.assertEqual(result['coverage'], 1.0)

    def test_soft_hyphen_joins_word_before_removing_marker(self):
        from bits_tool.paragraph_detector import append_line, finish_inlines, Hyphenation
        runs = []
        append_line(runs, line('La hé\u00ad', (10, 10, 70, 20)), Hyphenation())
        append_line(runs, line('lice conserva su forma.', (10, 25, 150, 35)), Hyphenation())
        self.assertEqual(''.join(r['text'] for r in finish_inlines(runs)), 'La hélice conserva su forma.')

    def test_vector_bounds_obey_pdf_clip_and_restore(self):
        doc = pymupdf.open()
        p = doc.new_page(width=300, height=300)
        p.draw_rect((30, 80, 260, 220), color=(1, 0, 0), fill=(1, 0, 0))
        stream = p.get_contents()[0]
        # PDF coordinates have their origin at bottom-left. Clip the first
        # drawing; restore before a second independent drawing.
        doc.update_stream(stream, b"q 50 100 100 100 re W n\n" + doc.xref_stream(stream) + b"\nQ")
        p.draw_rect((200, 30, 240, 60), color=(0, 0, 1))
        drawings = page_drawings(p)
        self.assertEqual(drawings[0]['bbox'], (50.0, 100.0, 150.0, 200.0))
        self.assertEqual(drawings[1]['bbox'], (200.0, 30.0, 240.0, 60.0))
        self.assertTrue(drawings[0]['clip_bounds'])
        self.assertFalse(drawings[1]['clip_bounds'])
        doc.close()

    def test_narrow_three_and_four_columns(self):
        for n in (2, 3, 4):
            ls = [line(f'Column {c} row {r}', (20+c*140, 100+r*18, 135+c*140, 112+r*18)) for r in range(4) for c in range(n)]
            p = page(ls)
            p.columns = detect_columns(p, ls)
            ordered = [o.text for _, o in order_items(p, ls, [])]
            self.assertEqual(ordered, [f'Column {c} row {r}' for c in range(n) for r in range(4)])
            self.assertTrue(p.diagnostics[-1]['items'])

    def test_full_width_heading_and_mid_page_spanner(self):
        ls = [line(f'{c}{r}', (50+c*280, 100+r*20, 250+c*280, 112+r*20)) for c in range(2) for r in range(2)]
        ls += [line('Spanning heading', (50, 60, 530, 80), size=18), line('Middle spanning heading', (50, 160, 530, 180), size=18)]
        ls += [line(f'below{c}{r}', (50+c*280, 200+r*20, 250+c*280, 212+r*20)) for c in range(2) for r in range(2)]
        p = page(ls); p.columns = [(50, 250), (330, 530)]
        self.assertEqual([o.text for _, o in order_items(p, ls, [])],
                         ['Spanning heading', '00', '01', '10', '11', 'Middle spanning heading', 'below00', 'below01', 'below10', 'below11'])

    def test_single_figure_column_precedes_text_column(self):
        ls = [line(f'Body {i}', (300, 100+i*20, 560, 112+i*20)) for i in range(6)]
        figure = Region('figure', (50, 90, 240, 400), 0)
        p = page(ls, [figure]); p.columns = [(0, 600)]
        result = order_items(p, ls, [figure])
        self.assertIs(result[0][1], figure)
        self.assertEqual(len(result), 7)

    def test_headers_require_context_and_sequence(self):
        pages = []
        for i in range(3):
            p = page([line('Recurring running title', (50, 20, 230, 30), size=8),
                      line('Meaningful edge text', (50, 740, 240, 750), size=8),
                      line('Body content', (50, 80, 260, 92)),
                      line(str(10+i), (280, 775, 295, 785), size=8)])
            p.index = p.pdf_page = i
            pages.append(p)
        mark_headers_footers(pages)
        self.assertEqual([p.lines[0].role for p in pages], ['header']*3)
        # No body above this bottom fragment in the same x region? It is
        # repeated and isolated, so add a unique footnote to test preservation.
        unique = page([line('A unique marginal note', (50, 765, 230, 775), size=8)])
        mark_headers_footers([unique])
        self.assertEqual(unique.lines[0].role, 'body')
        self.assertTrue(all(p.lines[-1].role == 'folio' for p in pages))

    def test_caption_does_not_take_other_column_art(self):
        cap = [line('FIGURE 1. Caption', (40, 400, 230, 412))]
        p = page(cap)
        graphics = [{'bbox': (320, 250, 530, 390), 'kind': 'image'}]
        self.assertIsNone(figure_region_for_caption(p, cap, graphics, [(0,600)], []))

    def test_overprinted_spans_deduplicated_but_repeated_words_retained(self):
        with pymupdf.open() as doc:
            p = doc.new_page()
            p.insert_text((50, 100), 'Repeated words')
            p.insert_text((50, 100), 'Repeated words')
            p.insert_text((50, 130), 'Repeated words')
            info = extract_page(doc, 0, 0, 'test.pdf')
            self.assertEqual(sum(l.text.count('Repeated words') for l in info.lines), 2)
            self.assertTrue(any(d['action'] == 'exclude-duplicate' for d in info.diagnostics))

    def test_export_preserves_pixels_and_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as tmp, pymupdf.open() as doc:
            p = doc.new_page(width=300, height=300)
            p.draw_rect((50,50,200,150), fill=(.85,.88,.9))
            p.insert_text((70,100), 'Diagram label')
            path = Path(tmp)/'crop.png'
            export_region(doc, 0, (50,50,200,150), path, dpi=72, pad=0)
            expected = p.get_pixmap(clip=pymupdf.Rect(50,50,200,150), alpha=False)
            with Image.open(path) as actual:
                self.assertEqual(actual.size, (150,100))
                self.assertIsNone(ImageChops.difference(actual, Image.frombytes('RGB', actual.size, expected.samples)).getbbox())

    def test_changing_column_counts_in_horizontal_bands(self):
        upper = [line(f'upper{c}{r}', (40+c*300, 100+r*18, 260+c*300, 112+r*18)) for c in range(2) for r in range(3)]
        lower = [line(f'lower{c}{r}', (40+c*190, 300+r*18, 190+c*190, 312+r*18)) for c in range(3) for r in range(3)]
        p = page(upper+lower); p.columns = [(0,600)]
        ordered = [o.text for _,o in order_items(p, p.lines, [])]
        self.assertEqual(ordered, [l.text for l in upper+lower])

    def test_multicomponent_raster_figure(self):
        cap = [line('FIGURE 2. Several panels', (50,430,230,442))]
        ls = [line('Surrounding body text stays in the right column.', (300,120,550,132))]
        p = page(cap+ls)
        graphics = [{'kind':'image', 'bbox':(80,100+i*80,180,150+i*80)} for i in range(4)]
        found = figure_region_for_caption(p, cap, graphics, [(0,250),(280,600)], [])
        self.assertEqual(found[0], (80,100,180,390))

    def test_ocr_preserves_native_text_and_suppresses_overlap(self):
        from bits_tool.ocr_engine import ocr_page, OcrEngine
        p = page([line('Native', (50,100,100,112))])
        class Cache:
            def get(self, pno, engine):
                return {'words': [{'t':'Native', 'b':(50,100,100,112), 'c':90, 'k':(0,0)},
                                  {'t':'Scanned', 'b':(50,150,100,162), 'c':90, 'k':(1,0)}]}
        ocr_page(None, 0, p, OcrEngine(), Cache(), ['eng'], 72)
        self.assertEqual([l.text for l in p.lines], ['Native', 'Scanned'])
        self.assertEqual(p.diagnostics[-1]['action'], 'exclude-ocr-overlap')

    def test_zone_edit_recomputes_mathml(self):
        from bits_tool.zoning import ZoneEditor
        editor = object.__new__(ZoneEditor)
        l = line('a = b', (100,100,150,112)); l.uid = '0:1'
        editor.lines = {'0:1':l}; editor.page_by = {0:page([l])}
        n = Node('disp-formula', page=0, bbox=l.bbox,
                 meta={'_ln':['0:1'], 'mathml':{'tag':'mi','text':'STALE'}})
        editor._rebuild(n, {})
        self.assertNotIn('STALE', json.dumps(n.meta['mathml']))
        self.assertIn('a', json.dumps(n.meta['mathml']))

    def test_inline_math_survives_paragraph_rebuild_and_rejects_italic_prose(self):
        from bits_tool.paragraph_detector import append_line, Hyphenation, line_runs
        spans = [Span('The rate ', 'Times', 11, bbox=(0,100,50,112)),
                 Span('v', 'Times-Italic', 11, italic=True, bbox=(50,100,56,112)),
                 Span(' = ', 'Times', 11, bbox=(56,100,68,112)),
                 Span('k', 'Times-Italic', 11, italic=True, bbox=(68,100,74,112)),
                 Span(' varies.', 'Times', 11, bbox=(74,100,115,112))]
        runs = []
        append_line(runs, Line(spans,(0,100,115,112),0), Hyphenation())
        self.assertEqual([r['k'] for r in runs], ['t','math','t'])
        prose = line('The result = a different interpretation.', (50,100,400,112), italic=True)
        self.assertFalse(any(r['k']=='math' for r in line_runs(prose)))

    def test_extraction_cache_roundtrip_retains_baselines(self):
        from bits_tool.text_extractor import page_to_json, page_from_json
        with pymupdf.open() as d:
            p = d.new_page(); p.insert_text((50,100),'Cached text')
            first = extract_page(d,0,0,'test.pdf')
            restored = page_from_json(json.loads(json.dumps(page_to_json(first))),0)
            self.assertEqual(first.lines[0].spans[0].origin, restored.lines[0].spans[0].origin)
            self.assertEqual(first.lines[0].text, restored.lines[0].text)

    def test_formula_is_sibling_inside_list_item(self):
        from bits_tool.bits_generator import BitsGenerator
        from bits_tool.dtd_analyzer import find_dtd, load_rules
        from bits_tool.id_generator import IdGenerator
        from bits_tool.target_registry import TargetRegistry
        from bits_tool.equation_detector import build_mathml_ast
        cfg = Config(DEFAULTS, ROOT)
        rules = load_rules(str(find_dtd(ROOT/'DTD')))
        # Use the real BITS serializer, not just a Node ownership assertion.
        gen = BitsGenerator(rules, IdGenerator('test'), TargetRegistry(), IssueLog(), cfg, None)
        parent = etree.Element('list-item')
        gen.block(parent, Node('p', id='test-p1', inlines=[{'k':'t','text':'List text','st':[]}]))
        eq = Node('disp-formula', id='test-eq1', meta={'text':'x = 1', 'mathml':build_mathml_ast([line('x = 1',(100,100,140,112))])})
        gen.block(parent, eq)
        self.assertEqual([c.tag for c in parent], ['p','disp-formula'])
        self.assertFalse(parent.xpath('./p/disp-formula'))


class FullConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        cls.pdf = cls.folder/'synthetic.pdf'
        fixture(cls.pdf)
        cfg = Config.load(overrides={'paths': {k: str(ROOT/v) for k,v in DEFAULTS['paths'].items()},
                                    'ocr': {'engine':'none'}, 'images': {'dpi':72,'format':'png'},
                                    'mode':'development'})
        cfg.set('paths.cache_dir', str(cls.folder/'cache'))
        cls.result = BookConverter(cfg).convert(cls.pdf, cls.folder/'converted')
        cls.tree = etree.parse(cls.result.xml_path, etree.XMLParser(load_dtd=False))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_xml_dtd_ids_links_assets(self):
        v = self.result.validation
        self.assertNotEqual(self.result.status, 'FAILED', v.get('gate'))
        self.assertEqual(v['content']['coverage'], 1.0)
        self.assertTrue(v['dtd']['valid'], v['dtd']['errors'])
        self.assertFalse(v['images']['missing'])
        self.assertFalse(v['images']['unreadable'])
        ids = self.tree.xpath('//@id')
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(self.result.failed_pages)

    def test_fraction_structure_survives_zoning_and_serialization(self):
        ns = {'m':'http://www.w3.org/1998/Math/MathML'}
        self.assertEqual(len(self.tree.xpath('//disp-formula', namespaces=ns)), 2)
        self.assertEqual(len(self.tree.xpath('//m:mfrac', namespaces=ns)), 1)
        self.assertEqual(len(self.tree.xpath('//m:msub', namespaces=ns)), 2)
        self.assertFalse(self.tree.xpath('//p/disp-formula'))
        eq = self.tree.xpath('//disp-formula')[0]
        self.assertIn('v', ''.join(eq.itertext()))

    def test_diagram_labels_owned_by_figure_not_paragraphs(self):
        figs = self.tree.xpath('//fig')
        self.assertEqual(len(figs), 1)
        self.assertIn('Synthetic stacked diagram', ''.join(figs[0].itertext()))
        self.assertFalse(self.tree.xpath('//p[normalize-space(.)="S"]'))
        data = json.loads((Path(self.result.out_dir)/'intermediate/document_structure.json').read_text())
        figure = [r for r in data['pages'][1]['regions'] if r['kind']=='figure'][0]
        self.assertLess(figure['meta']['art'][1], 130)
        self.assertGreater(figure['meta']['art'][3], 400)
        self.assertLess(figure['meta']['art'][2], 285)

    def test_merged_table_cells_and_inline_math(self):
        ns = {'m': 'http://www.w3.org/1998/Math/MathML'}
        self.assertEqual(len(self.tree.xpath('//table-wrap')), 1)
        self.assertTrue(self.tree.xpath('//td[@colspan="2"]'))
        self.assertTrue(self.tree.xpath('//td[@rowspan="2"]'))
        self.assertEqual(len(self.tree.xpath('//inline-formula/m:math', namespaces=ns)), 1)
        text = ''.join(self.tree.getroot().itertext())
        self.assertEqual(text.count('Spanning row cell'), 1)

    def test_ambiguous_equation_uses_one_reviewable_crop(self):
        eqs = self.tree.xpath('//disp-formula[graphic]')
        self.assertEqual(len(eqs), 1)
        self.assertIn('x = 1', ''.join(eqs[0].itertext()))
        self.assertIn('y = 2', ''.join(eqs[0].itertext()))
        self.assertTrue(any(i['code'] == 'EQUATION_NEEDS_REVIEW' for i in self.result.issues))
        rendered = Renderer('images').equation_content(eqs[0])
        self.assertIn('<img', rendered)

    def test_paragraph_and_preview(self):
        paragraphs = [''.join(p.itertext()) for p in self.tree.xpath('//p')]
        self.assertTrue(any('starts here and continues onto this second line' in t for t in paragraphs))
        self.assertEqual(preview([self.result.xml_path, '--no-open']), 0)
        html = Path(self.result.xml_path).with_name('synthetic_preview.html').read_text()
        self.assertIn('<mfrac>', html)
        self.assertIn('xmlns="http://www.w3.org/1998/Math/MathML"', html)
        self.assertNotIn('&lt;mfrac', html)
