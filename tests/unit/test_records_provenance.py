"""Committed run records name what actually ran, and only revisions that still exist.

A record's provenance carries the commit the working tree was at, whether it
was dirty, and the digests of the edge files and the scorer. A record that
names a commit outside this history, or one that is not an ancestor of
HEAD, is refused: a run on a commit later rewritten away names nothing.
"""
from __future__ import annotations

import copy

from runner import edge_check


def test_the_v2_template_record_was_run_on_a_clean_tree_at_a_commit_that_exists():
    recs = edge_check.load_records()
    rec = next(r for _, r in recs if r["fixtures"]["fixture_set"] == "v2" and r["edge"]["edge_id"] == "template-reference-verifier")
    assert edge_check.validate_record(rec) == []
    prov = rec["provenance"]
    assert prov["worktree_dirty"] is False
    assert edge_check._git("cat-file", "-e", f"{prov['testbed_commit_at_run']}^{{commit}}") is not None
    m2, _ = edge_check.read_manifest(edge_check.ROOT / rec["fixtures"]["path"])
    # The run happened at or after the verifier the set is sealed against.
    assert edge_check._git("merge-base", "--is-ancestor", m2["sealed_against"]["verifier_commit"],
                           prov["testbed_commit_at_run"]) is not None


def test_a_record_may_only_name_a_revision_that_exists_and_is_an_ancestor_of_head():
    recs = edge_check.load_records()
    rec = copy.deepcopy(next(r for _, r in recs if r["fixtures"]["fixture_set"] == "v2"))
    rec["provenance"]["testbed_commit_at_run"] = "0" * 40
    assert any("not a commit" in p for p in edge_check.validate_record(rec))
