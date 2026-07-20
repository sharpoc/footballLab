"""Parametric feature tests for _settlement_unit deduplication.

Freezes both implementations' outputs BEFORE refactoring,
then verifies the unified implementation matches exactly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any



# ---------------------------------------------------------------------------
# Load the two implementations independently (pre-refactor snapshot)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from worldcup.decision_settlement import settlement_unit, _settlement_unit as _settle_ds  # noqa: E402
from worldcup.match_decision import _settlement_unit as _settle_md  # noqa: E402


# ---------------------------------------------------------------------------
# Exhaustive test matrix
# ---------------------------------------------------------------------------

# (score_margin, line, expected_unit)
# Covers: integer lines, half-ball, quarter-ball (positive/negative),
# full-win, full-loss, push, half-win, half-loss, and boundary floats.
TEST_CASES: list[tuple[float, float, float]] = [
    # --- Integer line (0, -1, +1, -2) ---
    # Home wins by 2, line=0 → margin+line=2 → full win
    (2.0, 0.0, 1.0),
    # Draw, line=0 → margin+line=0 → push
    (0.0, 0.0, 0.0),
    # Home loses by 1, line=0 → margin+line=-1 → full loss
    (-1.0, 0.0, -1.0),
    # Home wins by 1, line=-1 → margin+line=0 → push
    (1.0, -1.0, 0.0),
    # Home wins by 2, line=-1 → margin+line=1 → full win
    (2.0, -1.0, 1.0),
    # Home loses by 1, line=+1 → margin+line=0 → push
    (-1.0, 1.0, 0.0),
    # Home loses by 2, line=+1 → margin+line=-1 → full loss
    (-2.0, 1.0, -1.0),
    # Home wins by 1, line=-2 → margin+line=-1 → full loss
    (1.0, -2.0, -1.0),
    # Home wins by 3, line=-2 → margin+line=1 → full win
    (3.0, -2.0, 1.0),

    # --- Half-ball line (-0.5, +0.5, -1.5) ---
    # Home wins by 1, line=-0.5 → margin+line=0.5 → full win
    (1.0, -0.5, 1.0),
    # Draw, line=-0.5 → margin+line=-0.5 → full loss
    (0.0, -0.5, -1.0),
    # Home loses by 1, line=+0.5 → margin+line=-0.5 → full loss
    (-1.0, 0.5, -1.0),
    # Draw, line=+0.5 → margin+line=0.5 → full win
    (0.0, 0.5, 1.0),
    # Home wins by 1, line=-1.5 → margin+line=-0.5 → full loss
    (1.0, -1.5, -1.0),
    # Home wins by 2, line=-1.5 → margin+line=0.5 → full win
    (2.0, -1.5, 1.0),

    # --- Quarter-ball line (-0.25, +0.25, -0.75, +0.75, -1.25, -1.75) ---
    # line=-0.25 splits into (-0.5, 0): margin=0 → 0.5*(-1.0) + 0.5*(0.0) = -0.5
    (0.0, -0.25, -0.5),
    # line=-0.25: margin=1 → 0.5*(1.0) + 0.5*(1.0) = 1.0
    (1.0, -0.25, 1.0),
    # line=+0.25 splits into (0, +0.5): margin=0 → 0.5*(0.0) + 0.5*(1.0) = 0.5
    (0.0, 0.25, 0.5),
    # line=+0.25: margin=-1 → 0.5*(-1.0) + 0.5*(-1.0) = -1.0
    (-1.0, 0.25, -1.0),
    # line=-0.75 splits into (-1, -0.5): margin=1 → 0.5*(0.0) + 0.5*(1.0) = 0.5
    (1.0, -0.75, 0.5),
    # line=-0.75: margin=0 → 0.5*(-1.0) + 0.5*(-1.0) = -1.0
    (0.0, -0.75, -1.0),
    # line=+0.75 splits into (+0.5, +1.0): margin=-1 → 0.5*(-1.0) + 0.5*(0.0) = -0.5
    (-1.0, 0.75, -0.5),
    # line=+0.75: margin=0 → 0.5*(1.0) + 0.5*(1.0) = 1.0
    (0.0, 0.75, 1.0),
    # line=-1.25 splits into (-1.5, -1.0): margin=1 → 0.5*(-1.0) + 0.5*(0.0) = -0.5
    (1.0, -1.25, -0.5),
    # line=-1.25: margin=2 → 0.5*(1.0) + 0.5*(1.0) = 1.0
    (2.0, -1.25, 1.0),
    # line=-1.75 splits into (-2.0, -1.5): margin=2 → 0.5*(0.0) + 0.5*(1.0) = 0.5
    (2.0, -1.75, 0.5),
    # line=-1.75: margin=1 → 0.5*(-1.0) + 0.5*(-1.0) = -1.0
    (1.0, -1.75, -1.0),

    # --- Boundary floats (exact representation) ---
    # margin exactly on the adjusted boundary
    (0.5, -0.5, 0.0),  # margin+line=0.0 → push
    (1.5, -1.5, 0.0),  # push
    (-0.5, 0.5, 0.0),  # push (negative margin)
]

# Non-quarter lines that should raise ValueError
INVALID_LINES: list[float] = [0.1, 0.33, 0.125, 1.1, -0.3]


def test_both_implementations_produce_identical_results():
    """Before refactoring: verify both copies return the same value."""
    for margin, line, expected in TEST_CASES:
        ds_result = _settle_ds(margin, line)
        md_result = _settle_md(margin, line)
        assert ds_result == md_result, (
            f"Mismatch at margin={margin}, line={line}: "
            f"decision_settlement={ds_result}, match_decision={md_result}"
        )
        assert abs(ds_result - expected) < 1e-12, (
            f"Unexpected at margin={margin}, line={line}: "
            f"got {ds_result}, expected {expected}"
        )


def test_decision_settlement_unit_matches_expected():
    """Freeze decision_settlement._settlement_unit expected outputs."""
    for margin, line, expected in TEST_CASES:
        result = _settle_ds(margin, line)
        assert abs(result - expected) < 1e-12, (
            f"margin={margin}, line={line}: got {result}, expected {expected}"
        )


def test_match_decision_unit_matches_expected():
    """Freeze match_decision._settlement_unit expected outputs."""
    for margin, line, expected in TEST_CASES:
        result = _settle_md(margin, line)
        assert abs(result - expected) < 1e-12, (
            f"margin={margin}, line={line}: got {result}, expected {expected}"
        )


def test_invalid_lines_raise_valueerror_decision_settlement():
    for line in INVALID_LINES:
        raised = False
        try:
            _settle_ds(1.0, line)
        except ValueError:
            raised = True
        assert raised, f"Expected ValueError for line={line}"


def test_invalid_lines_raise_valueerror_match_decision():
    for line in INVALID_LINES:
        raised = False
        try:
            _settle_md(1.0, line)
        except ValueError:
            raised = True
        assert raised, f"Expected ValueError for line={line}"


def test_public_settlement_unit_matches_matrix():
    """After refactoring: public API produces same results as frozen matrix."""
    for margin, line, expected in TEST_CASES:
        result = settlement_unit(margin, line)
        assert abs(result - expected) < 1e-12, (
            f"margin={margin}, line={line}: got {result}, expected {expected}"
        )


def test_both_modules_share_same_function_object():
    """After refactoring: match_decision uses the canonical implementation."""
    assert _settle_ds is _settle_md


# ---------------------------------------------------------------------------
# Manual runner (no pytest dependency required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import inspect

    failures = 0
    count = 0
    for name, fn in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        count += 1
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"\n{count - failures}/{count} passed")
    raise SystemExit(1 if failures else 0)
