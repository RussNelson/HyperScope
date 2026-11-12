"""Checks for the HyperScope PySide2 port.

Run from the pyside2 directory:

    QT_QPA_PLATFORM=offscreen python3 tests/test_core.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide2.QtCore import Qt, QEvent, QUrl
from PySide2.QtGui import QKeyEvent
from PySide2.QtWidgets import QApplication, QDialog

from hs.address import (
    IndirectLink,
    NodeID,
    NodeNumber,
    Relative,
    apply_view,
    normalize_user_address,
    parse_address,
    resolve,
)
from hs.commands import CommandSession
from hs.errors import HyperscopeError
from hs.model import (
    _CACHE,
    load_opml,
    offset_pieces,
    plain_text,
    to_node_number,
)
from hs.render import render_document, visible_nodes
from hs.app import MainWindow, ViewspecDialog, default_document, demo_directory
from hs.viewspec import CurrentViewspecs

DEMOS = demo_directory()
TUTOR = DEMOS / "tutor-hyperscope.opml"

_APP = None


def setUpModule():
    global _APP
    _APP = QApplication.instance() or QApplication([])


def _opml(body, head="", namespace=True):
    ns = (
        ' xmlns:hs="http://www.hyperscope.org/hyperscope/opml/public/2006/05/09"'
        if namespace else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<opml version="2.0"%s>'
        "<head>%s</head><body>%s</body></opml>"
    ) % (ns, head, body)


class AddressAndNumberTests(unittest.TestCase):
    def test_numbering_alternates_decimal_and_letters(self):
        self.assertEqual(to_node_number("0", 1), "1")
        self.assertEqual(to_node_number("0", 10), "10")
        self.assertEqual(to_node_number("2", 1), "2A")
        self.assertEqual(to_node_number("2", 26), "2Z")
        self.assertEqual(to_node_number("2", 27), "2AA")
        self.assertEqual(to_node_number("2", 28), "2AB")
        self.assertEqual(to_node_number("2A", 3), "2A3")
        self.assertEqual(to_node_number("2A3", 2), "2A3B")

    def test_offset_reads_letters_as_decimal_digits(self):
        self.assertEqual(offset_pieces("2A3"), [2, 1, 3])
        # A is 1, so AA is the number 11, not the 27th sibling.
        self.assertEqual(offset_pieces("AA"), [11])
        self.assertEqual(offset_pieces("J"), [10])
        with self.assertRaises(HyperscopeError):
            offset_pieces("0")

    def test_tokens_and_user_addresses(self):
        address = parse_address("2A3B")
        self.assertEqual(len(address.pieces), 1)
        self.assertIsInstance(address.pieces[0], NodeNumber)
        self.assertEqual(address.pieces[0].number, "2A3B")
        self.assertFalse(address.pieces[0].is_offset)

        address = parse_address("02")
        self.assertIsInstance(address.pieces[0], NodeID)
        address = parse_address("0")
        self.assertIsInstance(address.pieces[0], NodeNumber)
        self.assertEqual(address.pieces[0].number, "0")

        address = parse_address("!2A")
        self.assertTrue(address.pieces[0].is_offset)

        address = parse_address("Figure-13@")
        self.assertEqual(address.pieces[0].label, "Figure-13")

        address = parse_address(".ub")
        self.assertIsInstance(address.pieces[0], Relative)
        self.assertIsInstance(address.pieces[1], Relative)
        address = parse_address(".l")
        self.assertIsInstance(address.pieces[0], IndirectLink)

        self.assertEqual(normalize_user_address("2"), "#2")
        self.assertEqual(normalize_user_address(".u"), "#.u")
        self.assertEqual(normalize_user_address(".u:x"), "#.u:x")
        self.assertEqual(normalize_user_address("INTRODUCTION"), "#INTRODUCTION")
        self.assertEqual(normalize_user_address(":x"), "#:x")
        self.assertEqual(normalize_user_address("./tutor.opml"), "./tutor.opml")
        self.assertEqual(normalize_user_address("tutor.opml#2"), "tutor.opml#2")
        self.assertEqual(normalize_user_address("/tmp/a.opml"), "/tmp/a.opml")

    def test_bad_addresses_are_reported(self):
        for text in ("#:R", "#:M", "#:N", "+2w", ".c", "$Outside"):
            with self.assertRaises(HyperscopeError):
                resolve(text, relative_to=self.doc)

    def setUp(self):
        _CACHE.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tiny.opml"
        self.path.write_text(_opml(
            '<outline text="Root"><outline text="Only"/></outline>'
        ), encoding="utf-8")
        self.doc = load_opml(self.path)

    def tearDown(self):
        self.directory.cleanup()
        _CACHE.clear()

    def test_string_position_and_external_label(self):
        with self.assertRaises(HyperscopeError) as caught:
            resolve("#+2w", relative_to=self.doc)
        self.assertIn("string positioning", str(caught.exception))
        with self.assertRaises(HyperscopeError) as caught:
            resolve("#$Outside", relative_to=self.doc)
        self.assertIn("external", str(caught.exception))
        with self.assertRaises(HyperscopeError):
            resolve("#.c", relative_to=self.doc)


class ViewspecTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        view = CurrentViewspecs()
        self.assertEqual(view.to_string(), "")
        self.assertTrue(view.show_addressing)
        self.assertTrue(view.show_labels)
        self.assertFalse(view.show_signatures)
        self.assertEqual(view.level_clip, 63)
        self.assertEqual(view.line_clip, 63)
        self.assertTrue(view.show_blank_lines)

        self.assertEqual(CurrentViewspecs("n").to_string(), "n")
        self.assertEqual(CurrentViewspecs("x").to_string(), "x")
        self.assertEqual(CurrentViewspecs("z").to_string(), "z")
        self.assertEqual(CurrentViewspecs("O").to_string(), "O")
        self.assertEqual(CurrentViewspecs("o").to_string(), "o")
        self.assertEqual(CurrentViewspecs("fuvEF").to_string(), "")

        self.assertEqual(CurrentViewspecs("xw").level_clip, 63)
        self.assertEqual(CurrentViewspecs("wx").level_clip, 1)
        self.assertEqual(CurrentViewspecs("wx").line_clip, 1)
        self.assertEqual(CurrentViewspecs("d" + ("b" * 100)).level_clip, 63)
        self.assertEqual(CurrentViewspecs("daaa").level_clip, 0)
        self.assertEqual(CurrentViewspecs("t" + ("q" * 5)).line_clip, 1)
        self.assertEqual(CurrentViewspecs("t" + ("r" * 100)).line_clip, 63)

        clipped = CurrentViewspecs("e", context_level=2)
        self.assertEqual(clipped.level_clip, 2)

    def test_unknown_letters(self):
        for letter in ("R", "M", "N", "1"):
            with self.assertRaises(HyperscopeError) as caught:
                CurrentViewspecs(letter)
            self.assertTrue(str(caught.exception).startswith("? "))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()
        _CACHE.clear()

    def write(self, name, body, head=""):
        path = self.root / name
        path.write_text(_opml(body, head), encoding="utf-8")
        return path

    def test_single_and_multiple_roots(self):
        path = self.write("one.opml", """
            <outline text="Origin" hs:nid="01" hs:label="Origin" hs:createdBy="Ada" hs:createdOn="2006">
              <outline text="First" hs:nid="02" hs:label="First"/>
              <outline text="Second" hs:nid="03">
                <outline text="Second child"/>
              </outline>
            </outline>
        """)
        doc = load_opml(path)
        self.assertEqual([node.number for node in doc.order], ["0", "1", "2", "2A"])
        self.assertEqual(doc.context.number, "0")
        self.assertEqual(doc.by_nid["03"].number, "2")

        many = self.write("many.opml", """
            <outline text="First"/>
            <outline text="Second"><outline text="Child"/></outline>
        """)
        doc = load_opml(many)
        self.assertEqual([node.number for node in doc.order], ["1", "2", "2A"])
        self.assertNotIn("0", doc.by_number)

    def test_deep_letters_and_offsets(self):
        children = "".join("<outline text='c%s'/>" % i for i in range(1, 28))
        path = self.write("deep.opml", """
            <outline text="root">
              <outline text="one"/>
              <outline text="two">%s</outline>
            </outline>
        """ % children)
        doc = load_opml(path)
        self.assertEqual(doc.by_number["2"].text, "two")
        self.assertEqual(doc.by_number["2AA"].text, "c27")
        self.assertEqual(doc.by_number["2A"].text, "c1")
        # An offset is applied from the origin of a freshly loaded file,
        # even if another statement was on screen. !2A is the origin's
        # second child, then that child's first child.
        parked = resolve(str(path) + "#1")
        self.assertEqual(parked.context.text, "one")
        landed = resolve("#!2A", relative_to=parked)
        self.assertEqual(landed.context.text, "c1")
        self.assertEqual(resolve(str(path) + "#2K").context.text, "c11")

    def test_jumps(self):
        path = self.write("jump.opml", """
            <outline text="Origin">
              <outline text="A" hs:label="Alpha">
                <outline text="A1" hs:label="Inner"/>
                <outline text="A2"/>
              </outline>
              <outline text="B" hs:label="Beta">
                <outline text="B1"/>
              </outline>
              <outline text="See &lt;a href=&quot;#2&quot;&gt;bee&lt;/a&gt; &lt;a name=&quot;mark&quot;&gt;&lt;/a&gt;"/>
            </outline>
        """)
        doc = resolve(str(path) + "#1A")
        self.assertEqual(doc.context.text, "A1")
        self.assertEqual(resolve("#.u", relative_to=doc).context.text, "A")
        self.assertEqual(resolve("#.2u", relative_to=doc).context.number, "0")
        self.assertEqual(resolve("#.9u", relative_to=doc).context.number, "0")
        self.assertEqual(resolve("#.o", relative_to=doc).context.number, "0")
        origin = resolve(str(path))
        self.assertEqual(resolve("#.d", relative_to=origin).context.text, "A")
        self.assertEqual(resolve("#.2d", relative_to=origin).context.text, "A1")
        self.assertEqual(resolve("#1A.d", relative_to=doc).context.text, "A1")
        back = resolve("#1A.b", relative_to=doc)
        self.assertEqual(back.context.text, "A")
        self.assertEqual(resolve("#.b", relative_to=resolve(str(path))).context.number, "0")
        nxt = resolve("#1.n", relative_to=doc)
        self.assertEqual(nxt.context.text, "A1")
        self.assertEqual(resolve("#1.s", relative_to=doc).context.text, "B")
        self.assertEqual(resolve("#2.p", relative_to=doc).context.text, "A")
        tail = resolve("#1.t", relative_to=doc)
        self.assertEqual(tail.context.number, "3")
        self.assertIn("bee", plain_text(tail.context.text))
        # The link statement is 3; its plain text drops the tags.
        self.assertEqual(resolve("#1A.h", relative_to=doc).context.text, "A1")
        self.assertEqual(resolve("#1B.h", relative_to=doc).context.text, "A1")
        self.assertEqual(resolve("#1.e", relative_to=doc).context.text, "A2")
        self.assertEqual(resolve("#Alpha", relative_to=doc).context.text, "A")
        self.assertEqual(resolve("#alpha", relative_to=doc).context.number, "1")
        self.assertEqual(resolve("#*Beta", relative_to=resolve(str(path))).context.text, "B")
        with self.assertRaises(HyperscopeError):
            resolve("#!Beta", relative_to=resolve(str(path) + "#2"))
        self.assertEqual(
            resolve("#!Inner", relative_to=resolve(str(path) + "#1")).context.text,
            "A1",
        )
        self.assertEqual(resolve("#@mark", relative_to=doc).context.number, "3")
        self.assertEqual(resolve("#3.l", relative_to=doc).context.text, "B")
        with self.assertRaises(HyperscopeError):
            resolve('#"no-such-phrase"', relative_to=doc)
        found = resolve('#"second child"'.replace("second child", "A2"), relative_to=doc)
        self.assertEqual(found.context.text, "A2")
        found = resolve("#.o/a2/i", relative_to=doc)
        self.assertEqual(found.context.text, "A2")
        with self.assertRaises(HyperscopeError):
            resolve("#.o/a2/", relative_to=doc)

    def test_view_is_absolute_or_relative_and_e_follows_the_jump(self):
        path = self.write("view.opml", """
            <outline text="Origin">
              <outline text="A"><outline text="A1 apple"/></outline>
              <outline text="B apple"/>
            </outline>
        """)
        doc = resolve(str(path) + "#1A:e")
        self.assertEqual(doc.context.text, "A1 apple")
        self.assertEqual(doc.viewspecs.level_clip, doc.context.level)
        self.assertGreater(doc.context.level, 1)

        hidden = resolve("#:n", relative_to=doc)
        self.assertFalse(hidden.viewspecs.show_addressing)
        kept = resolve("#:x", relative_to=hidden)
        self.assertFalse(kept.viewspecs.show_addressing)
        self.assertEqual(kept.viewspecs.level_clip, 1)
        opened = apply_view(kept, "x")
        self.assertTrue(opened.viewspecs.show_addressing)
        self.assertEqual(opened.viewspecs.level_clip, 1)
        reset = apply_view(kept, "", None)
        self.assertEqual(reset.viewspecs.to_string(), "")
        self.assertEqual(reset.context.number, kept.context.number)

    def test_content_filter_words_and_position(self):
        path = self.write("filt.opml", """
            <outline text="start">
              <outline text="apple one"/>
              <outline text="middle pineapple"/>
              <outline text="apple two"/>
              <outline text="end"/>
            </outline>
        """)
        doc = resolve(str(path) + '#:i;"apple";')
        self.assertEqual(doc.context.text, "apple one")
        shown = [node.text for node in visible_nodes(doc)]
        self.assertIn("apple one", shown)
        self.assertIn("apple two", shown)
        self.assertNotIn("middle pineapple", shown)
        self.assertNotIn("start", shown)

        later = resolve(str(path) + "#4")
        later = resolve('#:i;"apple";', relative_to=later)
        self.assertEqual(later.context.text, "apple two")

        between = resolve(str(path) + "#2")
        between = resolve('#:i;"APPLE";', relative_to=between)
        self.assertEqual(between.context.text, "apple one")

        nxt = resolve(str(path) + '#2:k;"apple";')
        self.assertEqual(nxt.context.text, "apple two")
        with self.assertRaises(HyperscopeError):
            resolve(str(path) + '#3:k;"apple";')
        with self.assertRaises(HyperscopeError):
            resolve(str(path) + '#:i;"zzzz";')

        kept = resolve("#4", relative_to=doc)
        self.assertEqual(kept.content_filter, '"apple"')
        self.assertNotIn("middle pineapple", [node.text for node in visible_nodes(kept)])
        cleared = resolve("#:j", relative_to=kept)
        self.assertIn("middle pineapple", [node.text for node in visible_nodes(cleared)])

    def test_includes_same_file_cross_file_and_failure(self):
        self.write("other.opml", """
            <outline text="Other root">
              <outline text="Other child"><outline text="Other grandchild"/></outline>
              <outline text="Other sibling"/>
            </outline>
        """)
        host = self.write("host.opml", """
            <outline text="Host root" hs:nid="01">
              <outline text="Alpha body" hs:nid="02" hs:label="Alpha"/>
              <outline type="include" url="other.opml#1" text="include node"/>
              <outline type="include" url="other.opml#1" hs:include-type="branch" text="include branch"/>
              <outline type="include" url="other.opml" hs:include-type="plex" text="include plex"/>
              <outline type="include" url="#02" text="include self"/>
              <outline type="include" url="missing.opml" text="include missing"/>
            </outline>
        """)
        plain = resolve(str(host))
        self.assertFalse(plain.viewspecs.sequence_generators)
        self.assertTrue(any(node.include_url for node in plain.order))

        doc = resolve(str(host) + "#:O")
        texts = [plain_text(node.text) for node in doc.order]
        self.assertIn("Alpha body", texts)
        self.assertGreaterEqual(texts.count("Alpha body"), 2)
        self.assertIn("Other child", texts)
        self.assertIn("Other grandchild", texts)
        self.assertIn("Other root", texts)
        self.assertTrue(any(node.include_failed for node in doc.order))
        self.assertTrue(any("Include failed" in node.text for node in doc.order))
        self.assertTrue(any(node.included and node.text == "Other child" and not node.children
                            for node in doc.order))
        self.assertTrue(any(node.included and node.text == "Other child" and node.children
                            for node in doc.order))
        self.assertEqual(doc.context.text, "Host root")

        stayed = resolve(str(host) + "#1:O")
        self.assertEqual(stayed.context.text, "Alpha body")
        self.assertFalse(stayed.context.included)

    def test_include_cycle_finishes(self):
        self.write("a.opml", """
            <outline text="File A">
              <outline type="include" url="b.opml" hs:include-type="branch" text="to B"/>
            </outline>
        """)
        self.write("b.opml", """
            <outline text="File B">
              <outline type="include" url="a.opml" hs:include-type="branch" text="to A"/>
            </outline>
        """)
        doc = resolve(str(self.root / "a.opml") + "#:O")
        blob = " ".join(plain_text(node.text) for node in doc.order)
        self.assertIn("File A", blob)
        self.assertIn("File B", blob)

    def test_labels_entities_bom_and_cache(self):
        path = self.root / "odd.opml"
        xml = """<?xml version="1.0" encoding="UTF-8"?>
            <opml version="2.0" xmlns:hs="http://www.hyperscope.org/hyperscope/opml/public/2006/05/09">
              <head hs:right-label-delim=":"><title>Odd</title></head>
              <body>
                <outline text="Name: the rest"/>
                <outline text="&amp;qufot;figures/x.gif"/>
              </body>
            </opml>"""
        path.write_bytes(b"\xef\xbb\xbf" + xml.encode("utf-8"))
        loaded = load_opml(path)
        self.assertEqual(loaded.order[0].label, "Name:")
        self.assertIn("qufot", loaded.order[1].text)

        resolve(str(path))
        path.write_text(_opml('<outline text="Changed"/>'), encoding="utf-8")
        cached = resolve(str(path))
        self.assertEqual(cached.context.text, "Name: the rest")
        fresh = resolve(str(path), fresh=True)
        self.assertEqual(fresh.context.text, "Changed")

        with self.assertRaises(HyperscopeError):
            load_opml(self.root / "missing.opml")
        bad = self.root / "bad.opml"
        bad.write_text("<html></html>", encoding="utf-8")
        with self.assertRaises(HyperscopeError):
            load_opml(bad)

    def test_other_file_does_not_keep_view_or_filter(self):
        first = self.write("first.opml", '<outline text="One apple"/>')
        self.write("second.opml", '<outline text="Two"/>')
        doc = resolve(str(first) + '#:nx;"apple";')
        self.assertFalse(doc.viewspecs.show_addressing)
        other = resolve("second.opml", relative_to=doc)
        self.assertTrue(other.viewspecs.show_addressing)
        self.assertIsNone(other.content_filter)
        self.assertEqual(other.context.text, "Two")


class RenderTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        body = """
            <outline text="Origin" hs:nid="01" hs:createdBy="Ada" hs:createdOn="2006">
              <outline text="Alpha body" hs:nid="02" hs:label="Code"/>
              <outline text="%s"/>
              <outline text="&lt;a href=&quot;#1&quot;&gt;go&lt;/a&gt; &lt;b&gt;short&lt;/b&gt;"/>
              <outline text="&lt;img src=&quot;dot.gif&quot; /&gt;"/>
              <outline text="&lt;a href=&quot;javascript:alert(1)&quot;&gt;js&lt;/a&gt;"/>
              <outline text="&lt;img src=&quot;http://example.com/a.gif&quot; /&gt;"/>
            </outline>
        """ % ("word " * 80)
        path = self.root / "draw.opml"
        path.write_text(_opml(body), encoding="utf-8")
        (self.root / "dot.gif").write_bytes(b"GIF89a")
        self.path = path

    def tearDown(self):
        self.directory.cleanup()
        _CACHE.clear()

    def test_numbers_labels_links_images_and_clipping(self):
        doc = resolve(str(self.path))
        html = render_document(doc)
        self.assertIn("(0)", html)
        self.assertIn("(1)", html)
        self.assertIn("#7a4ea3", html)
        self.assertIn("Alpha body", html)
        self.assertIn("font-style:italic", html)
        self.assertIn(">Code <", html)
        self.assertIn("margin-left: 0.0em", html)
        self.assertIn("margin-left: 1.4em", html)
        self.assertIn("0 0 14px 0", html)
        self.assertIn("#e7f0ff", html)
        self.assertIn("of ", html)
        self.assertIn("statements", html)
        self.assertIn("hs:/jump/1", html)
        self.assertIn("hs:/go/", html)
        self.assertIn("javascript:alert(1)", html)
        self.assertIn("http://example.com/a.gif", html)
        self.assertIn("dot.gif", html)
        self.assertIn("file:", html)

        quiet = resolve("#:nDzB", relative_to=doc)
        html = render_document(quiet)
        self.assertNotIn("#7a4ea3", html)
        self.assertNotIn("font-style:italic", html)
        self.assertNotIn("margin-left: 1.4em", html)
        self.assertIn("0 0 2px 0", html)

        signed = resolve("#:K", relative_to=doc)
        self.assertIn("Ada", render_document(signed))
        self.assertIn("2006", render_document(signed))

        ids = resolve("#:I", relative_to=doc)
        self.assertIn("(02)", render_document(ids))

        right = resolve("#:G", relative_to=doc)
        page = render_document(right)
        self.assertLess(page.find("Origin"), page.find("(0)"))

        clipped = resolve("#:t", relative_to=doc)
        page = render_document(clipped)
        self.assertIn("…", page)
        self.assertIn("<b>short</b>", page)
        # A one-line clip turns an image statement into text.
        self.assertNotIn("dot.gif", page)
        self.assertNotIn("<img", page.lower())

    def test_structure_level_and_context(self):
        doc = resolve(str(TUTOR))
        self.assertEqual(doc.by_number["1"].label, "INTRODUCTION")
        self.assertEqual(doc.by_number["1A"].label, "Welcome")
        self.assertEqual(doc.by_nid["02"].number, "1")

        branch = resolve("#1:g", relative_to=doc)
        numbers = [node.number for node in visible_nodes(branch)]
        self.assertIn("1", numbers)
        self.assertIn("1A", numbers)
        self.assertNotIn("0", numbers)
        self.assertNotIn("2", numbers)

        plex = resolve("#1A:l", relative_to=doc)
        numbers = [node.number for node in visible_nodes(plex)]
        self.assertIn("1A", numbers)
        self.assertIn("1B", numbers)
        self.assertNotIn("1", numbers)
        self.assertNotIn("2", numbers)

        level = resolve("#:x", relative_to=doc)
        numbers = [node.number for node in visible_nodes(level)]
        self.assertIn("1", numbers)
        self.assertNotIn("1A", numbers)

        deep = resolve("#1A:x", relative_to=doc)
        numbers = [node.number for node in visible_nodes(deep)]
        self.assertIn("1A", numbers)
        self.assertIn("1", numbers)
        self.assertNotIn("1B", numbers)

        html = render_document(resolve("#:x", relative_to=doc))
        self.assertIn("INTRODUCTION", html)
        self.assertNotIn("INTRODUCTION INTRODUCTION", html)


class CommandTests(unittest.TestCase):
    def test_jump_item_and_noise(self):
        session = CommandSession()
        prompt, action = session.type_char("j")
        self.assertIn("Jump (to)", prompt)
        self.assertIsNone(action)
        session.type_char("i")
        self.assertTrue(session.marking())
        prompt, action = session.type_char("2")
        self.assertIn("M/A: 2", prompt)
        self.assertEqual(prompt.count("M/A:"), 1)
        prompt, action = session.enter()
        self.assertIn("V:", prompt)
        self.assertIsNone(action)
        prompt, action = session.enter()
        self.assertEqual(prompt, "BASE C: ")
        self.assertEqual(action["method"], "jump_item")
        self.assertEqual(action["location"], "2")
        self.assertEqual(action["viewspecs"], "")

    def test_invalid_partial_mark_and_repeat(self):
        session = CommandSession()
        prompt, action = session.type_char("q")
        self.assertIsNone(action)
        self.assertTrue(prompt.endswith("?"))
        self.assertEqual(session.prompt(), "BASE C: ")

        session.type_char("j")
        prompt, action = session.type_char("q")
        self.assertTrue(prompt.endswith("?"))
        self.assertIn("Jump (to)", session.prompt())

        session.reset()
        prompt, _action = session.type_char("r")
        self.assertEqual(prompt, "BASE R")
        prompt, action = session.type_char("e")
        self.assertIn("RE", prompt)
        session.type_char("s")
        session.type_char("v")
        prompt, action = session.enter()
        self.assertEqual(action["method"], "reset_viewspecs")

        session.reset()
        for character in " se":
            session.type_char(character)
        session.type_char("v")
        session.type_char("x")
        prompt, action = session.enter()
        self.assertEqual(action["method"], "set_viewspecs")
        self.assertEqual(action["viewspecs"], "x")

        session.reset()
        session.type_char("j")
        session.type_char("i")
        prompt, action = session.mark("2A")
        self.assertIsNone(action)
        self.assertIn("!", prompt)
        prompt, action = session.enter()
        self.assertTrue(action["marked"])
        self.assertEqual(action["location"], "2A")

        session.type_char("j")
        session.type_char("i")
        session.type_char("3")
        session.enter()
        session.enter()
        prompt, action = session.repeat()
        self.assertIsNone(action)
        self.assertIn("V:", prompt)

    def test_persisted_search_and_escape(self):
        session = CommandSession()
        for character in "jcf":
            session.type_char(character)
        for character in "Hello":
            session.type_char(character)
        session.enter()
        _prompt, action = session.enter()
        self.assertEqual(action["method"], "jump_content_first")
        self.assertEqual(action["typein"], "Hello")

        for character in "jcn":
            session.type_char(character)
        self.assertIn("RC/T:", session.prompt())
        self.assertIn("Hello", session.prompt())
        session.enter()
        _prompt, action = session.enter()
        self.assertEqual(action["typein"], "Hello")
        self.assertEqual(action["method"], "jump_content_next")

        session.type_char("j")
        prompt, _action = session.reset()
        self.assertEqual(prompt, "BASE C: ")
        session.enter()
        self.assertEqual(session.prompt(), "BASE C: ")

    def test_end_plex_and_backspace(self):
        session = CommandSession()
        for character in "jep":
            session.type_char(character)
        session.type_char("1")
        session.enter()
        _prompt, action = session.enter()
        self.assertEqual(action["method"], "jump_end_plex")
        session.type_char("j")
        session.backspace()
        self.assertEqual(session.prompt(), "BASE C: ")


class DemoAndWindowTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()
        self.window = MainWindow()
        self.window.show()

    def tearDown(self):
        self.window.close()
        _CACHE.clear()

    def type_command(self, text):
        for character in text:
            if character == " ":
                key = Qt.Key_Space
            elif character.isdigit():
                key = getattr(Qt, "Key_" + character)
            else:
                key = getattr(Qt, "Key_" + character.upper())
            event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, character)
            self.assertTrue(self.window.handle_command_key(event))

    def press(self, key):
        event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, "")
        self.assertTrue(self.window.handle_command_key(event))

    def test_every_demo_parses(self):
        failures = []
        for path in sorted(DEMOS.glob("*.opml")):
            try:
                load_opml(path)
            except HyperscopeError as exc:
                failures.append("%s: %s" % (path.name, exc))
        self.assertEqual(failures, [])

    def test_tutorial_navigation_view_and_history(self):
        self.assertEqual(default_document(), TUTOR)
        names = [action.text() for action in self.window.demo_menu.actions()]
        self.assertIn("tutor-hyperscope.opml", names)

        self.window.open_path(str(TUTOR))
        doc = self.window.document
        self.assertEqual(doc.context.number, "0")
        self.assertEqual(doc.by_number["1"].label, "INTRODUCTION")
        self.assertIn("INTRODUCTION", self.window.view.toPlainText())
        self.assertIn("(1)", self.window.view.toPlainText())
        self.assertIn("hs:/jump/1", self.window.view.toHtml())
        self.assertIn("tutor-hyperscope.opml#0", self.window.address_text())
        self.assertIn("HyperScope", self.window.windowTitle())

        self.window.go("INTRODUCTION")
        self.assertEqual(self.window.document.context.number, "1")
        self.window.go(".u")
        self.assertEqual(self.window.document.context.number, "0")
        self.window.go("#1A:e")
        self.assertEqual(self.window.document.context.label, "Welcome")
        self.assertEqual(
            self.window.document.viewspecs.level_clip,
            self.window.document.context.level,
        )
        self.window.go("#:n")
        self.assertFalse(self.window.document.viewspecs.show_addressing)
        self.assertNotIn("#7a4ea3", self.window.view.toHtml())

        self.window.back()
        self.assertEqual(self.window.document.context.label, "Welcome")
        self.window.forward()
        self.assertFalse(self.window.document.viewspecs.show_addressing)

        self.window.reset_view()
        self.assertTrue(self.window.document.viewspecs.show_addressing)
        self.assertEqual(self.window.document.viewspecs.to_string(), "")

        self.window.go("#:n")
        self.window.go("#:hjmpuwyACHJLP")
        self.assertTrue(self.window.document.viewspecs.show_addressing)
        self.window.go("#:n")
        self.window.go("./tutor.opml#2")
        self.assertIn("AUGMENT", plain_text(self.window.document.context.text))
        self.assertTrue(self.window.document.viewspecs.show_addressing)

        self.window.copy_address()
        copied = QApplication.clipboard().text()
        self.assertIn("tutor.opml#2", copied)

    def test_links_quick_buttons_commands_and_dialog(self):
        self.window.open_path(str(TUTOR))
        self.window.go("#1A")
        self.window.follow_anchor(QUrl("hs:/quick/out"))
        self.assertEqual(self.window.document.context.number, "1")
        self.assertEqual(self.window.document.viewspecs.line_clip, 1)
        self.assertFalse(self.window.document.viewspecs.show_blank_lines)
        self.assertEqual(
            self.window.document.viewspecs.level_clip,
            self.window.document.context.level + 1,
        )

        self.window.follow_anchor(QUrl("hs:/quick/all"))
        self.assertEqual(self.window.document.viewspecs.level_clip, 63)
        self.assertTrue(self.window.document.viewspecs.show_blank_lines)

        self.window.follow_anchor(QUrl("hs:/jump/2"))
        self.assertEqual(self.window.document.context.number, "2")
        self.assertEqual(self.window.document.context.label, "AUGMENTED")

        self.window.follow_anchor(QUrl("hs:/go/" + "%23.u"))
        self.assertEqual(self.window.document.context.number, "0")

        self.window.go("#10")
        # Statement 10's parent is the origin, not statement 1. The number
        # string itself would chop "10" down to "1".
        self.assertEqual(self.window.document.context.number, "10")
        self.window.follow_anchor(QUrl("hs:/quick/out"))
        self.assertEqual(self.window.document.context.number, "0")

        self.window.set_turbo(True)
        self.assertTrue(self.window.command_label.isVisible())
        self.assertFalse(self.window.jump_button.isVisible())
        self.type_command("ji2")
        self.press(Qt.Key_Return)
        self.press(Qt.Key_Return)
        self.assertEqual(self.window.document.context.number, "2")
        self.assertEqual(self.window.command_label.text(), "BASE C: ")

        self.type_command("jep")
        self.type_command("1A")
        self.press(Qt.Key_Return)
        self.press(Qt.Key_Return)
        self.assertEqual(self.window.document.context.number, "1B")

        self.type_command("jnf")
        self.type_command("ZOOM")
        self.press(Qt.Key_Return)
        self.press(Qt.Key_Return)
        self.assertEqual(self.window.document.context.label, "ZOOM")
        self.assertIn("ZOOM OUT", plain_text(self.window.document.context.text))

        self.window.set_turbo(False)
        self.assertTrue(self.window.jump_button.isVisible())

        before = len(self.window.history)
        self.window.open_path(str(TUTOR))
        self.assertEqual(len(self.window.history), 1)
        self.assertFalse(self.window.back_action.isEnabled())
        self.assertGreater(before, 1)

        dialog = ViewspecDialog(self.window, self.window.document)
        dialog.numbers.setChecked(False)
        dialog._apply()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        letters, content_filter = dialog.result_view()
        self.assertIn("n", letters)
        shown = apply_view(self.window.document, letters, content_filter)
        self.assertFalse(shown.viewspecs.show_addressing)

        dialog = ViewspecDialog(self.window, shown)
        dialog.filter_on.setChecked(True)
        dialog.filter_text.setText("Welcome")
        dialog.letters.setText("n")
        dialog._apply()
        letters, content_filter = dialog.result_view()
        self.assertEqual(content_filter, '"Welcome"')
        self.assertIn("i", letters)
        self.assertIn("n", letters)

        dialog.letters.setText("R")
        dialog._apply()
        self.assertIn("?", dialog.error.text())

    def test_augment_figure_and_broken_entity(self):
        broken = load_opml(DEMOS / "augment-132811.opml")
        blob = " ".join(node.text for node in broken.order)
        self.assertIn("fig14.gif", blob)
        self.assertIn("qufot", blob)

        figures = load_opml(DEMOS / "augment-132082.opml")
        html = render_document(figures)
        self.assertIn("fig01.gif", html)
        self.assertIn("file:", html)

        addresses = resolve(str(DEMOS / "example_addresses.opml") + "#:O")
        blob = " ".join(plain_text(node.text) for node in addresses.order)
        self.assertIn("programmed text", blob)

    def test_launcher_imports(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "hyperscope_main", ROOT / "hyperscope.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main(verbosity=2)
