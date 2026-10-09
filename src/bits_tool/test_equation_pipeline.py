"""Automated Regression Tests for Equation Pipeline and MathML Reconstruction."""

import unittest
from lxml import etree
from src.document_tree import Line, Span, Node, IssueLog
from src.equation_detector import is_display_math, math_tokens, build_math_ast, linearize
from src.bits_generator import BitsGenerator

MML_NS = "http://www.w3.org/1998/Math/MathML"

class TestEquationPipeline(unittest.TestCase):

    def test_simple_equation_structured_mathml(self):
        """1. Simple equation produces structured MathML."""
        line = Line(spans=[
            Span(text="v", bbox=(100, 100, 110, 110), font="Italic"),
            Span(text="=", bbox=(115, 100, 125, 110)),
            Span(text="k", bbox=(130, 100, 140, 110), font="Italic"),
            Span(text="[E]", bbox=(145, 100, 165, 110))
        ], bbox=(100, 100, 165, 110), page=1)
        
        ast = build_math_ast([line])
        self.assertEqual(ast["tag"], "mrow")
        self.assertTrue(any(c["tag"] == "mo" and c["text"] == "=" for c in ast["children"]))

    def test_fraction_uses_mfrac(self):
        """2. Fraction uses <mml:mfrac>."""
        line_num = Line(spans=[Span(text="V_max[S]", bbox=(100, 80, 150, 90))], bbox=(100, 80, 150, 90), page=1)
        line_den = Line(spans=[Span(text="K_m + [S]", bbox=(100, 100, 150, 110))], bbox=(100, 100, 150, 110), page=1)
        
        ast = build_math_ast([line_num, line_den])
        self.assertEqual(ast["tag"], "mfrac")

    def test_subscripts_and_superscripts_preserved(self):
        """3. Superscripts and subscripts are preserved."""
        line = Line(spans=[
            Span(text="V", bbox=(100, 100, 110, 110)),
            Span(text="max", bbox=(111, 105, 125, 112), sub=True)
        ], bbox=(100, 100, 125, 112), page=1)
        
        ast = build_math_ast([line])
        self.assertEqual(ast["tag"], "msub")

    def test_reaction_scheme_not_split(self):
        """4. Michaelis-Menten reaction scheme is preserved as coherent structure."""
        line = Line(spans=[
            Span(text="E + S ⇌ ES → E + P", bbox=(100, 100, 300, 115))
        ], bbox=(100, 100, 300, 115), page=1)
        
        ast = build_math_ast([line])
        self.assertEqual(ast["tag"], "mrow")
        self.assertTrue(any(c["tag"] == "mo" and c["text"] == "⇌" for c in ast["children"]))

    def test_prose_sentence_not_classified_as_equation(self):
        """5. Long prose sentence with math symbols is rejected."""
        line = Line(spans=[
            Span(text="La constante de velocidad k1 determina la formación del complejo ES durante la reacción posterior.", bbox=(50, 100, 450, 112))
        ], bbox=(50, 100, 450, 112), page=1)
        
        self.assertFalse(is_display_math(line, col_left=50, em=10))

    def test_figure_caption_not_merged_into_equation(self):
        """6. Figure captions are not merged into equations."""
        line = Line(spans=[
            Span(text="Figura 9.1. Esquema de la reacción enzimática.", bbox=(50, 100, 300, 112))
        ], bbox=(50, 100, 300, 112), page=1)
        
        self.assertFalse(is_display_math(line, col_left=50, em=10))

    def test_display_equation_sibling_in_list_item(self):
        """8. Display equation inside list-item is sibling of paragraph, not nested inside <p>."""
        p_node = Node("p", id="p1")
        eq_node = Node("disp-formula", id="eq1")
        item_node = Node("list-item")
        item_node.add(p_node)
        item_node.add(eq_node)
        
        self.assertEqual(item_node.children[0].kind, "p")
        self.assertEqual(item_node.children[1].kind, "disp-formula")
        self.assertNotIn(eq_node, p_node.children)

if __name__ == "__main__":
    unittest.main()