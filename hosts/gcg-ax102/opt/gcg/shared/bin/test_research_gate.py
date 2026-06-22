#!/usr/bin/env python3
"""
test_research_gate.py — Regression tests for the council research gate.

Proves that:
  - A council with no external findings block is BLOCKED.
  - A council with no internal findings block is BLOCKED.
  - A council whose plan has both blocks PASSES.
  - A council whose plan is missing both blocks but has a sidecar research
    artifact with both blocks PASSES.
  - A council with only an external block (no internal) is BLOCKED.

No fleet send, no DB, no subprocess. Pure unit test of check_research_gate().
Run: python3 test_research_gate.py
"""
import sys
import tempfile
from pathlib import Path

# Import the function under test directly
sys.path.insert(0, str(Path(__file__).parent))
from council_convene import check_research_gate


def write_plan(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content)
    return p


def test_no_research_artifact_blocked():
    """Plan file has neither block → BLOCKED."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md",
                          "# Plan\n\n## Phase 1\nDo stuff.\n")
        ok, detail = check_research_gate("test-slug", str(plan))
        assert not ok, f"Expected BLOCK, got PASS: {detail}"
        assert "no research artifact" in detail or "missing" in detail, detail
        print(f"PASS: no research artifact → BLOCKED ({detail})")


def test_external_only_blocked():
    """Plan has External findings but no Internal findings → BLOCKED."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md", (
            "# Plan\n\n"
            "## External findings\n"
            "- Industry standard: do X.\n\n"
            "## Phase 1\nDo stuff.\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan))
        assert not ok, f"Expected BLOCK, got PASS: {detail}"
        assert "Internal findings" in detail, f"Unexpected detail: {detail}"
        print(f"PASS: external-only → BLOCKED ({detail})")


def test_internal_only_blocked():
    """Plan has Internal findings but no External findings → BLOCKED."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md", (
            "# Plan\n\n"
            "## Internal findings\n"
            "- We already have X in the codebase.\n\n"
            "## Phase 1\nDo stuff.\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan))
        assert not ok, f"Expected BLOCK, got PASS: {detail}"
        assert "External findings" in detail, f"Unexpected detail: {detail}"
        print(f"PASS: internal-only → BLOCKED ({detail})")


def test_both_blocks_in_plan_passes():
    """Plan file contains both blocks inline → PASS."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md", (
            "# Plan\n\n"
            "## Internal findings\n"
            "- Existing system does X.\n\n"
            "## External findings\n"
            "- Best practice: use Y pattern.\n\n"
            "## Phase 1\nDo stuff.\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan))
        assert ok, f"Expected PASS, got BLOCK: {detail}"
        print(f"PASS: both blocks in plan → PASS ({detail})")


def test_sidecar_research_artifact_passes():
    """Plan has no blocks, but sidecar *research*.md in same dir has both → PASS."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md",
                          "# Plan\n\n## Phase 1\nDo stuff.\n")
        # Sidecar file with both blocks
        write_plan(Path(d), "test-slug-research.md", (
            "# Research\n\n"
            "## Internal findings\n"
            "- Prior work: X exists.\n\n"
            "## External findings\n"
            "- Literature: use Y.\n\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan))
        assert ok, f"Expected PASS, got BLOCK: {detail}"
        print(f"PASS: sidecar research artifact → PASS ({detail})")


def test_explicit_research_artifact_passes():
    """Explicit --research-artifact path with both blocks → PASS."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md",
                          "# Plan\n\n## Phase 1\nDo stuff.\n")
        artifact = write_plan(Path(d), "my-research.md", (
            "## Internal findings\n- X.\n\n## External findings\n- Y.\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan),
                                         research_artifact=str(artifact))
        assert ok, f"Expected PASS, got BLOCK: {detail}"
        print(f"PASS: explicit artifact → PASS ({detail})")


def test_bold_header_variant_passes():
    """**Internal findings** / **External findings** (bold, no #) → PASS."""
    with tempfile.TemporaryDirectory() as d:
        plan = write_plan(Path(d), "plan.md", (
            "# Plan\n\n"
            "**Internal findings**\n"
            "- We have X.\n\n"
            "**External findings**\n"
            "- Industry does Y.\n\n"
        ))
        ok, detail = check_research_gate("test-slug", str(plan))
        assert ok, f"Expected PASS (bold headers), got BLOCK: {detail}"
        print(f"PASS: bold header variant → PASS ({detail})")


if __name__ == "__main__":
    tests = [
        test_no_research_artifact_blocked,
        test_external_only_blocked,
        test_internal_only_blocked,
        test_both_blocks_in_plan_passes,
        test_sidecar_research_artifact_passes,
        test_explicit_research_artifact_passes,
        test_bold_header_variant_passes,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            failures += 1

    print(f"\n{'='*50}")
    if failures:
        print(f"FAILED: {failures}/{len(tests)} tests")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
