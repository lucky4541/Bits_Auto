"""Spatial regression cases; these are synthetic, not the user's source PDFs."""
import unittest
from bits_tool.document_tree import Line, Span, PageInfo
from bits_tool.equation_detector import build_mathml_ast, equation_regions, is_display_math
from bits_tool.layout_analyzer import BookStyle


def line(text, box, size=10, **style):
    return Line([Span(text, "Times-Roman", size, bbox=box, **style)], box, 0)


def tags(ast):
    if ast is None:
        return []
    return [ast['tag']] + [t for c in ast.get('children', []) for t in tags(c)]


class TestEquationPipeline(unittest.TestCase):
    def test_simple_equation(self):
        ast = build_mathml_ast([line('v = k[E]', (100, 100, 160, 112))])
        self.assertIn('mo', tags(ast))
        self.assertNotIn('mtext', tags(ast))

    def test_michaelis_menten_fraction_and_left_hand_side(self):
        ls = [line('v =', (60, 93, 80, 105)), line('V_max[S]', (100, 80, 160, 90)),
              line('K_m + [S]', (100, 106, 160, 116))]
        bar = {'bbox': (98, 99, 162, 99), 'hline': True}
        ast = build_mathml_ast(ls, [bar])
        self.assertEqual(ast['children'][0]['text'], 'v')
        self.assertEqual(ast['children'][1]['text'], '=')
        self.assertEqual(tags(ast).count('mfrac'), 1)
        self.assertEqual(tags(ast).count('msub'), 2)

    def test_stacked_lines_are_not_assumed_fraction(self):
        self.assertIsNone(build_mathml_ast([line('x = 1', (100, 80, 150, 90)), line('y = 2', (100, 100, 150, 110))]))

    def test_script_geometry(self):
        a, b = line('V', (100, 100, 110, 112)), line('max', (111, 108, 128, 116), size=7, sub=True)
        ast = build_mathml_ast([a, b])
        self.assertIn('msub', tags(ast))
        self.assertIn('msubsup', tags(build_mathml_ast([line('x_1^2', (100, 100, 140, 112))])))

    def test_michaelis_menten_reaction_labels(self):
        ls = [line('E + S', (50, 100, 90, 112)), line('⇌', (100, 100, 130, 112)),
              line('ES', (145, 100, 165, 112)), line('→', (180, 100, 210, 112)),
              line('E + P', (225, 100, 265, 112)), line('k_1', (105, 82, 125, 92)),
              line('k_2', (105, 122, 125, 132)), line('k_3', (185, 82, 205, 92))]
        ast = build_mathml_ast(ls)
        self.assertIn('munderover', tags(ast))
        self.assertIn('mover', tags(ast))
        self.assertEqual(tags(ast).count('msub'), 3)
        p = PageInfo(0, 'test.pdf', 0, 600, 800, (0, 0, 600, 800), lines=ls, columns=[(0, 600)])
        regions = equation_regions(p, BookStyle())
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0].meta['lines']), 8)

    def test_short_words_and_italic_prose_are_not_equations(self):
        for text in ('S', 'Introduction', 'An italic title', 'Figura 9.1. Reaction scheme', 'The rate v = k changes with temperature.'):
            self.assertFalse(is_display_math(line(text, (100, 100, 400, 112), italic=True), 50, 10))

    def test_other_column_not_absorbed(self):
        ls = [line('x = 1', (60, 100, 150, 112)), line('y = 2', (350, 100, 450, 112))]
        p = PageInfo(0, 'test.pdf', 0, 600, 800, (0, 0, 600, 800), lines=ls, columns=[(0, 280), (320, 600)])
        self.assertEqual(len(equation_regions(p, BookStyle())), 2)

    def test_unsupported_root_retained_for_review(self):
        l = line('v = √x', (100, 100, 200, 115))
        p = PageInfo(0, 'test.pdf', 0, 600, 800, (0, 0, 600, 800), lines=[l])
        region = equation_regions(p, BookStyle())[0]
        self.assertIsNone(region.meta['mathml'])
        self.assertEqual(region.meta['fallback_image'], l.bbox)
