from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.csl_closing_coverage_runner as coverage_runner
from worldcup.csl_closing_coverage_runner import (
    _derived_report_fields,
    _validate_report,
    run_closing_coverage,
    run_initial_manifest,
)
from worldcup.csl_closing_coverage import coverage_input_fingerprint


ONE_ID_SHA256 = "1de1a3be233ae01a142505365f039d75b4c874a3f0eb78de0aa87b3d8d8efd00"
SHANDONG_ID_SHA256 = "6acbd0ecb91206b112cb0f9fcb0f15d92e34c92f16f1d74ee487702185ea5cf9"
PRODUCTION_INITIAL_EXPECTED_GAPS = 128
PRODUCTION_INITIAL_MATCH_IDS_SHA256 = (
    "530acaa872d753c911861e2cab1e1bf6a2a0a87c595028d9c5e369523a7f6a40"
)
PRODUCTION_INITIAL_MATCH_IDS = tuple(
    """
csl_2026:2026-03-06:chengdu_rongcheng:shenzhen_peng_city
csl_2026:2026-03-07:shandong_taishan:liaoning_tieren
csl_2026:2026-03-07:shanghai_port:henan
csl_2026:2026-03-07:shanghai_shenhua:dalian_yingbo
csl_2026:2026-03-07:tianjin_jinmen_tiger:chongqing_tonglianglong
csl_2026:2026-03-07:yunnan_yukun:qingdao_hainiu
csl_2026:2026-03-08:wuhan_three_towns:beijing_guoan
csl_2026:2026-03-08:zhejiang_professional:qingdao_west_coast
csl_2026:2026-03-13:wuhan_three_towns:dalian_yingbo
csl_2026:2026-03-14:chongqing_tonglianglong:liaoning_tieren
csl_2026:2026-03-14:shandong_taishan:beijing_guoan
csl_2026:2026-03-14:shenzhen_peng_city:tianjin_jinmen_tiger
csl_2026:2026-03-14:zhejiang_professional:shanghai_shenhua
csl_2026:2026-03-15:chengdu_rongcheng:qingdao_hainiu
csl_2026:2026-03-15:henan:yunnan_yukun
csl_2026:2026-03-15:shanghai_port:qingdao_west_coast
csl_2026:2026-03-20:dalian_yingbo:shanghai_port
csl_2026:2026-03-20:qingdao_hainiu:zhejiang_professional
csl_2026:2026-03-21:beijing_guoan:shanghai_shenhua
csl_2026:2026-03-21:chongqing_tonglianglong:chengdu_rongcheng
csl_2026:2026-03-21:henan:wuhan_three_towns
csl_2026:2026-03-21:liaoning_tieren:tianjin_jinmen_tiger
csl_2026:2026-03-21:qingdao_west_coast:shenzhen_peng_city
csl_2026:2026-03-21:yunnan_yukun:shandong_taishan
csl_2026:2026-04-03:chengdu_rongcheng:qingdao_west_coast
csl_2026:2026-04-04:liaoning_tieren:beijing_guoan
csl_2026:2026-04-04:qingdao_hainiu:henan
csl_2026:2026-04-04:shandong_taishan:dalian_yingbo
csl_2026:2026-04-04:shanghai_port:yunnan_yukun
csl_2026:2026-04-05:shenzhen_peng_city:wuhan_three_towns
csl_2026:2026-04-05:tianjin_jinmen_tiger:shanghai_shenhua
csl_2026:2026-04-05:zhejiang_professional:chongqing_tonglianglong
csl_2026:2026-04-10:dalian_yingbo:zhejiang_professional
csl_2026:2026-04-11:chongqing_tonglianglong:wuhan_three_towns
csl_2026:2026-04-11:henan:shandong_taishan
csl_2026:2026-04-11:qingdao_west_coast:liaoning_tieren
csl_2026:2026-04-11:shanghai_shenhua:shanghai_port
csl_2026:2026-04-12:beijing_guoan:chengdu_rongcheng
csl_2026:2026-04-12:shenzhen_peng_city:yunnan_yukun
csl_2026:2026-04-12:tianjin_jinmen_tiger:qingdao_hainiu
csl_2026:2026-04-17:chongqing_tonglianglong:shenzhen_peng_city
csl_2026:2026-04-17:qingdao_hainiu:qingdao_west_coast
csl_2026:2026-04-17:shandong_taishan:shanghai_port
csl_2026:2026-04-17:wuhan_three_towns:chengdu_rongcheng
csl_2026:2026-04-17:yunnan_yukun:tianjin_jinmen_tiger
csl_2026:2026-04-17:zhejiang_professional:beijing_guoan
csl_2026:2026-04-18:dalian_yingbo:henan
csl_2026:2026-04-18:shanghai_shenhua:liaoning_tieren
csl_2026:2026-04-21:chengdu_rongcheng:yunnan_yukun
csl_2026:2026-04-21:shanghai_port:chongqing_tonglianglong
csl_2026:2026-04-21:shenzhen_peng_city:beijing_guoan
csl_2026:2026-04-21:tianjin_jinmen_tiger:shandong_taishan
csl_2026:2026-04-21:wuhan_three_towns:zhejiang_professional
csl_2026:2026-04-22:liaoning_tieren:dalian_yingbo
csl_2026:2026-04-22:qingdao_west_coast:henan
csl_2026:2026-04-22:shanghai_shenhua:qingdao_hainiu
csl_2026:2026-04-25:beijing_guoan:tianjin_jinmen_tiger
csl_2026:2026-04-25:chengdu_rongcheng:zhejiang_professional
csl_2026:2026-04-25:shanghai_port:wuhan_three_towns
csl_2026:2026-04-26:chongqing_tonglianglong:qingdao_west_coast
csl_2026:2026-04-26:dalian_yingbo:yunnan_yukun
csl_2026:2026-04-26:henan:shanghai_shenhua
csl_2026:2026-04-26:qingdao_hainiu:shandong_taishan
csl_2026:2026-04-26:shenzhen_peng_city:liaoning_tieren
csl_2026:2026-05-01:dalian_yingbo:chongqing_tonglianglong
csl_2026:2026-05-01:henan:liaoning_tieren
csl_2026:2026-05-01:shandong_taishan:qingdao_west_coast
csl_2026:2026-05-01:shanghai_shenhua:chengdu_rongcheng
csl_2026:2026-05-01:tianjin_jinmen_tiger:wuhan_three_towns
csl_2026:2026-05-02:qingdao_hainiu:shanghai_port
csl_2026:2026-05-02:yunnan_yukun:beijing_guoan
csl_2026:2026-05-02:zhejiang_professional:shenzhen_peng_city
csl_2026:2026-05-05:chongqing_tonglianglong:henan
csl_2026:2026-05-05:liaoning_tieren:chengdu_rongcheng
csl_2026:2026-05-05:qingdao_west_coast:tianjin_jinmen_tiger
csl_2026:2026-05-05:shandong_taishan:shanghai_shenhua
csl_2026:2026-05-06:beijing_guoan:dalian_yingbo
csl_2026:2026-05-06:shanghai_port:shenzhen_peng_city
csl_2026:2026-05-06:wuhan_three_towns:qingdao_hainiu
csl_2026:2026-05-06:yunnan_yukun:zhejiang_professional
csl_2026:2026-05-09:chengdu_rongcheng:henan
csl_2026:2026-05-09:shanghai_shenhua:chongqing_tonglianglong
csl_2026:2026-05-10:beijing_guoan:shanghai_port
csl_2026:2026-05-10:liaoning_tieren:yunnan_yukun
csl_2026:2026-05-10:qingdao_hainiu:dalian_yingbo
csl_2026:2026-05-10:qingdao_west_coast:wuhan_three_towns
csl_2026:2026-05-10:shenzhen_peng_city:shandong_taishan
csl_2026:2026-05-10:zhejiang_professional:tianjin_jinmen_tiger
csl_2026:2026-05-15:beijing_guoan:qingdao_hainiu
csl_2026:2026-05-15:dalian_yingbo:qingdao_west_coast
csl_2026:2026-05-15:henan:shenzhen_peng_city
csl_2026:2026-05-15:shanghai_port:zhejiang_professional
csl_2026:2026-05-15:tianjin_jinmen_tiger:chengdu_rongcheng
csl_2026:2026-05-16:shandong_taishan:chongqing_tonglianglong
csl_2026:2026-05-16:wuhan_three_towns:liaoning_tieren
csl_2026:2026-05-16:yunnan_yukun:shanghai_shenhua
csl_2026:2026-05-19:chengdu_rongcheng:shanghai_port
csl_2026:2026-05-19:qingdao_west_coast:beijing_guoan
csl_2026:2026-05-19:shenzhen_peng_city:dalian_yingbo
csl_2026:2026-05-19:tianjin_jinmen_tiger:henan
csl_2026:2026-05-20:chongqing_tonglianglong:yunnan_yukun
csl_2026:2026-05-20:liaoning_tieren:qingdao_hainiu
csl_2026:2026-05-20:shanghai_shenhua:wuhan_three_towns
csl_2026:2026-05-20:zhejiang_professional:shandong_taishan
csl_2026:2026-05-23:beijing_guoan:henan
csl_2026:2026-05-23:dalian_yingbo:chengdu_rongcheng
csl_2026:2026-05-23:shanghai_port:tianjin_jinmen_tiger
csl_2026:2026-05-24:qingdao_hainiu:chongqing_tonglianglong
csl_2026:2026-05-24:shandong_taishan:wuhan_three_towns
csl_2026:2026-05-24:shanghai_shenhua:shenzhen_peng_city
csl_2026:2026-05-24:yunnan_yukun:qingdao_west_coast
csl_2026:2026-05-24:zhejiang_professional:liaoning_tieren
csl_2026:2026-05-29:liaoning_tieren:shanghai_port
csl_2026:2026-05-30:chengdu_rongcheng:shandong_taishan
csl_2026:2026-05-30:chongqing_tonglianglong:beijing_guoan
csl_2026:2026-05-30:henan:zhejiang_professional
csl_2026:2026-05-30:qingdao_west_coast:shanghai_shenhua
csl_2026:2026-05-30:shenzhen_peng_city:qingdao_hainiu
csl_2026:2026-05-31:tianjin_jinmen_tiger:dalian_yingbo
csl_2026:2026-05-31:wuhan_three_towns:yunnan_yukun
csl_2026:2026-06-26:qingdao_hainiu:yunnan_yukun
csl_2026:2026-06-27:beijing_guoan:wuhan_three_towns
csl_2026:2026-06-27:chongqing_tonglianglong:tianjin_jinmen_tiger
csl_2026:2026-06-27:henan:shanghai_port
csl_2026:2026-06-27:liaoning_tieren:shandong_taishan
csl_2026:2026-06-27:shenzhen_peng_city:chengdu_rongcheng
csl_2026:2026-06-28:dalian_yingbo:shanghai_shenhua
csl_2026:2026-06-28:qingdao_west_coast:zhejiang_professional
""".strip().splitlines()
)


def _run_test_coverage(*, root: Path, **kwargs):
    original_count = coverage_runner.INITIAL_EXPECTED_GAPS
    original_hash = coverage_runner.INITIAL_MATCH_IDS_SHA256
    coverage_runner.INITIAL_EXPECTED_GAPS = 1
    coverage_runner.INITIAL_MATCH_IDS_SHA256 = ONE_ID_SHA256
    try:
        return run_closing_coverage(
            root=root,
            expected_initial_count=1,
            expected_initial_ids_sha256=ONE_ID_SHA256,
            **kwargs,
        )
    finally:
        coverage_runner.INITIAL_EXPECTED_GAPS = original_count
        coverage_runner.INITIAL_MATCH_IDS_SHA256 = original_hash


def _seed_inputs(root: Path) -> None:
    results = root / "data/cache/club_results_csl_2026.csv"
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-03-06",
                "home_team": "成都蓉城",
                "away_team": "深圳新鹏城",
                "home_score": "5",
                "away_score": "1",
                "neutral": "0",
            }
        )
    raw = root / "data/cache/csl_results_sources"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "cfl_official_2026.json").write_text(
        json.dumps(
            {
                "data": {
                    "dataList": [
                        {
                            "id": "official-1",
                            "match_status": "Played",
                            "local_date": "2026-03-06",
                            "local_time": "19:35:00",
                            "week": 1,
                            "home_contestant_name": "成都蓉城",
                            "away_contestant_name": "深圳新鹏城",
                            "ft_home_score": 5,
                            "ft_away_score": 1,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (raw / "sevenm_2026_fixture.js").write_text(
        '''
        var Tmp_bh_Arr = [ 7001 ];
        var Run_Arr = [ 1 ];
        var Time_Arr = [ "2026,03,06,19,35,00" ];
        var Scores_Arr = [ "5-1(2-0)" ];
        var TeamA_Arr = [ "成都蓉城" ];
        var TeamB_Arr = [ "深圳新鹏城" ];
        var Stat_Arr = [ 4 ];
        var Memo_Arr = [ "" ];
        ''',
        encoding="utf-8",
    )


def _rewrite_home_identity(root: Path) -> None:
    for path in (
        root / "data/cache/club_results_csl_2026.csv",
        root / "data/cache/csl_results_sources/cfl_official_2026.json",
        root / "data/cache/csl_results_sources/sevenm_2026_fixture.js",
    ):
        source = path.read_text(encoding="utf-8")
        path.write_text(source.replace("成都蓉城", "山东泰山"), encoding="utf-8")


def _seed_observed_closing(root: Path) -> None:
    history = root / "data/local/diagnostics/csl_history"
    history.mkdir(parents=True, exist_ok=True)
    (history / "snapshot_observed.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_at": "2026-03-06T11:10:00+00:00",
                "competition": {"id": "csl_2026"},
                "matches": [
                    {
                        "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
                        "home_canonical": "chengdu_rongcheng",
                        "away_canonical": "shenzhen_peng_city",
                        "match_decision": {
                            "schema_version": 2,
                            "policy_version": "match_pick_v3",
                            "label": "NO_CLEAN_MARKET",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _pending_payload(
    *, attempt_id: str, input_fingerprint: str | None
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "pending",
        "attempt_id": attempt_id,
        "attempted_at": "2026-08-13T02:00:00+00:00",
        "input_fingerprint": input_fingerprint,
        "reason": "coverage_report_commit_pending",
        "error_type": None,
    }


def _concurrent_coverage_worker(root_value: str, event: dict[str, str]) -> None:
    result = _run_test_coverage(
        root=Path(root_value),
        write=True,
        generated_at=event["observed_at"],
        audit_events=[event],
    )
    if result.get("status") not in {"stored", "unchanged"}:
        raise RuntimeError("coverage worker failed")


def test_manifest_defaults_to_zero_write_then_freezes_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        dry = run_initial_manifest(
            root=root,
            write=False,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert dry == {
            "status": "dry_run",
            "write": False,
            "competition_id": "csl_2026",
            "season": "2026",
            "matches": 1,
            "observed_cutoff": "2026-06-29",
        }
        assert not target.exists()
        assert not (
            root / "data/local/diagnostics/csl_closing_coverage.lock"
        ).exists()
        assert not (root / "data/local").exists()

        stored = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        original = target.read_bytes()
        unchanged = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T03:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert stored["status"] == "stored"
        assert unchanged["status"] == "unchanged"
        assert unchanged["matches"] == 1
        assert target.read_bytes() == original


def test_manifest_validates_fixed_membership_before_first_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"

        result = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256="0" * 64,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_inputs_unavailable"
        assert result["error_type"] == "ValueError"
        assert not target.exists()


def test_manifest_freeze_blocks_changed_match_identity():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        stored = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert stored["status"] == "stored"
        original = target.read_bytes()
        _rewrite_home_identity(root)

        result = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T03:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=SHANDONG_ID_SHA256,
        )

        assert result == {
            "status": "blocked",
            "reason": "initial_manifest_identity_mismatch",
            "write": True,
            "competition_id": "csl_2026",
            "season": "2026",
        }
        assert target.read_bytes() == original


def test_manifest_closed_schema_rejects_sensitive_root_row_and_source_fields():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        stored = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert stored["status"] == "stored"
        canonical = json.loads(target.read_text(encoding="utf-8"))

        mutations = (
            lambda value: value.__setitem__("Authorization", "private-root"),
            lambda value: value["matches"][0].__setitem__("Cookie", "private-row"),
            lambda value: value["matches"][0]["source_match_ids"].__setitem__(
                "api_key", "private-source"
            ),
            lambda value: value.pop("membership_policy"),
            lambda value: value["matches"][0].pop("probe_status"),
        )
        for mutate in mutations:
            poisoned = deepcopy(canonical)
            mutate(poisoned)
            target.write_text(json.dumps(poisoned, ensure_ascii=False), encoding="utf-8")
            original = target.read_bytes()

            result = run_initial_manifest(
                root=root,
                write=True,
                created_at="2026-08-13T03:00:00+00:00",
                expected_count=1,
                expected_ids_sha256=ONE_ID_SHA256,
            )

            assert result["status"] == "blocked"
            assert result["reason"] == "initial_manifest_identity_mismatch"
            assert target.read_bytes() == original
            target.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")


def test_manifest_rejects_duplicate_source_identity_without_overwrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        poisoned = json.loads(target.read_text(encoding="utf-8"))
        duplicate = deepcopy(poisoned["matches"][0])
        duplicate["match_id"] = "csl_2026:2026-03-07:other_home:other_away"
        duplicate["match_date"] = "2026-03-07"
        duplicate["home_canonical"] = "other_home"
        duplicate["away_canonical"] = "other_away"
        poisoned["matches"].append(duplicate)
        poisoned["expected_match_count"] = 2
        target.write_text(json.dumps(poisoned, ensure_ascii=False), encoding="utf-8")
        original = target.read_bytes()

        result = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T03:00:00+00:00",
            expected_count=2,
            expected_ids_sha256="0" * 64,
        )

        assert result["status"] == "blocked"
        assert target.read_bytes() == original


def test_frozen_manifest_reuse_ignores_later_observed_history():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        stored = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert stored["status"] == "stored"
        original = target.read_bytes()
        _seed_observed_closing(root)

        unchanged = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T03:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )

        assert unchanged["status"] == "unchanged"
        assert unchanged["matches"] == 1
        assert target.read_bytes() == original


def test_report_pending_lifecycle_is_atomic_and_idempotent():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        dry_run_lock = root / "dry-run-coverage.lock"
        dry = _run_test_coverage(
            root=root,
            write=False,
            generated_at="2026-08-13T02:00:00+00:00",
            lock_path=dry_run_lock,
        )
        assert dry["status"] == "dry_run"
        assert not report_path.exists()
        assert not pending_path.exists()
        assert not dry_run_lock.exists()

        stored = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        assert stored["status"] == "stored"
        assert not pending_path.exists()

        event = {
            "observed_at": "2026-03-06T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
            "home_canonical": "chengdu_rongcheng",
            "away_canonical": "shenzhen_peng_city",
            "issue_code": "quota_blocked",
        }
        updated = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:30:00+00:00",
            audit_events=[event, event],
        )
        same_event = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:45:00+00:00",
            audit_events=[event],
        )
        canonical_payload = json.loads(report_path.read_text(encoding="utf-8"))
        canonical = report_path.read_bytes()
        assert updated["status"] == "stored"
        assert same_event["status"] == "unchanged"
        assert canonical_payload["operational_event_counts"] == {"quota_blocked": 1}
        assert len(canonical_payload["operational_events"]) == 1

        pending_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "attempt_id": "stale-attempt",
                    "attempted_at": "2026-08-13T02:50:00+00:00",
                    "input_fingerprint": updated["input_fingerprint"],
                    "reason": "coverage_report_commit_pending",
                    "error_type": None,
                }
            ),
            encoding="utf-8",
        )
        unchanged = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )
        assert unchanged["status"] == "unchanged"
        assert unchanged["stale_pending_cleared"] is True
        assert report_path.read_bytes() == canonical
        assert not pending_path.exists()


def test_report_write_failure_preserves_redacted_pending():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )

        def broken_report_write(_path, _payload):
            raise OSError("private odds secret must not leak")

        failed = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
            report_write=broken_report_write,
        )
        pending = json.loads(pending_path.read_text(encoding="utf-8"))

        assert failed["status"] == "error"
        assert failed["reason"] == "coverage_report_commit_failed"
        assert set(pending) == {
            "schema_version",
            "status",
            "attempt_id",
            "attempted_at",
            "input_fingerprint",
            "reason",
            "error_type",
        }
        serialized = json.dumps(
            {"failed": failed, "pending": pending}, ensure_ascii=False
        )
        for forbidden in (
            "private odds secret",
            "api_key",
            "Authorization",
            "Cookie",
            "bookmaker",
            "THE_ODDS_API_KEY",
        ):
            assert forbidden not in serialized


def test_write_mode_persists_pending_before_reconciliation_failure():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        pending = json.loads(pending_path.read_text(encoding="utf-8"))

    assert result["status"] == "blocked"
    assert result["reason"] == "coverage_inputs_unavailable"
    assert pending["reason"] == "coverage_reconciliation_failed"
    assert pending["input_fingerprint"] is None
    assert pending["error_type"] == "FileNotFoundError"


def test_reconciliation_failure_preserves_prior_pending_fingerprint():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        prior = _pending_payload(
            attempt_id="prior-attempt", input_fingerprint="f" * 64
        )
        pending_path.write_text(json.dumps(prior), encoding="utf-8")
        (root / "data/cache/csl_results_sources/cfl_official_2026.json").unlink()

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )
        recovered = json.loads(pending_path.read_text(encoding="utf-8"))

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_inputs_unavailable"
        assert recovered["attempt_id"] != "prior-attempt"
        assert recovered["input_fingerprint"] == "f" * 64
        assert recovered["reason"] == "coverage_reconciliation_failed"
        assert recovered["error_type"] == "FileNotFoundError"


def test_cleanup_does_not_remove_pending_owned_by_another_attempt():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )

        def replace_pending_owner(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            foreign = _pending_payload(
                attempt_id="foreign-attempt",
                input_fingerprint=payload["input_fingerprint"],
            )
            pending_path.write_text(json.dumps(foreign), encoding="utf-8")

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
            report_write=replace_pending_owner,
        )
        retained = json.loads(pending_path.read_text(encoding="utf-8"))

        assert result["status"] == "stored_pending_cleanup"
        assert result["reason"] == "coverage_pending_owner_changed"
        assert retained["attempt_id"] == "foreign-attempt"


def test_invalid_prior_pending_is_blocked_without_overwrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        pending_path.write_text("[]", encoding="utf-8")
        original = pending_path.read_bytes()

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert result == {
            "status": "blocked",
            "reason": "coverage_pending_invalid",
            "error_type": "ValueError",
            "write": True,
        }
        assert pending_path.read_bytes() == original


def test_official_payload_container_error_is_stably_redacted():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        official = root / "data/cache/csl_results_sources/cfl_official_2026.json"
        official.write_text("[]", encoding="utf-8")

        result = run_initial_manifest(
            root=root,
            write=False,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_inputs_unavailable"
        assert result["error_type"] == "ValueError"
        assert "exception" not in result


def test_invalid_history_container_is_stably_redacted():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        history = root / "data/local/diagnostics/csl_history"
        history.mkdir(parents=True, exist_ok=True)
        (history / "snapshot_bad.json").write_text("[]", encoding="utf-8")

        result = run_initial_manifest(
            root=root,
            write=False,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_inputs_unavailable"
        assert result["error_type"] == "ValueError"


def test_corrupt_canonical_report_is_never_overwritten():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{corrupt", encoding="utf-8")
        original = report_path.read_bytes()

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert result == {
            "status": "blocked",
            "reason": "coverage_report_invalid",
            "error_type": "JSONDecodeError",
            "write": True,
        }
        assert report_path.read_bytes() == original
        assert not pending_path.exists()


def test_invalid_canonical_report_schema_is_never_overwritten():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text('{"schema_version": 1}', encoding="utf-8")
        original = report_path.read_bytes()

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_report_invalid"
        assert result["error_type"] == "ValueError"
        assert report_path.read_bytes() == original
        assert not pending_path.exists()


def test_canonical_report_rejects_inconsistent_derived_fields():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        stored = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        assert stored["status"] == "stored"
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        canonical = json.loads(report_path.read_text(encoding="utf-8"))

        mutations = (
            ("summary", lambda report: report["summary"].__setitem__("missing_count", 999)),
            (
                "performance",
                lambda report: report["performance"]["observed"][
                    "decision_sample"
                ].__setitem__("sample_too_small", False),
            ),
            (
                "event_counts",
                lambda report: report.__setitem__(
                    "operational_event_counts", {"quota_blocked": 999}
                ),
            ),
        )
        for _name, mutate in mutations:
            poisoned = json.loads(json.dumps(canonical))
            mutate(poisoned)
            report_path.write_text(
                json.dumps(poisoned, ensure_ascii=False), encoding="utf-8"
            )
            original = report_path.read_bytes()

            result = _run_test_coverage(
                root=root,
                write=True,
                generated_at="2026-08-13T03:00:00+00:00",
            )

            assert result == {
                "status": "blocked",
                "reason": "coverage_report_invalid",
                "error_type": "ValueError",
                "write": True,
            }
            assert report_path.read_bytes() == original
            assert not pending_path.exists()


def _make_report_mutation_self_consistent(report: dict) -> None:
    derived = _derived_report_fields(
        report["matches"], report["operational_events"]
    )
    for key, value in derived.items():
        report[key] = value
    report["input_fingerprint"] = coverage_input_fingerprint(report)


def _production_membership_report() -> dict:
    encoded_ids = json.dumps(
        sorted(PRODUCTION_INITIAL_MATCH_IDS),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(PRODUCTION_INITIAL_MATCH_IDS) == PRODUCTION_INITIAL_EXPECTED_GAPS
    assert (
        hashlib.sha256(encoded_ids).hexdigest()
        == PRODUCTION_INITIAL_MATCH_IDS_SHA256
    )

    def make_missing_row(match_id: str, *, initial: bool) -> dict:
        competition_id, match_date, home_canonical, away_canonical = (
            match_id.split(":")
        )
        return {
            "match_id": match_id,
            "competition_id": competition_id,
            "season": "2026",
            "match_date": match_date,
            "kickoff_at_utc": None,
            "home_team": home_canonical,
            "away_team": away_canonical,
            "home_canonical": home_canonical,
            "away_canonical": away_canonical,
            "provenance_class": "none",
            "coverage_status": "missing",
            "reason_code": "source_unapproved",
            "reason_codes": ["source_unapproved"],
            "closing_snapshot_at": None,
            "closing_snapshot_run_id": None,
            "audit_issue_codes": [] if initial else ["closing_archive_missing"],
            "operational_history_codes": [],
            "settlement": None,
        }

    extra_match_id = "csl_2026:2026-07-03:fixture_home:fixture_away"
    matches = [
        make_missing_row(match_id, initial=True)
        for match_id in PRODUCTION_INITIAL_MATCH_IDS
    ]
    matches.append(make_missing_row(extra_match_id, initial=False))
    report = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": "2026-08-13T02:00:00+00:00",
        "membership": {
            "initial_missing_count": PRODUCTION_INITIAL_EXPECTED_GAPS,
            "initial_missing_match_ids": list(PRODUCTION_INITIAL_MATCH_IDS),
            "observed_cutoff": "2026-06-29",
        },
        "operational_events": [],
        "matches": matches,
        "input_fingerprint": "0" * 64,
    }
    _make_report_mutation_self_consistent(report)
    return _validate_report(report)


def test_canonical_report_rejects_refingerprinted_127_member_subset_without_overwrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        poisoned = deepcopy(_production_membership_report())
        removed_id = poisoned["membership"]["initial_missing_match_ids"].pop(0)
        poisoned["membership"]["initial_missing_count"] = 127
        removed_match = next(
            match for match in poisoned["matches"] if match["match_id"] == removed_id
        )
        removed_match["audit_issue_codes"] = ["closing_archive_missing"]
        _make_report_mutation_self_consistent(poisoned)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(poisoned, ensure_ascii=False), encoding="utf-8"
        )
        original = report_path.read_bytes()

        blocked = run_closing_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert blocked == {
            "status": "blocked",
            "reason": "coverage_report_invalid",
            "error_type": "ValueError",
            "write": True,
        }, blocked
        assert report_path.read_bytes() == original
        assert not pending_path.exists()


def test_canonical_report_rejects_refingerprinted_membership_substitution_without_overwrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        poisoned = deepcopy(_production_membership_report())
        initial_ids = poisoned["membership"]["initial_missing_match_ids"]
        removed_id = initial_ids.pop(0)
        extra_id = next(
            match["match_id"]
            for match in poisoned["matches"]
            if match["match_id"] not in set(PRODUCTION_INITIAL_MATCH_IDS)
        )
        initial_ids.append(extra_id)
        initial_ids.sort()
        assert len(initial_ids) == PRODUCTION_INITIAL_EXPECTED_GAPS
        for match in poisoned["matches"]:
            if match["match_id"] == removed_id:
                match["audit_issue_codes"] = ["closing_archive_missing"]
            elif match["match_id"] == extra_id:
                match["audit_issue_codes"] = []
        _make_report_mutation_self_consistent(poisoned)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(poisoned, ensure_ascii=False), encoding="utf-8"
        )
        original = report_path.read_bytes()

        blocked = run_closing_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert blocked == {
            "status": "blocked",
            "reason": "coverage_report_invalid",
            "error_type": "ValueError",
            "write": True,
        }, blocked
        assert report_path.read_bytes() == original
        assert not pending_path.exists()


def test_canonical_report_closed_schemas_reject_sensitive_extra_fields():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        event = {
            "observed_at": "2026-03-06T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
            "home_canonical": "chengdu_rongcheng",
            "away_canonical": "shenzhen_peng_city",
            "issue_code": "quota_blocked",
        }
        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
            audit_events=[event],
        )
        assert result["status"] == "stored"
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        canonical = json.loads(report_path.read_text(encoding="utf-8"))

        mutations = (
            lambda value: value.__setitem__("Authorization", "private-root"),
            lambda value: value["membership"].__setitem__("Cookie", "private-membership"),
            lambda value: value["summary"].__setitem__("api_key", 1),
            lambda value: value["matches"][0].__setitem__("Cookie", "private-match"),
            lambda value: value["operational_events"][0].__setitem__(
                "Authorization", "private-event"
            ),
            lambda value: value["performance"].__setitem__("Cookie", "private-performance"),
            lambda value: value["performance"]["observed"].__setitem__(
                "Authorization", "private-observed"
            ),
            lambda value: value["performance"]["observed"]["decision_tally"].__setitem__(
                "Cookie", 0
            ),
            lambda value: value["performance"]["observed"]["decision_sample"].__setitem__(
                "Authorization", "private-sample"
            ),
            lambda value: value["performance"]["reconstructed"].__setitem__(
                "Cookie", "private-reconstructed"
            ),
        )
        for mutate in mutations:
            poisoned = deepcopy(canonical)
            mutate(poisoned)
            poisoned["input_fingerprint"] = coverage_input_fingerprint(poisoned)
            report_path.write_text(
                json.dumps(poisoned, ensure_ascii=False), encoding="utf-8"
            )
            original = report_path.read_bytes()

            blocked = _run_test_coverage(
                root=root,
                write=True,
                generated_at="2026-08-13T03:00:00+00:00",
            )

            assert blocked == {
                "status": "blocked",
                "reason": "coverage_report_invalid",
                "error_type": "ValueError",
                "write": True,
            }, blocked
            assert report_path.read_bytes() == original
            assert not pending_path.exists()
            report_path.write_text(
                json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
            )


def test_refingerprinted_status_provenance_reason_and_settlement_tampering_is_rejected():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        assert result["status"] == "stored"
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        canonical = json.loads(report_path.read_text(encoding="utf-8"))

        mutations = (
            lambda match: match.__setitem__(
                "coverage_status", "observed_current_decision"
            ),
            lambda match: match.__setitem__("provenance_class", "observed"),
            lambda match: (
                match.__setitem__("reason_code", "observed_closing"),
                match.__setitem__("reason_codes", ["observed_closing"]),
            ),
            lambda match: match.__setitem__(
                "settlement",
                {
                    "status": "hit",
                    "label": "命中",
                    "detail": "全场 5-1",
                    "settlement_class": "full_win",
                },
            ),
        )
        for mutate in mutations:
            poisoned = deepcopy(canonical)
            mutate(poisoned["matches"][0])
            _make_report_mutation_self_consistent(poisoned)
            report_path.write_text(
                json.dumps(poisoned, ensure_ascii=False), encoding="utf-8"
            )
            original = report_path.read_bytes()

            blocked = _run_test_coverage(
                root=root,
                write=True,
                generated_at="2026-08-13T03:00:00+00:00",
            )

            assert blocked["status"] == "blocked"
            assert blocked["reason"] == "coverage_report_invalid"
            assert report_path.read_bytes() == original
            assert not pending_path.exists()
            report_path.write_text(
                json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
            )


def test_observed_settlement_closed_schema_rejects_extra_field_without_overwrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        _seed_observed_closing(root)
        history = root / "data/local/diagnostics/csl_history/snapshot_observed.json"
        snapshot = json.loads(history.read_text(encoding="utf-8"))
        snapshot["matches"][0]["match_decision"] = {
            "schema_version": 2,
            "policy_version": "match_pick_v3",
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": "home",
            "odds": 1.8,
        }
        history.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        assert result["status"] == "stored"
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        poisoned = json.loads(report_path.read_text(encoding="utf-8"))
        poisoned["matches"][0]["settlement"]["Cookie"] = "private-settlement"
        _make_report_mutation_self_consistent(poisoned)
        report_path.write_text(json.dumps(poisoned, ensure_ascii=False), encoding="utf-8")
        original = report_path.read_bytes()

        blocked = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )

        assert blocked["status"] == "blocked"
        assert blocked["reason"] == "coverage_report_invalid"
        assert report_path.read_bytes() == original


def test_invalid_generated_at_is_rejected_before_lock_or_pending_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        lock_path = root / "invalid-time.lock"

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="not-a-time",
            lock_path=lock_path,
        )

        assert result == {
            "status": "blocked",
            "reason": "coverage_generated_at_invalid",
            "error_type": "ValueError",
            "write": True,
        }
        assert not pending_path.exists()
        assert not lock_path.exists()


def test_invalid_generated_at_does_not_overwrite_prior_pending():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        prior = _pending_payload(
            attempt_id="prior-valid-attempt", input_fingerprint="f" * 64
        )
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(json.dumps(prior), encoding="utf-8")
        original = pending_path.read_bytes()
        lock_path = root / "invalid-retry-time.lock"

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="not-a-time",
            lock_path=lock_path,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "coverage_generated_at_invalid"
        assert result["error_type"] == "ValueError"
        assert pending_path.read_bytes() == original
        assert not lock_path.exists()


def test_report_pending_alias_is_blocked_without_touching_path():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        alias = root / "shared-output.json"
        alias.write_text('{"sentinel": true}', encoding="utf-8")
        original = alias.read_bytes()
        lock = root / "coverage.lock"

        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
            report_path=alias,
            pending_path=alias,
            lock_path=lock,
        )

        assert result == {
            "status": "blocked",
            "reason": "coverage_path_conflict",
            "error_type": "ValueError",
            "write": True,
        }
        assert alias.read_bytes() == original
        assert not lock.exists()


def test_coverage_outputs_cannot_alias_protected_inputs():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        protected = (
            root / "data/cache/club_results_csl_2026.csv",
            root / "data/local/backfill/csl_2026/initial_missing_manifest.json",
            root / "data/cache/csl_results_sources/cfl_official_2026.json",
        )
        originals = [path.read_bytes() for path in protected]

        cases = (
            {"report_path": protected[0]},
            {"pending_path": protected[1]},
            {"lock_path": protected[2]},
        )
        for case in cases:
            result = _run_test_coverage(
                root=root,
                write=True,
                generated_at="2026-08-13T03:00:00+00:00",
                **case,
            )
            assert result["status"] == "blocked"
            assert result["reason"] == "coverage_path_conflict"
            assert result["error_type"] == "ValueError"

        assert [path.read_bytes() for path in protected] == originals


def test_manifest_output_cannot_alias_results_input():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        results_path = root / "data/cache/club_results_csl_2026.csv"
        original = results_path.read_bytes()
        lock_path = root / "manifest.lock"

        result = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
            output=results_path,
            lock_path=lock_path,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "initial_manifest_path_conflict"
        assert result["error_type"] == "ValueError"
        assert results_path.read_bytes() == original
        assert not lock_path.exists()


def test_process_lock_preserves_concurrent_operational_events():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        base = {
            "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
            "home_canonical": "chengdu_rongcheng",
            "away_canonical": "shenzhen_peng_city",
        }
        events = (
            {
                **base,
                "observed_at": "2026-08-13T02:30:00+00:00",
                "match_id": "event-quota",
                "issue_code": "quota_blocked",
            },
            {
                **base,
                "observed_at": "2026-08-13T02:31:00+00:00",
                "match_id": "event-provider",
                "issue_code": "provider_refresh_failed",
            },
        )
        context = multiprocessing.get_context("fork")
        processes = [
            context.Process(
                target=_concurrent_coverage_worker,
                args=(str(root), event),
            )
            for event in events
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            assert process.exitcode == 0

        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = (
            root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["operational_event_counts"] == {
            "provider_refresh_failed": 1,
            "quota_blocked": 1,
        }
        assert {event["issue_code"] for event in report["operational_events"]} == {
            "provider_refresh_failed",
            "quota_blocked",
        }
        assert len(report["operational_events"]) == 2
        assert not pending_path.exists()
