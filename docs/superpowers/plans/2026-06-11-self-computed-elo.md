# 自算 Elo（基线锚定 + 本届赛果增量重放）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Elo 来源不再依赖 eloratings 网站：以当前可信的官方 Elo 缓存为基线（baseline），每轮 refresh 用 openfootball 已完赛比分做增量重放，生成最新 `elo_world.tsv`；eloratings 抓取降级为"可选重新锚定"，抓不到只记 `source_errors`，不再标 `stale_sources`、不再依赖 48h 宽限期。

**Architecture:** 新增纯函数模块 `worldcup/elo_local.py`（冻结基线、加载基线、按赛果重放、渲染 TSV、行数护栏）；`refresh_runner` 改为"先试抓官方（成功则重新锚定基线），随后**无论抓取成败**都从基线+赛果重算并覆写 `data/cache/elo_world.tsv`"。下游 `build_snapshot_from_cache` 读同一文件，pipeline/快照/页面零改动。重放公式直接复用 `worldcup/elo_replay.py` 的 `update_pair`（已用 4.9 万场对照官方验证，top-10 overlap 10/10）。

**Tech Stack:** Python 标准库，自带测试 runner（无 pytest）。

**设计依据：** `docs/superpowers/specs/2026-06-11-elo-source-resilience-design.md`（含 WAF 探测结论、方案对比、中立口径决策）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

红/绿都跑全量，用 `FAIL <file>::<test>` 行确认。基线为当前全绿（约 292/292，以实际为准）。

**关键决策（实现者必读，勿改）：**

- **每轮全量重算、无状态累积**：每次 compute 都从"基线 + 全部基线后赛果"重新算，结果幂等，不存在跑飞累积；不要做增量缓存。
- **K=60、全部按中立场计算**（`neutral=True`）：东道主主场优势由 pipeline 的 host advantage 单独处理，Elo 增量按中立算避免双重计入；已知美墨加三队会有少量漂移，官方源恢复后重新锚定即自动校正（见设计文档）。
- **重新锚定语义**：eloratings 抓取成功 → 当场把抓到的官方文件冻结为新基线，`baseline_at` 取本轮 `observed`；只对 `kickoff_at_utc >= baseline_at` 的赛果做增量（官方值已包含更早的比赛）。
- **基线缺失时自动冻结**：refresh 时若无基线文件且 `data/cache/elo_world.tsv` / `elo_teams.tsv` 可解析，则以这两个文件自动冻结基线，`baseline_at` 取 `elo_world.tsv` 的 mtime（UTC ISO）。这是为了避免"代码合入 → 手工冻结"之间 LaunchAgent 先跑一轮导致报错。
- **护栏**：计算产物必须能被 `parse_elo_ratings` 解析，且行数**不少于基线行数**（默认值；调用方可显式传 `min_rows` 覆盖），否则抛错并保留旧 `elo_world.tsv`（沿用"宁可用旧数据也不写坏缓存"的既有原则；真实基线约 244 行）。
- **占位队/未映射队跳过**：`parse_openfootball_results` 已跳过占位队；映射不到 Elo code 的队按跳过处理，不报错。
- 全程离线（fake transport 测试）；不 push、不部署、不触发 live refresh、不调用 The Odds API、不真实访问 eloratings。每任务本地 commit。

---

### Task 1: `worldcup/elo_local.py` 纯函数层

**Files:**
- Create: `worldcup/elo_local.py`
- Test: `tests/test_elo_local.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_elo_local.py`：

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.collectors.eloratings import parse_elo_ratings
from worldcup.elo_local import (
    compute_updated_world_tsv,
    freeze_baseline,
    load_baseline,
)

WORLD_TSV = "1\t1\tMX\t1875\n2\t2\tZA\t1700\n3\t3\tCA\t1810\n"
TEAMS_TSV = "MX\tMexico\nZA\tSouth Africa\nCA\tCanada\n"
OPENFOOTBALL = {
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "13:00 UTC-6",
            "team1": "Mexico",
            "team2": "South Africa",
            "ground": "Mexico City",
            "score1": 2,
            "score2": 0,
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-12",
            "time": "13:00 UTC-6",
            "team1": "Canada",
            "team2": "Mexico",
            "ground": "Toronto",
        },
    ]
}


def _seed_cache(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "elo_world.tsv").write_text(WORLD_TSV)
    (cache / "elo_teams.tsv").write_text(TEAMS_TSV)
    (cache / "openfootball_2026.json").write_text(json.dumps(OPENFOOTBALL))


def test_freeze_and_load_baseline_roundtrip():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)

        meta = freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        assert meta["baseline_at"] == "2026-06-01T00:00:00+00:00"
        ratings, aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1875
        assert aliases["Mexico"] == "MX"
        assert baseline_at == "2026-06-01T00:00:00+00:00"


def test_compute_applies_finished_results_after_baseline():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        out = compute_updated_world_tsv(cache, min_rows=2)
        updated = parse_elo_ratings(out)

        # 墨西哥 2-0 胜：墨西哥升、南非降，零和；加拿大那场未完赛不参与
        assert updated["MX"].rating > 1875
        assert updated["ZA"].rating < 1700
        assert updated["CA"].rating == 1810
        assert updated["MX"].rating - 1875 == 1700 - updated["ZA"].rating
        # rank 按新 rating 重排：MX 仍第一
        assert updated["MX"].rank == 1


def test_compute_skips_results_before_baseline():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-12T00:00:00+00:00")

        out = compute_updated_world_tsv(cache, min_rows=2)
        updated = parse_elo_ratings(out)

        # 6-11 完赛的比赛早于基线时间，视为官方基线已包含，不重复计入
        assert updated["MX"].rating == 1875
        assert updated["ZA"].rating == 1700


def test_compute_rejects_too_few_rows():
    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        try:
            compute_updated_world_tsv(cache, min_rows=200)
        except ValueError as exc:
            assert "rows" in str(exc)
        else:
            raise AssertionError("expected ValueError for too few rows")


def test_load_baseline_raises_when_missing():
    with TemporaryDirectory() as tmp:
        try:
            load_baseline(Path(tmp))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("expected FileNotFoundError")
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_elo_local.py::...`，报 `No module named 'worldcup.elo_local'`（runner 在 import 阶段对该文件所有测试报错）。

- [ ] **Step 3: 实现 `worldcup/elo_local.py`**

```python
"""Locally replayed Elo: freeze a trusted official baseline, then update it
from openfootball finished results. Purely offline; never contacts eloratings.

设计依据：docs/superpowers/specs/2026-06-11-elo-source-resilience-design.md
口径：K=60（世界杯决赛圈）、全部按中立场计算（东道主优势由 pipeline 单独处理）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.models import EloRating
from worldcup.collectors.openfootball import parse_openfootball_results
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.elo_replay import update_pair

BASELINE_WORLD = "elo_baseline_world.tsv"
BASELINE_TEAMS = "elo_baseline_teams.tsv"
BASELINE_META = "elo_baseline_meta.json"
WORLD_CUP_K = 60.0


def freeze_baseline(cache_dir: str | Path, baseline_at: str) -> dict:
    cache = Path(cache_dir)
    world_text = (cache / "elo_world.tsv").read_text(encoding="utf-8")
    teams_text = (cache / "elo_teams.tsv").read_text(encoding="utf-8")
    if not parse_elo_ratings(world_text):
        raise ValueError("refusing to freeze baseline: elo_world.tsv parsed 0 rows")
    if not parse_elo_team_aliases(teams_text):
        raise ValueError("refusing to freeze baseline: elo_teams.tsv parsed 0 aliases")
    (cache / BASELINE_WORLD).write_text(world_text, encoding="utf-8")
    (cache / BASELINE_TEAMS).write_text(teams_text, encoding="utf-8")
    meta = {"baseline_at": baseline_at}
    (cache / BASELINE_META).write_text(json.dumps(meta), encoding="utf-8")
    return meta


def has_baseline(cache_dir: str | Path) -> bool:
    cache = Path(cache_dir)
    return all(
        (cache / name).exists() for name in (BASELINE_WORLD, BASELINE_TEAMS, BASELINE_META)
    )


def load_baseline(cache_dir: str | Path) -> tuple[dict[str, EloRating], dict[str, str], str]:
    cache = Path(cache_dir)
    if not has_baseline(cache):
        raise FileNotFoundError(f"elo baseline missing in {cache}")
    ratings = parse_elo_ratings((cache / BASELINE_WORLD).read_text(encoding="utf-8"))
    aliases = parse_elo_team_aliases((cache / BASELINE_TEAMS).read_text(encoding="utf-8"))
    meta = json.loads((cache / BASELINE_META).read_text(encoding="utf-8"))
    return ratings, aliases, str(meta["baseline_at"])


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def compute_updated_world_tsv(cache_dir: str | Path, min_rows: int | None = None) -> str:
    cache = Path(cache_dir)
    baseline, aliases, baseline_at = load_baseline(cache)
    if min_rows is None:
        min_rows = len(baseline)
    cutoff = _parse_at(baseline_at)
    code_by_canonical = {canonicalize_team(name): code for name, code in aliases.items()}

    current: dict[str, float] = {code: float(r.rating) for code, r in baseline.items()}
    results = parse_openfootball_results(
        json.loads((cache / "openfootball_2026.json").read_text(encoding="utf-8"))
    )
    for result in sorted(results, key=lambda r: r.kickoff_at_utc):
        if result.kickoff_at_utc.astimezone(timezone.utc) < cutoff:
            continue
        home_key = result.home_canonical or canonicalize_team(result.home_team_name)
        away_key = result.away_canonical or canonicalize_team(result.away_team_name)
        home_code = code_by_canonical.get(home_key)
        away_code = code_by_canonical.get(away_key)
        if home_code not in current or away_code not in current:
            continue
        new_home, new_away = update_pair(
            current[home_code],
            current[away_code],
            result.home_score,
            result.away_score,
            k=WORLD_CUP_K,
            neutral=True,
        )
        current[home_code] = new_home
        current[away_code] = new_away

    ordered = sorted(current.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        f"{rank}\t{rank}\t{code}\t{round(rating)}"
        for rank, (code, rating) in enumerate(ordered, start=1)
    ]
    out = "\n".join(lines) + "\n"
    if len(parse_elo_ratings(out)) < min_rows:
        raise ValueError(
            f"computed elo has {len(parse_elo_ratings(out))} rows, expected >= {min_rows}"
        )
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿（新增 5 个测试全 PASS）。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/elo_local.py tests/test_elo_local.py
git commit -m "feat: add locally replayed elo from frozen baseline"
```

---

### Task 2: `elo_local` CLI（--freeze / --check）

**Files:**
- Modify: `worldcup/elo_local.py`（追加 `main`）
- Test: `tests/test_elo_local.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_elo_local.py` 追加：

```python
def test_cli_check_reports_baseline_and_pending_results(capsys=None):
    from worldcup.elo_local import main

    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        code = main(["--cache-dir", str(cache), "--check"])

        assert code == 0


def test_cli_freeze_writes_baseline_files():
    from worldcup.elo_local import main

    with TemporaryDirectory() as tmp:
        cache = Path(tmp)
        _seed_cache(cache)

        code = main(["--cache-dir", str(cache), "--freeze", "--baseline-at", "2026-06-01T00:00:00+00:00"])

        assert code == 0
        ratings, _aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1875
        assert baseline_at == "2026-06-01T00:00:00+00:00"
```

- [ ] **Step 2: 运行确认失败**

Expected: `FAIL ...::test_cli_freeze_writes_baseline_files`（`main` 不存在）。

- [ ] **Step 3: 实现 CLI**

`worldcup/elo_local.py` 末尾追加：

```python
def _default_baseline_at(cache: Path) -> str:
    mtime = (cache / "elo_world.tsv").stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Freeze or inspect the local Elo baseline.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--freeze", action="store_true", help="Freeze current cache as baseline.")
    parser.add_argument("--baseline-at", default=None, help="ISO time; default: elo_world.tsv mtime.")
    parser.add_argument("--check", action="store_true", help="Report baseline and pending results.")
    args = parser.parse_args(argv)

    cache = Path(args.cache_dir)
    if args.freeze:
        baseline_at = args.baseline_at or _default_baseline_at(cache)
        meta = freeze_baseline(cache, baseline_at=baseline_at)
        print(json.dumps({"frozen": True, **meta}, ensure_ascii=False))
        return 0
    if args.check:
        ratings, _aliases, baseline_at = load_baseline(cache)
        out = compute_updated_world_tsv(cache)
        print(
            json.dumps(
                {
                    "baseline_at": baseline_at,
                    "baseline_teams": len(ratings),
                    "computed_rows": len(parse_elo_ratings(out)),
                },
                ensure_ascii=False,
            )
        )
        return 0
    parser.error("pass --freeze or --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

Expected: 全绿。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/elo_local.py tests/test_elo_local.py
git commit -m "feat: add elo baseline freeze/check cli"
```

---

### Task 3: `refresh_runner` 接入与宽限期退役

**Files:**
- Modify: `worldcup/refresh_runner.py`（Elo 抓取块 ~L108-117 及其后）
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: 改写/新增失败测试**

1a. 删除 `test_refresh_keeps_elo_fresh_within_grace_window` 和 `test_refresh_marks_elo_stale_beyond_grace_window`（宽限期机制退役；`_elo_grace_fixture` 改名 `_elo_cache_fixture` 保留复用）。

1b. 在原位置新增三个测试：

```python
def test_refresh_elo_fetch_failure_records_error_without_stale():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("invalid Elo ratings TSV: parsed 0 rows")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        assert result.snapshot["data_quality"]["source_errors"][0]["source"] == "eloratings"
        assert result.snapshot["data_quality"]["stale_sources"] == []
        assert result.snapshot["run"]["stale_sources"] == []
        home_signal = next(
            signal
            for signal in result.snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert "unconfirmed_backup" not in home_signal["reasons"]


def test_refresh_applies_finished_results_to_local_elo():
    from worldcup.elo_local import freeze_baseline

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        # 给 openfootball 缓存补一个已完赛比分（墨西哥 2-0）
        fixture_data = json.loads((cache / "openfootball_2026.json").read_text())
        fixture_data["matches"][0]["score1"] = 2
        fixture_data["matches"][0]["score2"] = 0
        openfootball_body = json.dumps(fixture_data)
        (cache / "openfootball_2026.json").write_text(openfootball_body)
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("blocked")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        # 完赛后主队 Elo 上调、客队下调（基线 1875/1700，K=60 中立）
        assert result.snapshot["matches"][0]["elo"]["home"] > 1875
        assert result.snapshot["matches"][0]["elo"]["away"] < 1700
        elo_quality = result.snapshot["data_quality"]["elo"]
        assert elo_quality["mode"] == "local_replay"
        assert elo_quality["results_applied"] == 1


def test_refresh_elo_fetch_success_reanchors_baseline():
    from worldcup.elo_local import load_baseline

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1880\n2\t2\tZA\t1695\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=elo_transport,
            observed_at="2026-06-14T00:00:00+00:00",
            history_dir=root / "history",
        )

        ratings, _aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1880
        assert baseline_at == "2026-06-14T00:00:00+00:00"
        assert result.snapshot["data_quality"]["source_errors"] == []
```

注意：`_elo_cache_fixture`（原 `_elo_grace_fixture`）已包含 `min_books` 满足的 odds 缓存与 Elo 缓存文件；第一个测试无基线 → 走"自动冻结"路径（用缓存 mtime，为当前时间，晚于 2026-06-11 的 kickoff → 不应用任何赛果，行为与旧缓存兜底一致）。

1c. 既有 `test_refresh_cache_and_build_snapshot_with_injected_transports` 的 `stale_sources == []` 等断言不变；不需要改。

- [ ] **Step 2: 运行确认失败**

Expected: 新增三个测试 FAIL（`stale_sources` 仍含 eloratings / `data_quality.elo` 不存在 / 基线文件不存在）。

- [ ] **Step 3: 实现 refresh_runner 接入**

3a. import 区把 `ELO_CACHE_GRACE_SECONDS` / `_cache_age_seconds` 删除（连同其使用），新增：

```python
from worldcup.elo_local import (
    compute_updated_world_tsv,
    freeze_baseline,
    has_baseline,
)
```

3b. 将原 Elo 抓取块（`try: fetch_elo_files(...) except ...` 含宽限期逻辑）替换为：

```python
    elo_quality: dict = {"mode": "local_replay"}
    try:
        fetch_elo_files(cache_dir=cache, transport=elo_transport)
        freeze_baseline(cache, baseline_at=observed)
        elo_quality["reanchored"] = True
    except Exception as exc:
        if not (elo_world_cache.exists() and elo_teams_cache.exists()):
            raise
        source_errors.append({"source": "eloratings", "error": f"{type(exc).__name__}: {exc}"})

    if not has_baseline(cache):
        baseline_at = datetime.fromtimestamp(
            elo_world_cache.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        freeze_baseline(cache, baseline_at=baseline_at)

    try:
        computed = compute_updated_world_tsv(cache)
        elo_world_cache.write_text(computed, encoding="utf-8")
        results_applied = _count_results_applied(cache)
        elo_quality["results_applied"] = results_applied
    except Exception as exc:
        # 计算失败时保留现有 elo_world.tsv 继续使用，不阻断刷新
        source_errors.append({"source": "elo_local", "error": f"{type(exc).__name__}: {exc}"})
        elo_quality["mode"] = "cache_passthrough"
```

3c. 新增模块级辅助 `_count_results_applied`（与 compute 同口径统计基线后完赛场数，用于 data_quality）：

```python
def _count_results_applied(cache: Path) -> int:
    import json as _json

    from worldcup.collectors.openfootball import parse_openfootball_results
    from worldcup.elo_local import load_baseline

    _ratings, _aliases, baseline_at = load_baseline(cache)
    cutoff = datetime.fromisoformat(baseline_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    results = parse_openfootball_results(
        _json.loads((cache / "openfootball_2026.json").read_text(encoding="utf-8"))
    )
    return sum(1 for r in results if r.kickoff_at_utc.astimezone(timezone.utc) >= cutoff)
```

（如实现时发现与 `compute_updated_world_tsv` 重复读取明显别扭，可改为让 compute 返回 `(tsv, applied_count)` 并同步调整 Task 1 测试——二选一，保持简单优先。）

3d. 在 `snapshot.setdefault("data_quality", {})["stale_sources"] = stale_sources` 之后追加：

```python
    snapshot.setdefault("data_quality", {})["elo"] = elo_quality
```

注意：`compute_updated_world_tsv` 必须在 `build_snapshot_from_cache(cache, ...)` **之前**完成并覆写 `elo_world.tsv`，snapshot 才会用更新后的 Elo。

- [ ] **Step 4: 运行确认通过**

Expected: 全绿（净增 1 个测试：删 2 加 3）。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/refresh_runner.py tests/test_refresh_runner.py
git commit -m "feat: source elo from local baseline replay"
```

---

### Task 4: 真实冻结基线 + 离线 smoke + 文档同步

**Files:**
- Modify: `CLAUDE.md`、`AGENTS.md`、`README.md`、`RECENT_WORK.md`

- [ ] **Step 1: 用真实缓存冻结首个基线（离线、一次性）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.elo_local --freeze
python3 -m worldcup.elo_local --check
```

Expected: freeze 输出 `{"frozen": true, "baseline_at": "<elo_world.tsv 的 mtime>"}`；check 输出 `baseline_teams` 约 244、`computed_rows` 与之一致（赛前无完赛，computed == baseline）。

- [ ] **Step 2: 离线 smoke 验证全链路**

```bash
python3 -m worldcup.local_runner --input-dir data/cache --out data/local/backtest/elo_smoke_snapshot.json
python3 - <<'EOF'
import json
snap = json.load(open("data/local/backtest/elo_smoke_snapshot.json"))
print({"matches": len(snap["matches"]), "sample_elo": snap["matches"][0]["elo"]})
EOF
```

Expected: 72 场、Elo 数值与现状一致（基线后零完赛）。不触发 live refresh。

- [ ] **Step 3: 文档同步**

3a. `CLAUDE.md` 开发规则中两条 Elo 相关规则（"source refresh 失败但本地缓存存在时…"的例外条款，即提到 `ELO_CACHE_GRACE_SECONDS` 的整条）替换为：

> - Elo 来源为本地基线重放：`data/cache/elo_baseline_*.tsv` + openfootball 完赛比分按 eloratings 公式（K=60、中立场）增量重放生成 `elo_world.tsv`；eloratings 抓取仅用于重新锚定基线，抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不降级信号。重放计算失败时回退沿用现有 `elo_world.tsv` 并记 `elo_local` 错误。常量与实现见 `worldcup/elo_local.py`。

3b. `AGENTS.md` 同步同一条规则（该项目两文件要求保持同步）。

3c. `README.md`：数据源/降级说明处同步上述行为；运维命令加一行 `python3 -m worldcup.elo_local --check`（只读检查基线与待应用赛果数）。

3d. `RECENT_WORK.md` 顶部按既有格式追加"2026-06-11 Elo 改为本地基线重放"一节：背景（WAF 断供 + 宽限期 6-13 到期）、口径（K=60/中立/幂等全量重算/行数不少于基线护栏）、重新锚定语义、宽限期退役、smoke 结果、未 push/未部署/未触发 live refresh。

- [ ] **Step 4: 最终全量验证**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: 全绿；无空白错误。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add CLAUDE.md AGENTS.md README.md RECENT_WORK.md
git commit -m "docs: record local elo baseline replay"
```

---

## 范围外（明确不做）

- 不上无头浏览器、不绕 eloratings 的 JS 挑战。
- 不从历史全量重放（仅基线 + 本届增量）。
- 不做东道主非中立 Elo 修正（已知美墨加少量漂移，官方源恢复重锚定即校正）。
- 不做官方/自算偏差对账报表（后续官方源恢复后另议）。
- 不改模型参数、不动 EV/Edge 阈值。
- 不部署、不 push（由用户单独确认）。
