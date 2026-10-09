"""Independent positive/negative geometry and QC checks; PDFs created locally."""
import io
import unittest
from lxml import etree
from PIL import Image
import pymupdf
from bits_tool.config import Config
from bits_tool.converter import quality_gate
from bits_tool.document_tree import Node, IssueLog, PageInfo
from bits_tool.text_extractor import page_images, page_drawings
from bits_tool.math_geometry import horizontal_arrow
from bits_tool.equation_detector import reconstruct_math
from bits_tool.recognition_qc import mathml_errors, audit_recognition
from bits_tool.chemical_detector import chemical_regions
from bits_tool.layout_analyzer import BookStyle
from bits_tool.test_equation_pipeline import line, tags


class RecognitionTests(unittest.TestCase):
    def test_nested_bitmap_clips_restore_and_rotation(self):
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), (200, 40, 40)).save(buf, format='PNG')
        with pymupdf.open() as doc:
            p = doc.new_page(width=300, height=300)
            xref = p.insert_image((30, 80, 260, 220), stream=buf.getvalue(), keep_proportion=False)
            stream = p.get_contents()[0]
            doc.update_stream(stream, b'q 50 100 100 100 re W n q 70 120 120 100 re W n\n' +
                              doc.xref_stream(stream) + b'\nQ Q')
            p.insert_image((30, 80, 260, 220), xref=xref, keep_proportion=False)
            for rotation in (0, 90, 180, 270):
                p.set_rotation(rotation)
                images = page_images(p, (0, 0, 300, 300))
                self.assertEqual(images[0]['bbox'], (70., 100., 150., 180.))
                self.assertEqual(images[1]['bbox'], (30., 80., 260., 220.))
                self.assertTrue(all(i['clip_status'] == 'verified' for i in images))
                self.assertEqual(p.rotation, rotation)

    def test_compound_path_bounds_include_disconnected_endpoints(self):
        from unittest.mock import Mock
        page = Mock()
        page.get_drawings.return_value = [{'rect': pymupdf.Rect(50, 50, 70, 70),
            'items': [('l', pymupdf.Point(50, 50), pymupdf.Point(70, 70)),
                      ('l', pymupdf.Point(40, 20), pymupdf.Point(90, 100))]}]
        self.assertEqual(page_drawings(page)[0]['bbox'], (40., 20., 90., 100.))

    def test_soft_mask_bounds_and_graphics_state_restore(self):
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), (200, 40, 40)).save(buf, format='PNG')
        with pymupdf.open() as doc:
            p = doc.new_page(width=300, height=300)
            xref = p.insert_image((30, 80, 260, 220), stream=buf.getvalue(), keep_proportion=False)
            mask = doc.get_new_xref()
            doc.update_object(mask, '<</Type/XObject/Subtype/Form/FormType 1/BBox[70 120 190 220]/Resources<<>>/Group<</S/Transparency/CS/DeviceGray>>>>')
            doc.update_stream(mask, b'1 g 70 120 120 100 re f')
            state = doc.get_new_xref()
            doc.update_object(state, f'<</Type/ExtGState/SMask<</S/Alpha/G {mask} 0 R>>>>')
            resource = int(doc.xref_get_key(p.xref, 'Resources')[1].split()[0])
            doc.xref_set_key(resource, 'ExtGState', f'<</Mask {state} 0 R>>')
            stream = p.get_contents()[0]
            doc.update_stream(stream, b'q /Mask gs\n' + doc.xref_stream(stream) + b'\nQ')
            p.insert_image((30, 80, 260, 220), xref=xref, keep_proportion=False)
            images = page_images(p, (0, 0, 300, 300))
            self.assertEqual(images[0]['bbox'], (70., 80., 190., 180.))
            self.assertEqual(images[1]['bbox'], (30., 80., 260., 220.))
            self.assertTrue(all(i['clip_status'] == 'verified' for i in images))

    def test_vector_region_rotation_matches_raster_export(self):
        import tempfile
        from pathlib import Path
        from bits_tool.figure_detector import export_region, export_vector_region
        with tempfile.TemporaryDirectory() as td, pymupdf.open() as doc:
            p = doc.new_page(width=300, height=300)
            p.draw_rect((50, 70, 100, 110), color=(1, 0, 0), fill=(1, 0, 0))
            p.draw_circle((180, 100), 10, color=(0, 0, 1), fill=(0, 0, 1))
            for rot in (0, 90, -90):
                svg, raster = Path(td) / 'region.svg', Path(td) / 'region.png'
                export_vector_region(doc, 0, (40, 60, 210, 130), svg, rot=rot)
                export_region(doc, 0, (40, 60, 210, 130), raster, dpi=72, pad=0, rot=rot)
                with pymupdf.open(svg) as vector:
                    rendered = vector[0].get_pixmap(alpha=False)
                im = Image.open(raster).convert('RGB')
                self.assertEqual((rendered.width, rendered.height), im.size)
                # Sample interiors of the two colored shapes after rotation.
                vim = Image.frombytes('RGB', im.size, rendered.samples)
                for x in range(5, im.width, 10):
                    for y in range(5, im.height, 10):
                        a, b = im.getpixel((x, y)), vim.getpixel((x, y))
                        if a in ((255, 0, 0), (0, 0, 255)):
                            self.assertEqual(a, b)

    def test_arrow_requires_shaft_and_head_geometry(self):
        arrow = {'bbox': (0, 0, 40, 8), 'paths': [
            ['l', (0, 4), (40, 4)], ['l', (34, 0), (40, 4)], ['l', (40, 4), (34, 8)]]}
        self.assertEqual(horizontal_arrow(arrow), 1)
        for paths in ([['re', (0, 0), (40, 8)]], arrow['paths'][1:],
                      [['l', (0, 0), (40, 0)], ['l', (40, 0), (40, 8)], ['l', (40, 8), (0, 8)]]):
            self.assertIsNone(horizontal_arrow({**arrow, 'paths': paths}))

    def test_fraction_does_not_swallow_unknown_vectors_or_radicals(self):
        ls = [line('x', (100, 80, 130, 90)), line('y', (100, 106, 130, 116))]
        bar = {'bbox': (98, 99, 132, 99), 'hline': True}
        self.assertIn('mfrac', tags(reconstruct_math(ls, [bar])[0]))
        self.assertIsNone(reconstruct_math(ls, [bar, {'bbox': (96, 80, 99, 116), 'hline': False}])[0])
        ls[0] = line('√x', (100, 80, 130, 90))
        self.assertIsNone(reconstruct_math(ls, [bar])[0])

    def test_overlap_fraction_ownership_requires_review(self):
        ls = [line('x', (100, 80, 130, 90)), line('y', (100, 106, 130, 116))]
        bars = [{'bbox': (98, y, 132, y), 'hline': True} for y in (99, 101)]
        self.assertIsNone(reconstruct_math(ls, bars)[0])

    def test_soft_hyphen_does_not_create_empty_math_operator(self):
        ast, _ = reconstruct_math([line('x\u00ad = 1\x08', (10, 10, 70, 20))])
        self.assertEqual([c.get('text') for c in ast['children']], ['x', '=', '1'])

    def test_mathml_invalid_arity_and_namespace_are_failures(self):
        math = etree.fromstring('<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mi>x</mi></mfrac><mo/></math>')
        self.assertIn('mfrac requires 2 children', mathml_errors(math))
        self.assertIn('empty mo token', mathml_errors(math))
        self.assertTrue(mathml_errors(etree.fromstring('<math><mi>x</mi></math>')))
        tree = etree.ElementTree(etree.fromstring('<book><disp-formula id="eq"/></book>'))
        tree.find('.//disp-formula').append(math)
        issues = IssueLog()
        report = audit_recognition(Node('book', children=[Node('disp-formula', id='eq')]), [], tree, issues)
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(issues.count('error'), 1)

    def test_unrecognized_equation_stays_in_review_queue(self):
        tree = etree.ElementTree(etree.fromstring('<book><disp-formula id="eq"><graphic/></disp-formula></book>'))
        root = Node('book', children=[Node('disp-formula', id='eq', meta={'href': 'eq.png', 'reason': 'unknown vector'})])
        report = audit_recognition(root, [], tree, IssueLog())
        self.assertEqual(report['status'], 'REVIEW')
        self.assertEqual(report['items'][0]['reason'], 'unknown vector')

    def test_strict_qc_rejects_unresolved_recognition(self):
        v = {'dtd': {'valid': True}, 'ids': {'duplicates': [], 'invalid': [], 'pattern_violation_count': 0},
             'links': {'broken': [], 'wrong_type': []}, 'images': {'missing': [], 'unreadable': []},
             'content': {'coverage': 1.0}, 'recognition': {'status': 'REVIEW'}}
        issues = IssueLog()
        issues.add('RECOGNITION_QC_REVIEW', 'warning', 'needs review')
        self.assertEqual(quality_gate(v, [], issues, Config.load()), 'PASS WITH WARNINGS')
        cfg = Config.load(overrides={'qa': {'fail_on_recognition_review': True}})
        self.assertEqual(quality_gate(v, [], issues, cfg), 'FAILED')

    def test_short_ordinary_text_does_not_become_chemistry(self):
        p = PageInfo(0, 'synthetic.pdf', 0, 600, 800, (0, 0, 600, 800),
                     lines=[line(t, (50, 50+i*20, 150, 60+i*20)) for i, t in enumerate(['NO', 'ON', 'CO', 'H', 'C'])])
        self.assertEqual(chemical_regions(p, BookStyle(), []), [])
        self.assertTrue(all(l.role == 'body' for l in p.lines))
