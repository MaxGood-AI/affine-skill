# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Deterministic unit tests: Markdown parsing, block build/decode round-trip,
timestamp helpers, search snippet. No AFFiNE app, no DB, no network."""
import contextlib
import datetime as dt
import io
import unittest
from unittest import mock

from affine_local import blocks as B
from affine_local import cli
from affine_local import config
from affine_local import decode
from affine_local import search as search_mod
from affine_local import store
from affine_local import textops as T
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


if __name__ == "__main__":
    unittest.main()
