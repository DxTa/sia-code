"""Tests for MCP payload size-cap helpers."""

from __future__ import annotations

from sia_code.mcp import _cap_list, _truncate_to_bytes


def test_cap_list_noop_under_limit() -> None:
    payload = {"matches": [1, 2, 3]}
    out, dropped = _cap_list(payload, "matches", 5)
    assert dropped == 0
    assert out["matches"] == [1, 2, 3]
    assert "truncated" not in out


def test_cap_list_truncates_and_marks() -> None:
    payload = {"matches": [1, 2, 3, 4, 5]}
    out, dropped = _cap_list(payload, "matches", 2)
    assert dropped == 3
    assert out["matches"] == [1, 2]
    assert out["truncated"] is True
    assert out["dropped"] == 3


def test_cap_list_missing_key_noop() -> None:
    payload = {"other": []}
    out, dropped = _cap_list(payload, "matches", 5)
    assert dropped == 0
    assert "truncated" not in out


def test_truncate_to_bytes_shrinks_matches() -> None:
    # Budget smaller than the serialized payload forces shrinking.
    payload = {"matches": [{"x": i} for i in range(50)]}
    out = _truncate_to_bytes(payload, 100)
    assert out.get("truncated") is True
    assert len(out["matches"]) < 50
    # Best-effort: never goes below 1 match.
    assert len(out["matches"]) >= 1


def test_truncate_to_bytes_noop_when_small() -> None:
    payload = {"matches": [1]}
    out = _truncate_to_bytes(payload, 4096)
    assert "truncated" not in out
    assert out["matches"] == [1]