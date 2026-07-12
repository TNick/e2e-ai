"""Tests for runtime model catalog discovery and resolution."""

from __future__ import annotations

from e2e_ai.agents.model_catalog import resolve_model_candidate


class TestResolveModelCandidate:
    def test_matches_exact_catalog_entry(self) -> None:
        resolved = resolve_model_candidate(
            ("gpt-5.6-sol",),
            ("gpt-5.6-terra", "gpt-5.6-sol"),
        )
        assert resolved == "gpt-5.6-sol"

    def test_falls_back_to_first_candidate_without_catalog(self) -> None:
        resolved = resolve_model_candidate(("composer-2.5",), ())
        assert resolved == "composer-2.5"

    def test_returns_none_when_no_candidate_matches(self) -> None:
        resolved = resolve_model_candidate(
            ("missing-model",),
            ("gpt-5.6-sol",),
        )
        assert resolved is None
