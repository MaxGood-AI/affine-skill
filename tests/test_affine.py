# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Deterministic unit tests: Markdown parsing, block build/decode round-trip,
timestamp helpers, search snippet. No AFFiNE app, no DB, no network."""
import contextlib
import datetime as dt
import io
import os
import unittest
from unittest import mock

from affine_local import blocks as B
from affine_local import cli
from affine_local import config
from affine_local import decode
from affine_local import folders
from affine_local import images
from affine_local import search as search_mod
from affine_local import store
from affine_local import textops as T
from affine_local import write as write_mod
from pycrdt import Map


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


def _table_doc(cell="Taster Avatar"):
    """A doc whose note holds one paragraph and one table (tables only exist as blocks)."""
    from pycrdt import Map, Array, Text
    doc = B.build_content_doc("T", "intro paragraph")
    blocks = doc.get("blocks", type=Map)
    note = B.find_note(blocks)
    with doc.transaction():
        blocks["tbl"] = Map({
            "sys:id": "tbl", "sys:flavour": "affine:table", "sys:version": 1,
            "sys:children": Array([]),
            "prop:columns.c1.columnId": "c1", "prop:columns.c1.order": "a0",
            "prop:rows.r1.rowId": "r1", "prop:rows.r1.order": "a0",
            "prop:rows.r2.rowId": "r2", "prop:rows.r2.order": "a1",
            "prop:cells.r1:c1.text": Text("Tier"),
            "prop:cells.r2:c1.text": Text(cell),
        })
        blocks[note]["sys:children"].append("tbl")
    return doc


def _apply(base, change):
    """The doc as it exists after committing `change` (no DB involved)."""
    from pycrdt import Doc
    out = Doc()
    out.apply_update(base.get_update(b"\x00"))
    for _doc_id, delta in change.deltas:
        out.apply_update(delta)
    return out


class TestTableParse(unittest.TestCase):
    TABLE = "| Tier | Price |\n| --- | --- |\n| Tester | free |\n| Essential | $499 |"

    def test_pipe_table_becomes_one_table_spec(self):
        specs = B.parse_markdown("before\n\n" + self.TABLE + "\n\nafter")
        self.assertEqual([s["flavour"] for s in specs],
                         ["affine:paragraph", "affine:table", "affine:paragraph"])
        self.assertEqual(specs[1]["rows"],
                         [["Tier", "Price"], ["Tester", "free"], ["Essential", "$499"]])

    def test_escaped_pipe_and_ragged_rows(self):
        specs = B.parse_markdown("| A | B |\n|---|---|\n| x \\| y |\n| 1 | 2 | 3 |")
        self.assertEqual(specs[0]["rows"], [["A", "B"], ["x | y", ""], ["1", "2"]])

    def test_pipe_line_without_separator_stays_a_paragraph(self):
        specs = B.parse_markdown("| just | text |\nnext")
        self.assertEqual([s["flavour"] for s in specs], ["affine:paragraph", "affine:paragraph"])

    def test_frac_index_is_ascii_ascending(self):
        keys = [B.frac_index(i) for i in range(70)]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys[0], "a0")
        self.assertEqual(len(set(keys)), 70)


class TestTableRoundTrip(unittest.TestCase):
    def test_build_then_decode_renders_same_table(self):
        md = "intro\n\n| A | B |\n| --- | --- |\n| 1 | x \\| y |\n| 2 |  |\n\nafter"
        _t, out = decode.doc_to_markdown(B.build_content_doc("T", md))
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(lines, ["intro", "| A | B |", "| --- | --- |",
                                 "| 1 | x \\| y |", "| 2 |  |", "after"])

    def test_table_block_uses_flat_affine_props(self):
        doc = B.build_content_doc("T", "| H |\n|---|\n| c |")
        blocks = doc.get("blocks", type=Map)
        tbl = next(blocks[b] for b in blocks.keys() if blocks[b]["sys:flavour"] == "affine:table")
        keys = list(tbl.keys())
        self.assertEqual(sum(k.startswith("prop:columns.") and k.endswith(".columnId") for k in keys), 1)
        self.assertEqual(sum(k.startswith("prop:rows.") and k.endswith(".rowId") for k in keys), 2)
        self.assertEqual(sum(k.startswith("prop:cells.") and k.endswith(".text") for k in keys), 2)

    def test_insert_and_append_validate_table_cells(self):
        doc = B.build_content_doc("T", "head\ntail")
        table = "| K | V |\n|---|---|\n| a | b |"
        change = write_mod.plan_insert(doc, "d1", "head", table)
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["head", "| K | V |", "| --- | --- |", "| a | b |", "tail"])
        change = write_mod.plan_append(doc, "d1", table)
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertTrue(md.rstrip().endswith("| a | b |"))


class TestByteOffsets(unittest.TestCase):
    """yrs offsets are UTF-8 bytes; a codepoint index silently corrupts text."""

    def test_byte_offset_units(self):
        self.assertEqual(T.byte_offset("abc", 2), 2)
        self.assertEqual(T.byte_offset("a—b", 2), 4)      # em dash = 3 bytes
        self.assertEqual(T.byte_offset("aéb", 2), 3)      # é = 2 bytes
        self.assertEqual(T.byte_offset("a\U0001F600b", 2), 5)  # emoji = 4 bytes

    def test_replace_after_multibyte_chars(self):
        doc = B.build_content_doc("T", "Version 1.2 — the Taster Avatar tier")
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("Version 1.2 — the Tester Avatar tier", md)

    def test_replace_after_astral_char(self):
        doc = B.build_content_doc("T", "\U0001F600 keep the Taster name")
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("\U0001F600 keep the Tester name", md)

    def test_multibyte_text_is_not_mangled_elsewhere(self):
        doc = B.build_content_doc("T", "café — naïve — Taster — résumé")
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("café — naïve — Tester — résumé", md)


class TestReplace(unittest.TestCase):
    def test_requires_unique_match_by_default(self):
        doc = B.build_content_doc("T", "the Taster tier\nanother Taster line")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_replace(doc, "d1", "Taster", "Tester")
        self.assertIn("2 occurrences", str(cm.exception))

    def test_all_replaces_every_occurrence(self):
        doc = B.build_content_doc("T", "the Taster tier\nanother Taster line")
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester", replace_all=True)
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertNotIn("Taster", md)
        self.assertEqual(md.count("Tester"), 2)

    def test_multiple_matches_in_one_block(self):
        doc = B.build_content_doc("T", "Taster then Taster then Taster")
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester", replace_all=True)
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("Tester then Tester then Tester", md)

    def test_replaces_inside_table_cell(self):
        doc = _table_doc()
        change = write_mod.plan_replace(doc, "d1", "Taster Avatar", "Tester Avatar")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("| Tester Avatar |", md)
        self.assertIn("| Tier |", md)

    def test_deletion_via_empty_replacement(self):
        doc = B.build_content_doc("T", "keep this DROPME too")
        change = write_mod.plan_replace(doc, "d1", " DROPME", "")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("keep this too", md)

    def test_rejects_missing_text(self):
        doc = B.build_content_doc("T", "body")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_replace(doc, "d1", "absent", "x")

    def test_rejects_identical_replacement(self):
        doc = B.build_content_doc("T", "same")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_replace(doc, "d1", "same", "same")

    def test_cross_block_match_explains_boundary(self):
        doc = B.build_content_doc("T", "first line\nsecond line")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_replace(doc, "d1", "first line\nsecond line", "merged")
        self.assertIn("cannot span block boundaries", str(cm.exception))

    def test_in_block_scopes_replacement(self):
        """A term and the rule quoting it: only the term must change."""
        doc = B.build_content_doc("T", 'Taster Avatar tier\nAlways "Tester". Never "Taster".')
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester",
                                        in_block="tier")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("Tester Avatar tier", md)
        self.assertIn('Always "Tester". Never "Taster".', md)

    def test_in_block_scopes_to_one_table_cell_group(self):
        doc = _table_doc()
        change = write_mod.plan_replace(doc, "d1", "Taster", "Tester", in_block="Tier")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("| Tester Avatar |", md)

    def test_in_block_rejects_missing_text(self):
        doc = B.build_content_doc("T", "alpha\nbravo")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_replace(doc, "d1", "alpha", "x", in_block="bravo")
        self.assertIn("was not found in the block", str(cm.exception))

    def test_leaves_other_blocks_untouched(self):
        doc = B.build_content_doc("T", "alpha target\nbravo\ncharlie")
        change = write_mod.plan_replace(doc, "d1", "target", "hit")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        self.assertIn("alpha hit", md)
        self.assertIn("bravo", md)
        self.assertIn("charlie", md)

    def test_preserves_block_ids(self):
        doc = B.build_content_doc("T", "keep my identity")
        from pycrdt import Map
        before = set(doc.get("blocks", type=Map).keys())
        change = write_mod.plan_replace(doc, "d1", "identity", "id")
        after = set(_apply(doc, change).get("blocks", type=Map).keys())
        self.assertEqual(before, after)


class TestInsert(unittest.TestCase):
    def test_insert_after_anchor(self):
        doc = B.build_content_doc("T", "one\ntwo\nthree")
        change = write_mod.plan_insert(doc, "d1", "two", "## New Section")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["one", "two", "## New Section", "three"])

    def test_insert_before_anchor(self):
        doc = B.build_content_doc("T", "one\ntwo")
        change = write_mod.plan_insert(doc, "d1", "two", "middle", before=True)
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["one", "middle", "two"])

    def test_insert_multiple_blocks_keeps_order(self):
        doc = B.build_content_doc("T", "head\ntail")
        change = write_mod.plan_insert(doc, "d1", "head", "- a\n- b\n- c")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["head", "- a", "- b", "- c", "tail"])

    def test_exact_block_match_wins_over_substring(self):
        """A short anchor names the TOC entry, not the heading or prose quoting it."""
        doc = B.build_content_doc("T", "Trust\n## 19. Trust\nsee the Trust section for detail")
        change = write_mod.plan_insert(doc, "d1", "Trust", "Philosophy")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines[:2], ["Trust", "Philosophy"])

    def test_ambiguous_anchor_rejected(self):
        doc = B.build_content_doc("T", "repeat\nrepeat")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_insert(doc, "d1", "repeat", "x")
        self.assertIn("2 blocks contain", str(cm.exception))

    def test_missing_anchor_rejected(self):
        doc = B.build_content_doc("T", "body")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_insert(doc, "d1", "nope", "x")


class TestDeleteBlock(unittest.TestCase):
    def test_deletes_only_target(self):
        doc = B.build_content_doc("T", "one\ntwo\nthree")
        change = write_mod.plan_delete_block(doc, "d1", "two")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["one", "three"])

    def test_refuses_structural_block(self):
        """Defensive guard: even if an anchor resolved to a note/page, never delete it."""
        from pycrdt import Map, Text
        doc = B.build_content_doc("T", "body")
        blocks = doc.get("blocks", type=Map)
        note = B.find_note(blocks)
        with doc.transaction():
            blocks[note]["prop:text"] = Text("structural bait")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_delete_block(doc, "d1", "structural bait")
        self.assertIn("refusing to delete structural block", str(cm.exception))

    def test_missing_anchor_rejected(self):
        doc = B.build_content_doc("T", "body")
        with self.assertRaises(write_mod.WriteError) as cm:
            write_mod.plan_delete_block(doc, "d1", "not here")
        self.assertIn("no block contains", str(cm.exception))

    def test_removes_nested_children(self):
        from pycrdt import Map
        doc = B.build_content_doc("T", "parent\nsibling")
        blocks = doc.get("blocks", type=Map)
        parent_id = next(f.block_id for f in T.text_fields(blocks) if f.value == "parent")
        with doc.transaction():
            B.insert_specs_at(blocks, parent_id, 0, B.parse_markdown("child text"))
        change = write_mod.plan_delete_block(doc, "d1", "parent")
        out = _apply(doc, change)
        _t, md = decode.doc_to_markdown(out)
        self.assertNotIn("child text", md)
        self.assertIn("sibling", md)


class TestSetTitle(unittest.TestCase):
    def _root(self, doc_id="d1", title="Old"):
        from pycrdt import Doc, Map, Array, Text
        root = Doc()
        meta = root.get("meta", type=Map)
        with root.transaction():
            meta["pages"] = Array([
                Map({"id": doc_id, "title": Text(title), "tags": Array([])}),
                Map({"id": "other", "title": Text("Untouched"), "tags": Array([])}),
            ])
        return root

    def test_updates_page_and_root_meta(self):
        doc = B.build_content_doc("Old", "body")
        root = self._root()
        change = write_mod.plan_set_title(doc, "d1", root, "root", "New")
        self.assertEqual(len(change.deltas), 2)
        doc_delta = dict(change.deltas)["d1"]
        root_delta = dict(change.deltas)["root"]

        from pycrdt import Doc, Map
        v = Doc(); v.apply_update(doc.get_update(b"\x00")); v.apply_update(doc_delta)
        self.assertEqual(decode.title_of(v), "New")

        r = Doc(); r.apply_update(root.get_update(b"\x00")); r.apply_update(root_delta)
        pages = {p["id"]: str(p["title"]) for p in
                 (r.get("meta", type=Map)["pages"][i] for i in range(2))}
        self.assertEqual(pages["d1"], "New")
        self.assertEqual(pages["other"], "Untouched")

    def test_rejects_unregistered_doc(self):
        doc = B.build_content_doc("Old", "body")
        root = self._root(doc_id="somethingelse")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_set_title(doc, "d1", root, "root", "New")

    def test_rejects_empty_and_unchanged(self):
        doc = B.build_content_doc("Old", "body")
        root = self._root()
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_set_title(doc, "d1", root, "root", "   ")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_set_title(doc, "d1", root, "root", "Old")


class TestAppendPlan(unittest.TestCase):
    def test_append_adds_to_end(self):
        doc = B.build_content_doc("T", "first")
        change = write_mod.plan_append(doc, "d1", "second")
        _t, md = decode.doc_to_markdown(_apply(doc, change))
        lines = [l for l in md.splitlines() if l.strip()]
        self.assertEqual(lines, ["first", "second"])

    def test_rejects_empty(self):
        doc = B.build_content_doc("T", "first")
        with self.assertRaises(write_mod.WriteError):
            write_mod.plan_append(doc, "d1", "   ")


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


class TestCliSyncFlag(unittest.TestCase):
    """`--sync` on the write commands pushes right after the write."""

    def _ws(self, kind):
        return config.Workspace("srv", "ws1", "/nonexistent/storage.db", "Team", kind)

    def test_workspaces_without_affine_is_an_error_not_silence(self):
        args = cli.build_parser().parse_args(["workspaces"])
        err = io.StringIO()
        with mock.patch.object(cli.config, "list_workspaces", return_value=[]), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                cli.cmd_workspaces(args)
        self.assertIn("no AFFiNE workspaces found", err.getvalue())

    def test_parser_accepts_sync_on_write_commands(self):
        p = cli.build_parser()
        self.assertTrue(p.parse_args(["new", "--title", "T", "--sync"]).sync)
        self.assertTrue(p.parse_args(["edit", "ID", "--append", "x", "--sync"]).sync)
        self.assertTrue(p.parse_args(["delete", "ID", "--sync"]).sync)
        self.assertFalse(p.parse_args(["delete", "ID"]).sync)

    def test_no_sync_only_reminds(self):
        with mock.patch.object(cli.appctl, "sync") as sync:
            cli._after_write(self._ws("cloud"), sync_now=False)
        sync.assert_not_called()

    def test_local_workspace_has_nothing_to_push(self):
        with mock.patch.object(cli.appctl, "sync") as sync:
            cli._after_write(self._ws("local"), sync_now=True)
        sync.assert_not_called()

    def test_sync_pushes_only_the_written_workspace(self):
        ws = self._ws("cloud")
        out = io.StringIO()
        with mock.patch.object(cli.appctl, "sync", return_value=(True, 6, [])) as sync, \
                mock.patch.object(cli.store, "lock"), contextlib.redirect_stdout(out):
            cli._after_write(ws, sync_now=True)
        sync.assert_called_once_with([ws])
        self.assertIn("synced 1 workspace(s)", out.getvalue())

    def test_sync_failure_is_an_error(self):
        ws = self._ws("cloud")
        with mock.patch.object(cli.appctl, "sync", return_value=(False, 120, [ws])), \
                mock.patch.object(cli.store, "lock"), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli._after_write(ws, sync_now=True)

# 1x1 transparent PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


class TestImages(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        os.makedirs(os.path.join(self.dir, "images"))
        with open(os.path.join(self.dir, "images", "01-shot.png"), "wb") as f:
            f.write(PNG_BYTES)

    def test_blob_id_is_urlsafe_base64_sha256_with_padding(self):
        import base64, hashlib
        key = images.blob_id(PNG_BYTES)
        self.assertEqual(key, base64.urlsafe_b64encode(hashlib.sha256(PNG_BYTES).digest()).decode())
        self.assertTrue(key.endswith("="))
        self.assertEqual(len(key), 44)

    def test_image_line_becomes_image_spec_relative_to_base_dir(self):
        specs = B.parse_markdown("before\n![A screen](images/01-shot.png)\nafter", self.dir)
        self.assertEqual([s["flavour"] for s in specs],
                         ["affine:paragraph", "affine:image", "affine:paragraph"])
        img = specs[1]
        self.assertEqual(img["caption"], "A screen")
        self.assertEqual(img["mime"], "image/png")
        self.assertEqual(img["source_id"], images.blob_id(PNG_BYTES))
        self.assertEqual(img["data"], PNG_BYTES)

    def test_absolute_path_and_angle_brackets_and_title(self):
        path = os.path.join(self.dir, "images", "01-shot.png")
        specs = B.parse_markdown(f'![x](<{path}> "a title")', None)
        self.assertEqual(specs[0]["flavour"], "affine:image")

    def test_missing_file_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            B.parse_markdown("![x](images/nope.png)", self.dir)
        self.assertIn("not found", str(cm.exception))

    def test_unsupported_type_is_refused(self):
        with open(os.path.join(self.dir, "a.svg"), "w") as f:
            f.write("<svg/>")
        with self.assertRaises(ValueError) as cm:
            B.parse_markdown("![x](a.svg)", self.dir)
        self.assertIn("unsupported type", str(cm.exception))

    def test_content_must_match_extension(self):
        with open(os.path.join(self.dir, "fake.png"), "wb") as f:
            f.write(b"not a png")
        with self.assertRaises(ValueError) as cm:
            B.parse_markdown("![x](fake.png)", self.dir)
        self.assertIn("not a valid image/png", str(cm.exception))

    def test_all_allowed_extensions_map(self):
        self.assertEqual(sorted(images.MIME_BY_EXT), [".gif", ".jpeg", ".jpg", ".png", ".webp"])

    def test_image_block_structure(self):
        doc = B.build_content_doc("T", "![A screen](images/01-shot.png)".replace("images", self.dir + "/images"))
        blocks = doc.get("blocks", type=Map)
        imgs = [blocks[b] for b in blocks.keys() if blocks[b]["sys:flavour"] == "affine:image"]
        self.assertEqual(len(imgs), 1)
        img = imgs[0]
        self.assertEqual(img["prop:sourceId"], images.blob_id(PNG_BYTES))
        self.assertEqual(img["prop:caption"], "A screen")
        self.assertEqual(img["prop:size"], len(PNG_BYTES))
        self.assertEqual(img["prop:xywh"], "[0,0,0,0]")
        note = B.find_note(blocks)
        self.assertIn(img["sys:id"], B.children_of(blocks, note))

    def test_decode_round_trips_caption_and_source(self):
        doc = B.build_content_doc("T", f"![Cap]({self.dir}/images/01-shot.png)")
        _t, md = decode.doc_to_markdown(doc)
        self.assertIn(f"![Cap](blob:{images.blob_id(PNG_BYTES)})", md)

    def test_blobs_of_dedups_same_file(self):
        specs = B.parse_markdown("![a](images/01-shot.png)\n![b](images/01-shot.png)", self.dir)
        blobs = B.blobs_of(specs)
        self.assertEqual(blobs, [(images.blob_id(PNG_BYTES), PNG_BYTES, "image/png")])

    def test_append_plan_carries_blob_and_validates(self):
        base = B.build_content_doc("T", "first")
        change = write_mod.plan_append(base, "d1", "![s](images/01-shot.png)", base_dir=self.dir)
        self.assertEqual(change.blobs[0][0], images.blob_id(PNG_BYTES))
        _t, md = decode.doc_to_markdown(_apply(base, change))
        self.assertIn("blob:" + images.blob_id(PNG_BYTES), md)

    def test_insert_plan_carries_blob(self):
        base = B.build_content_doc("T", "anchor line")
        change = write_mod.plan_insert(base, "d1", "anchor line", "![s](images/01-shot.png)",
                                       base_dir=self.dir)
        self.assertEqual(len(change.blobs), 1)

    def test_missing_image_in_plan_is_refused_before_any_delta(self):
        base = B.build_content_doc("T", "first")
        with self.assertRaises(ValueError):
            write_mod.plan_append(base, "d1", "![s](nope.png)", base_dir=self.dir)


def _store_db(path):
    """An empty AFFiNE-shaped storage.db (same tables and columns the app creates)."""
    import sqlite3
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE snapshots (doc_id VARCHAR PRIMARY KEY NOT NULL, data BLOB NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL);
        CREATE TABLE updates (doc_id VARCHAR NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
          data BLOB NOT NULL, PRIMARY KEY (doc_id, created_at));
        CREATE TABLE clocks (doc_id VARCHAR PRIMARY KEY NOT NULL, timestamp TIMESTAMP NOT NULL);
        CREATE TABLE blobs (key VARCHAR PRIMARY KEY NOT NULL, data BLOB NOT NULL, mime VARCHAR NOT NULL,
          size INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, deleted_at TIMESTAMP);
        CREATE TABLE peer_clocks (peer VARCHAR NOT NULL, doc_id VARCHAR NOT NULL,
          remote_clock TIMESTAMP NOT NULL DEFAULT 0, pulled_remote_clock TIMESTAMP NOT NULL DEFAULT 0,
          pushed_clock TIMESTAMP NOT NULL DEFAULT 0, PRIMARY KEY (peer, doc_id));
        CREATE TABLE peer_blob_sync (peer VARCHAR NOT NULL, blob_id VARCHAR NOT NULL, uploaded_at TIMESTAMP,
          PRIMARY KEY (peer, blob_id));
    """)
    con.commit()
    return con


def _folders_doc():
    """A db$folders doc: folders Support and Ops (with a nested Ops / Support), one doc in Support,
    one deleted record."""
    from pycrdt import Doc
    doc = Doc()
    recs = {
        "f1": {"id": "f1", "type": "folder", "parentId": None, "data": "Customer Support", "index": "Zs0abc"},
        "f2": {"id": "f2", "type": "folder", "parentId": None, "data": "Ops", "index": "Zt0abc"},
        "f3": {"id": "f3", "type": "folder", "parentId": "f2", "data": "Support", "index": "a0"},
        "e1": {"id": "e1", "type": "doc", "parentId": "f1", "data": "docA", "index": "a00Fman"},
    }
    with doc.transaction():
        for rid, rec in recs.items():
            m = doc.get(rid, type=Map)
            for k, v in rec.items():
                m[k] = v
        dead = doc.get("gone", type=Map)
        dead["id"] = "gone"
        dead["$$DELETED"] = True
    return doc


class TestFolders(unittest.TestCase):
    def test_folder_list_has_paths_and_counts_and_skips_deleted(self):
        listing = folders.folder_list(_folders_doc())
        self.assertEqual([f["path"] for f in listing],
                         ["Customer Support", "Ops", "Ops / Support"])
        self.assertEqual({f["name"]: f["docs"] for f in listing}["Customer Support"], 1)

    def test_find_folder_is_case_insensitive(self):
        self.assertEqual(folders.find_folder(_folders_doc(), "  customer SUPPORT "), "f1")

    def test_find_folder_ambiguous_name_lists_paths(self):
        from pycrdt import Map as M
        doc = _folders_doc()
        with doc.transaction():
            m = doc.get("f4", type=M)
            for k, v in {"id": "f4", "type": "folder", "parentId": None, "data": "support", "index": "Zu0"}.items():
                m[k] = v
        with self.assertRaises(folders.FolderError) as cm:
            folders.find_folder(doc, "Support")
        self.assertIn("2 folders", str(cm.exception))
        self.assertIn("Ops / Support", str(cm.exception))

    def test_find_folder_by_full_path_when_name_is_ambiguous(self):
        self.assertEqual(folders.find_folder(_folders_doc(), "ops / support"), "f3")

    def test_missing_folder_lists_names_and_never_creates(self):
        doc = _folders_doc()
        before = set(doc.keys())
        with self.assertRaises(folders.FolderError) as cm:
            folders.plan_place(doc, "docB", "Nope")
        self.assertIn("Customer Support", str(cm.exception))
        self.assertEqual(set(doc.keys()), before)

    def test_workspace_without_folders_says_so(self):
        from pycrdt import Doc
        with self.assertRaises(folders.FolderError) as cm:
            folders.find_folder(Doc(), "X")
        self.assertIn("no folders", str(cm.exception))

    def test_index_after(self):
        self.assertEqual(folders.index_after(None), "a0")
        self.assertEqual(folders.index_after("Zs0abc"), "Zt")
        self.assertEqual(folders.index_after("a00Fman"), "a1")
        self.assertEqual(folders.index_after("Zz0x"), "a0")
        self.assertGreater(folders.index_after("a0z"), "a0z")
        self.assertGreater(folders.index_after("a00Fman"), "a00Fman")

    def test_place_adds_doc_entry_after_existing_siblings(self):
        doc = _folders_doc()
        delta, summary = folders.plan_place(doc, "docB", "customer support")
        self.assertIn("place docB in folder 'Customer Support'", summary)
        from pycrdt import Doc
        after = Doc()
        after.apply_update(doc.get_update(b"\x00"))
        after.apply_update(delta)
        mine = [r for r in folders.records(after) if r["data"] == "docB"]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["type"], mine[0]["parentId"]), ("doc", "f1"))
        self.assertGreater(mine[0]["index"], "a00Fman")
        self.assertEqual(len(mine[0]["id"]), 21)

    def test_place_moves_existing_entry(self):
        doc = _folders_doc()
        delta, summary = folders.plan_place(doc, "docA", "Ops")
        self.assertTrue(summary.startswith("move docA"))
        from pycrdt import Doc
        after = Doc()
        after.apply_update(doc.get_update(b"\x00"))
        after.apply_update(delta)
        entries = [r for r in folders.records(after) if r["data"] == "docA"]
        self.assertEqual([(e["id"], e["parentId"]) for e in entries], [("e1", "f2")])

    def test_place_in_current_folder_is_refused(self):
        with self.assertRaises(folders.FolderError):
            folders.plan_place(_folders_doc(), "docA", "Customer Support")


class TestStoreWrites(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "storage.db")
        con = _store_db(self.db)
        from pycrdt import Doc, Array, Text
        root = Doc()
        meta = root.get("meta", type=Map)
        with root.transaction():
            meta["pages"] = Array([])
        con.execute("INSERT INTO snapshots (doc_id, data, updated_at) VALUES (?,?,?)",
                    ("ws1", root.get_update(b"\x00"), "2026-01-01 00:00:00.000"))
        con.execute("INSERT INTO clocks VALUES ('ws1', '2026-01-01 00:00:00.000')")
        fdoc = _folders_doc()
        con.execute("INSERT INTO snapshots (doc_id, data, updated_at) VALUES (?,?,?)",
                    ("db$folders", fdoc.get_update(b"\x00"), "2026-01-01 00:00:00.000"))
        con.commit()
        con.close()
        with open(os.path.join(self._tmp.name, "s.png"), "wb") as f:
            f.write(PNG_BYTES)

    def _con(self):
        import sqlite3
        return sqlite3.connect(self.db)

    def test_new_doc_with_image_and_folder(self):
        doc_id = write_mod.new_doc(self.db, "ws1", "srv", "ws1", "Title",
                                   "# Title\ntext\n![Shot](s.png)", base_dir=self._tmp.name,
                                   folder="Customer Support")
        con = self._con()
        key, mime, size, data = con.execute("SELECT key, mime, size, data FROM blobs").fetchone()
        self.assertEqual((key, mime, size, bytes(data)), (images.blob_id(PNG_BYTES), "image/png",
                                                          len(PNG_BYTES), PNG_BYTES))
        self.assertEqual(con.execute("SELECT count(*) FROM peer_blob_sync").fetchone()[0], 0)
        doc = store.load_doc(con, doc_id)
        _t, md = decode.doc_to_markdown(doc)
        self.assertIn(f"![Shot](blob:{key})", md)
        entries = [r for r in folders.records(folders.load(con)) if r["data"] == doc_id]
        self.assertEqual([(e["type"], e["parentId"]) for e in entries], [("doc", "f1")])
        self.assertEqual(con.execute("SELECT count(*) FROM clocks WHERE doc_id='db$folders'").fetchone()[0], 1)

    def test_bad_folder_writes_nothing(self):
        with self.assertRaises(folders.FolderError):
            write_mod.new_doc(self.db, "ws1", "srv", "ws1", "T", "x", folder="Nope")
        con = self._con()
        self.assertEqual(con.execute("SELECT count(*) FROM updates").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT count(*) FROM blobs").fetchone()[0], 0)

    def test_bad_image_writes_nothing(self):
        with self.assertRaises(ValueError):
            write_mod.new_doc(self.db, "ws1", "srv", "ws1", "T", "![x](missing.png)",
                              base_dir=self._tmp.name)
        self.assertEqual(self._con().execute("SELECT count(*) FROM updates").fetchone()[0], 0)

    def test_commit_stores_blob_with_append(self):
        doc_id = write_mod.new_doc(self.db, "ws1", "srv", "ws1", "T", "first")
        con = self._con()
        base = store.load_doc(con, doc_id)
        change = write_mod.plan_append(base, doc_id, "![s](s.png)", base_dir=self._tmp.name)
        write_mod.commit(self.db, change)
        con = self._con()
        self.assertEqual(con.execute("SELECT count(*) FROM blobs").fetchone()[0], 1)
        _t, md = decode.doc_to_markdown(store.load_doc(con, doc_id))
        self.assertIn("blob:" + images.blob_id(PNG_BYTES), md)

    def test_move_to_folder_commit(self):
        doc_id = write_mod.new_doc(self.db, "ws1", "srv", "ws1", "T", "first")
        con = self._con()
        change = write_mod.plan_move_to_folder(folders.load(con), doc_id, "Ops")
        write_mod.commit(self.db, change)
        con = self._con()
        entries = [r for r in folders.records(folders.load(con)) if r["data"] == doc_id]
        self.assertEqual([e["parentId"] for e in entries], ["f2"])

    def test_unpushed_blob_count_follows_uploaded_marker(self):
        write_mod.new_doc(self.db, "ws1", "srv", "ws1", "T", "![s](s.png)", base_dir=self._tmp.name)
        con = self._con()
        self.assertEqual(store.unpushed_blob_count(con, "srv"), 1)
        con.execute("INSERT INTO peer_blob_sync VALUES (?,?,?)",
                    ("cloud:srv", images.blob_id(PNG_BYTES), "2026-11-16 00:00:00"))
        con.commit()
        self.assertEqual(store.unpushed_blob_count(con, "srv"), 0)
        self.assertEqual(store.unpushed_blob_count(con, "other"), 1)


class TestCliNewFolderFlags(unittest.TestCase):
    def test_parser_accepts_folder_flags_and_folders_command(self):
        p = cli.build_parser()
        self.assertEqual(p.parse_args(["new", "--title", "T", "--folder", "F"]).folder, "F")
        self.assertEqual(p.parse_args(["edit", "id", "--move-to-folder", "F"]).move_to_folder, "F")
        self.assertIs(p.parse_args(["folders", "--json"]).fn, cli.cmd_folders)

    def test_move_and_other_edit_ops_are_exclusive(self):
        p = cli.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            p.parse_args(["edit", "id", "--move-to-folder", "F", "--append", "x"])

    def test_base_dir_is_the_markdown_files_folder(self):
        self.assertEqual(cli._base_dir("/a/b/body.md"), "/a/b")
        self.assertEqual(cli._base_dir(None), os.getcwd())


if __name__ == "__main__":
    unittest.main()
