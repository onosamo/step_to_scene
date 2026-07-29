"""Tests for the TUI fuzzy search: pure helpers plus an app-level smoke test."""

import asyncio
from pathlib import Path

from textual.widgets import Input, Tree

from step_to_scene.parser import StepAssembly
from step_to_scene.tui import StepExplorerApp, compute_visible_ids, fuzzy_match


class TestFuzzyMatch:
    def test_empty_query_matches_everything(self):
        assert fuzzy_match("", "anything")

    def test_exact_match(self):
        assert fuzzy_match("iep-013122", "iep-013122")

    def test_subsequence_match(self):
        assert fuzzy_match("i13122", "iep-013122")
        assert fuzzy_match("lidcart", "lid cart with sensor")

    def test_no_match(self):
        assert not fuzzy_match("xyz", "iep-013122")

    def test_order_matters(self):
        assert not fuzzy_match("321", "123")

    def test_query_longer_than_text(self):
        assert not fuzzy_match("abcdef", "abc")


def _tree():
    """root -> (a -> a1), (b -> b1)"""
    root = StepAssembly("root", "#1")
    a = StepAssembly("motor mount", "#1/#2", description="left bracket")
    a1 = StepAssembly("bolt", "#1/#2/#3")
    b = StepAssembly("sensor", "#1/#4")
    b1 = StepAssembly("cable", "#1/#4/#5", description="power cable")
    root.add_child(a)
    a.add_child(a1)
    root.add_child(b)
    b.add_child(b1)
    return root, a, a1, b, b1


class TestComputeVisibleIds:
    def test_match_by_name_includes_ancestors(self):
        root, a, a1, b, b1 = _tree()
        visible = compute_visible_ids([root], "bolt", {})
        assert visible == {root.id, a.id, a1.id}

    def test_match_by_description(self):
        root, a, a1, b, b1 = _tree()
        visible = compute_visible_ids([root], "power", {})
        assert visible == {root.id, b.id, b1.id}

    def test_match_by_id(self):
        root, a, a1, b, b1 = _tree()
        visible = compute_visible_ids([root], "#4/#5", {})
        assert b1.id in visible
        assert a.id not in visible

    def test_case_insensitive(self):
        root, a, a1, b, b1 = _tree()
        visible = compute_visible_ids([root], "MOTOR", {})
        assert a.id in visible

    def test_no_match_is_empty(self):
        root, *_ = _tree()
        assert compute_visible_ids([root], "doesnotexist", {}) == set()

    def test_matching_parent_does_not_pull_in_children(self):
        root, a, a1, b, b1 = _tree()
        visible = compute_visible_ids([root], "motor", {})
        assert a.id in visible
        assert a1.id not in visible

    def test_texts_cache_is_reused(self):
        root, *_ = _tree()
        cache: dict[str, tuple[str, ...]] = {}
        compute_visible_ids([root], "motor", cache)
        assert len(cache) == 5
        first = dict(cache)
        compute_visible_ids([root], "sensor", cache)
        assert cache == first


class TestLabelMarkupEscaping:
    def test_brackets_in_cad_names_survive_and_do_not_crash(
        self, assembly_step_file: Path
    ):
        from rich.text import Text

        app = StepExplorerApp(assembly_step_file)
        assembly = StepAssembly(
            "widget [rev2]", "#1", description="note [/misc] -- draft"
        )

        label = app._format_assembly_label(assembly)
        rendered = Text.from_markup(label).plain  # must not raise MarkupError
        assert "widget [rev2]" in rendered
        assert "[/misc]" in rendered

    def test_selected_and_excluded_markup_still_renders(self, assembly_step_file: Path):
        from rich.text import Text

        app = StepExplorerApp(assembly_step_file)
        assembly = StepAssembly("part [a]", "#1")

        app.selected_assemblies = {"#1"}
        selected = Text.from_markup(app._format_assembly_label(assembly)).plain
        assert selected.startswith("[+] ")
        assert "part [a]" in selected

        app.selected_assemblies = set()
        app.excluded_assemblies = {"#1"}
        excluded = Text.from_markup(app._format_assembly_label(assembly)).plain
        assert "part [a]" in excluded


class TestTreeLazyLoadingAndSearch:
    def test_lazy_expand_and_debounced_search(self, assembly_step_file: Path):
        async def scenario():
            app = StepExplorerApp(assembly_step_file)
            async with app.run_test() as pilot:
                await pilot.pause()
                tree = app.query_one("#assembly_tree", Tree)

                # lazy initial build: only the root assembly node exists
                assert len(tree.root.children) == 1
                cell = tree.root.children[0]
                assert len(cell.children) == 0

                # children materialize on expansion
                cell.expand()
                await pilot.pause()
                assert len(cell.children) == 6

                # search narrows the tree after the debounce interval
                search = app.query_one("#search_input", Input)
                search.focus()
                await pilot.press(*"widget")
                await pilot.pause(0.5)

                assert len(tree.root.children) == 1
                cell = tree.root.children[0]
                widget_labels = [str(node.label) for node in cell.children]
                assert len(widget_labels) == 3
                assert all("widget" in label for label in widget_labels)
                # few matches: results come pre-expanded
                assert cell.is_expanded

                # clearing the search restores the lazy full tree
                search.value = ""
                await pilot.pause(0.5)
                cell = tree.root.children[0]
                assert len(cell.children) == 0
                cell.expand()
                await pilot.pause()
                assert len(cell.children) == 6

        asyncio.run(scenario())
