import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import worldcup.league_result_evidence as evidence_module
from worldcup.league_result_evidence import build_result_contract_evidence, verify_result_contract_evidence


FOTMOB_SAMPLE_SHA256 = "a861a1aa1c83b7193ea68a6705abc44647fb49194ee80a557356b55fe5bf1e00"


def test_result_contract_evidence_binds_competition_sport_key_schema_and_scope():
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )

    assert verify_result_contract_evidence(evidence, "epl_2026_27") is True
    assert verify_result_contract_evidence(evidence, "laliga_2026_27") is False
    assert evidence["fingerprint"]


def test_result_contract_evidence_rejects_unproven_or_wrong_scope():
    unproven = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="completed_score_unspecified",
        source_reference="saved-sample-sha256",
    )
    assert unproven["verified"] is False
    assert verify_result_contract_evidence({**unproven, "verified": True}, "epl_2026_27") is False


def test_result_contract_evidence_accepts_verified_fotmob_90_minute_schema():
    """Restricting evidence to scores API would block the quota-free FotMob result source."""
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
    )

    assert evidence["verified"] is True
    assert verify_result_contract_evidence(evidence, "epl_2026_27") is True
    assert verify_result_contract_evidence(
        {**evidence, "provider_schema": "fotmob_league_results_v2"},
        "epl_2026_27",
    ) is False


def test_fotmob_evidence_requires_its_provider_schema_and_saved_sample_sha256():
    """Accepting a legacy provider or a label instead of sample content would bypass FotMob semantics proof."""
    fotmob = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
    )
    legacy = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )
    short_reference = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="saved-fotmob-sample",
        provider="fotmob",
    )
    wrong_provider = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="theoddsapi",
    )

    assert verify_result_contract_evidence(
        fotmob, "epl_2026_27", provider_schema="fotmob_league_results_v1"
    ) is True
    assert verify_result_contract_evidence(
        legacy, "epl_2026_27", provider_schema="fotmob_league_results_v1"
    ) is False
    assert short_reference["verified"] is False
    assert wrong_provider["verified"] is False
    assert verify_result_contract_evidence(
        {**short_reference, "verified": True},
        "epl_2026_27",
        provider_schema="fotmob_league_results_v1",
    ) is False
    assert verify_result_contract_evidence(
        {**wrong_provider, "verified": True},
        "epl_2026_27",
        provider_schema="fotmob_league_results_v1",
    ) is False


def test_fotmob_production_evidence_fingerprint_binds_sanitized_probe_sample_path():
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
        sample_path="data/probe/leagues/results/epl-finished.json",
    )
    cache_path = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
        sample_path="data/cache/leagues/results/epl-finished.json",
    )

    assert evidence["verified"] is True
    assert evidence["sample_path"] == "data/probe/leagues/results/epl-finished.json"
    assert verify_result_contract_evidence(evidence, "epl_2026_27") is True
    assert verify_result_contract_evidence(
        {**evidence, "sample_path": "data/probe/leagues/results/other.json"},
        "epl_2026_27",
    ) is False
    assert cache_path["verified"] is False


def _sample(root: Path, content: bytes = b"saved-fotmob-result-sample") -> tuple[str, Path]:
    relative = "data/probe/leagues/results/epl/sample.json"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return relative, path


def _assert_safe_reader_error(root: Path, sample_path: str, private: str = "") -> None:
    try:
        evidence_module.read_fotmob_sample_bytes(root, sample_path)
    except ValueError as exc:
        assert str(exc) == "fotmob_sample_read_invalid"
        assert str(root) not in str(exc)
        assert private not in str(exc)
    else:
        raise AssertionError("unsafe or unreadable sample must fail closed")


def test_shared_fotmob_sample_reader_returns_exact_bytes_and_lowercase_sha_from_fd_read():
    """A preliminary Path.read_bytes or a separately computed digest would break the single-read binding."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative, _path = _sample(root)
        expected = b"saved-fotmob-result-sample"

        with patch.object(Path, "read_bytes", side_effect=AssertionError("unsafe preliminary read")):
            content, digest = evidence_module.read_fotmob_sample_bytes(root, relative)

        assert content == expected
        assert digest == hashlib.sha256(expected).hexdigest()


def test_shared_fotmob_sample_reader_rejects_path_escape_missing_and_every_symlink_layer():
    """Following any attacker-controlled component could bind evidence to bytes outside data/probe."""
    mutations = (
        "outside",
        "traversal",
        "missing",
        "symlinked_probe",
        "symlinked_intermediate",
        "symlinked_final",
    )
    for mutation in mutations:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative, sample = _sample(root)
            requested = relative
            if mutation == "outside":
                outside = root / "private-provider-sample.json"
                outside.write_bytes(b"private-provider-bytes")
                requested = str(outside)
            elif mutation == "traversal":
                requested = "data/probe/leagues/results/../../../../private-provider-sample.json"
            elif mutation == "missing":
                sample.unlink()
            elif mutation == "symlinked_probe":
                probe = root / "data/probe"
                real_probe = root / "data/probe-real"
                probe.rename(real_probe)
                probe.symlink_to(real_probe, target_is_directory=True)
            elif mutation == "symlinked_intermediate":
                results = root / "data/probe/leagues/results"
                real_results = root / "data/probe/leagues/results-real"
                results.rename(real_results)
                results.symlink_to(real_results, target_is_directory=True)
            elif mutation == "symlinked_final":
                target = sample.with_name("private-target.json")
                sample.rename(target)
                sample.symlink_to(target.name)

            _assert_safe_reader_error(root, requested, "private-provider-bytes")


def test_shared_fotmob_sample_reader_rejects_inode_replacement_between_lstat_and_open():
    """Opening a replacement inode after validating the original inode must never return attacker bytes."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative, sample = _sample(root, b"accepted-original-bytes")
        replacement = sample.with_name("replacement.json")
        replacement.write_bytes(b"private-replacement-bytes")
        real_open = os.open
        swapped = False

        def replacing_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if path == sample.name and dir_fd is not None and not swapped:
                swapped = True
                os.replace(replacement, sample)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with patch.object(evidence_module.os, "open", side_effect=replacing_open):
            _assert_safe_reader_error(root, relative, "private-replacement-bytes")
