# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Deterministic unit tests: Markdown parsing, block build/decode round-trip,
timestamp helpers, search snippet. No AFFiNE app, no DB, no network."""
import datetime as dt
import unittest

from affine_local import blocks as B
from affine_local import decode
from affine_local import search as search_mod
from affine_local import store
from affine_local import write as write_mod


class TestMarkdownParse(unittest.TestCase):
    def test_block_types(self):
        md = "# H1\n## H2\n- bullet\n1. num\n- [ ] todo\n- [x] done\n> quote\n---\npara"
        specs = B.parse_markdown(md)
        kinds = [(s["flavour"], s.get("type"), s.get("checked")) for s in specs]
        self.assertIn(("affine:paragraph", "h1", None), kinds)
        self.assertIn(("affine:paragraph", "h2", None), kinds)
        self.assertIn(("affine:list", "bulleted", None), kinds)
        self.assertIn(("affine:list", "numbered", None), kinds)
        self.assertIn(("affine:list", "todo", False), kinds)
        self.assertIn(("affine:list", "todo", True), kinds)
        self.assertIn(("affine:paragraph", "quote", None), kinds)
        self.assertTrue(any(s["flavour"] == "affine:divider" for s in specs))
        self.assertIn(("affine:paragraph", "text", None), kinds)

    def test_code_fence(self):
        specs = B.parse_markdown("```python\nprint(1)\nprint(2)\n```")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["flavour"], "affine:code")
        self.assertEqual(specs[0]["language"], "python")
        self.assertEqual(specs[0]["text"], "print(1)\nprint(2)")

    def test_empty(self):
        self.assertEqual(B.parse_markdown(""), [])
        self.assertEqual(B.parse_markdown("\n\n  \n"), [])


class TestStripH1(unittest.TestCase):
    def test_strips_leading_h1(self):
        self.assertEqual(write_mod._strip_leading_h1("# Title\n\nbody"), "\nbody")

    def test_keeps_when_no_h1(self):
        self.assertEqual(write_mod._strip_leading_h1("body\n# later"), "body\n# later")


class TestRoundTrip(unittest.TestCase):
    def test_build_then_decode(self):
        md = "## Section\n- one\n- two\n\nA paragraph.\n```js\nx=1\n```"
        doc = B.build_content_doc("My Title", md)
        title, out = decode.doc_to_markdown(doc)
        self.assertEqual(title, "My Title")
        self.assertIn("## Section", out)
        self.assertIn("- one", out)
        self.assertIn("- two", out)
        self.assertIn("A paragraph.", out)
        self.assertIn("```js", out)
        self.assertIn("x=1", out)

    def test_append_specs(self):
        doc = B.build_content_doc("T", "first")
        from pycrdt import Map
        blocks = doc.get("blocks", type=Map)
        note = B.find_note(blocks)
        self.assertIsNotNone(note)
        with doc.transaction():
            B.append_specs_to_note(blocks, note, B.parse_markdown("appended"))
        _t, out = decode.doc_to_markdown(doc)
        self.assertIn("first", out)
        self.assertIn("appended", out)


class TestTableRender(unittest.TestCase):
    def _build(self, r2c1="v1"):
        from pycrdt import Doc, Map, Array, Text
        doc = Doc()
        blocks = doc.get("blocks", type=Map)
        with doc.transaction():
            blocks["t"] = Map({
                "sys:id": "t", "sys:flavour": "affine:table", "sys:version": 1,
                "sys:children": Array([]),
                "prop:columns.c1.columnId": "c1", "prop:columns.c1.order": "a0",
                "prop:columns.c2.columnId": "c2", "prop:columns.c2.order": "a1",
                "prop:rows.r1.rowId": "r1", "prop:rows.r1.order": "a0",
                "prop:rows.r2.rowId": "r2", "prop:rows.r2.order": "a1",
                "prop:cells.r1:c1.text": Text("Head A"), "prop:cells.r1:c2.text": Text("Head B"),
                "prop:cells.r2:c1.text": Text(r2c1), "prop:cells.r2:c2.text": Text("v2"),
            })
        return doc, blocks["t"]  # keep doc alive in caller

    def test_render_table(self):
        _doc, t = self._build()
        lines = decode._render_table(t).splitlines()
        self.assertEqual(lines[0], "| Head A | Head B |")
        self.assertEqual(lines[1], "| --- | --- |")
        self.assertEqual(lines[2], "| v1 | v2 |")

    def test_pipe_escaped_and_ws_collapsed(self):
        _doc, t = self._build(r2c1="a | b\nc")
        self.assertIn("a \\| b c", decode._render_table(t))


class TestTimestamps(unittest.TestCase):
    def test_parse_fmt_roundtrip(self):
        s = "2026-07-23 16:09:28.773"
        self.assertEqual(store.fmt_ts(store.parse_ts(s)), s)

    def test_parse_seconds(self):
        self.assertEqual(store.parse_ts("2026-07-23 16:09:28").second, 28)

    def test_next_monotonic(self):
        a = store.parse_ts("2026-07-23 16:09:28.773")
        b = store.parse_ts(store.fmt_ts(a + dt.timedelta(seconds=5)))
        self.assertGreater(b, a)


class TestSnippet(unittest.TestCase):
    def test_snippet_centers_on_match(self):
        md = "alpha beta gamma delta needle epsilon zeta"
        snip = search_mod._snippet(md, ["needle"], width=20)
        self.assertIn("needle", snip)


if __name__ == "__main__":
    unittest.main()
