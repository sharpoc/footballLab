# Elo Replay + 真实历史回测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用已探测确认的 `martj42/international_results` 历史数据集（本地样例 `data/probe/intl_results_martj42.csv`）建立真实回测证据链：实现 eloratings 风格的 Elo replay 推演赛前评分、把历史结果转换成回测 CSV 契约、跑出首份真实回测基线报告，并完成 `dc_rho` 参数扫描，为模型参数调优提供证据。

**Architecture:** 新增两个离线模块：`worldcup/elo_replay.py`（纯函数 Elo 公式 + 按时间顺序重放全部历史得到每场赛前评分，并提供与官方 eloratings 榜单的对照 CLI）和 `worldcup/backtest_data.py`（过滤 + 转换成 `worldcup.backtest` 的 CSV 契约）。`worldcup/backtest.py` 增加 `--sweep` 一键参数扫描。所有模块只读本地文件、不联网、不进线上 pipeline。

**Tech Stack:** 纯标准库 Python，无新依赖；测试用 `tests/run_tests.py` 纯函数断言风格。

---

## 背景与约束

- 数据源已探测并经用户确认：`data/probe/intl_results_martj42.csv`（49,450 行，1872 → 2026-06-08，列：`date,home_team,away_team,home_score,away_score,tournament,city,country,neutral`）。注意其中 72 行是预录的 2026 世界杯赛程，比分为字符串 `NA`，解析时必须跳过。
- 该文件在被 gitignore 的 `data/probe/`，**单元测试一律用内联小样例**；只有 smoke 测试可以读它，且文件缺失时直接 return 跳过（沿用项目既有 probe smoke 模式）。
- 历史赛前 Elo 没有免费现成源，方案是按 eloratings.net 公开公式自己重放：
  - 期望胜率 `We = 1 / (10^(-dr/400) + 1)`，`dr` 含主场 +100（中立场不加）——直接复用 `worldcup.engine.elo.expected_score`。
  - 评分变化 `ΔR = K × G × (W − We)`，主队 `+Δ`、客队 `−Δ`（零和）。
  - `W`：胜 1、平 0.5、负 0。
  - `G`（净胜球指数）：净胜 0/1 球 → 1.0；2 球 → 1.5；N≥3 球 → `(11+N)/8`。
  - `K` 按赛事：世界杯决赛圈 60；洲际锦标赛决赛圈/重大洲际杯 50；世预赛/洲际预选/Nations League 40；其他正式赛事 30；友谊赛 20。
  - 所有队伍初始评分 1500，从 1872 年重放到今天，评分自然收敛。
- replay 结果与官方榜单（`data/probe/elo_world.tsv`）的对照**只做 CLI 报告供人工审阅，不做硬测试断言**（两边历史细节有差异，硬阈值会让自动执行卡死）；smoke 测试只断言能跑通和规模合理。
- **本计划不修改 `config/settings.yaml` 的任何参数值**。`dc_rho` 扫描只产出证据报告，是否启用非零 rho 由用户看报告后单独决定。
- 回测报告与研究文档不得包含资金建议；数值必须如实粘贴命令输出，不允许编造或美化。
- 验证命令（无 pytest 环境，一次跑全部测试）：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- 本项目允许本地 commit，不允许 push。基于最新 `main` 新建分支执行。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `worldcup/elo_replay.py` | 新建 | Elo 公式（K/G/单场更新）、数据集解析、按时间重放、官方榜对照 CLI |
| `worldcup/backtest_data.py` | 新建 | 历史结果 → 回测 CSV 契约的过滤/转换 + CLI |
| `worldcup/backtest.py` | 修改 | 新增 `--sweep SECTION.KEY=V1,V2,...` 参数扫描 |
| `tests/test_elo_replay.py` | 新建 | 公式单测 + 重放顺序 + 解析 + probe smoke |
| `tests/test_backtest_data.py` | 新建 | 转换过滤 + 与 `load_matches` 的 roundtrip |
| `tests/test_backtest.py` | 修改 | 新增 sweep CLI 测试 |
| `docs/research/2026-06-10-intl-backtest-baseline.md` | 新建 | 首份真实回测证据报告 |
| `README.md`、`RECENT_WORK.md` | 修改 | 文档同步 |

---

### Task 1: Elo 公式核心（K 因子、净胜球指数、单场更新）

**Files:**
- Create: `worldcup/elo_replay.py`
- Create: `tests/test_elo_replay.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_elo_replay.py`：

```python
import math

from worldcup.elo_replay import goal_index, k_factor, update_pair


def test_k_factor_by_tournament_class():
    assert k_factor("FIFA World Cup") == 60.0
    assert k_factor("Copa América") == 50.0
    assert k_factor("UEFA Euro") == 50.0
    assert k_factor("FIFA World Cup qualification") == 40.0
    assert k_factor("UEFA Euro qualification") == 40.0
    assert k_factor("UEFA Nations League") == 40.0
    assert k_factor("CONCACAF Nations League") == 40.0
    assert k_factor("Friendly") == 20.0
    assert k_factor("King's Cup") == 30.0


def test_goal_index_margins():
    assert goal_index(0) == 1.0
    assert goal_index(1) == 1.0
    assert goal_index(-1) == 1.0
    assert goal_index(2) == 1.5
    assert goal_index(-2) == 1.5
    assert math.isclose(goal_index(3), (11 + 3) / 8)
    assert math.isclose(goal_index(-5), (11 + 5) / 8)


def test_update_pair_friendly_home_win_uses_home_advantage():
    from worldcup.engine.elo import expected_score

    rh, ra = update_pair(1500.0, 1500.0, 1, 0, k=20.0, neutral=False)
    delta = 20.0 * 1.0 * (1.0 - expected_score(100.0))
    assert math.isclose(rh, 1500.0 + delta)
    assert math.isclose(ra, 1500.0 - delta)


def test_update_pair_neutral_draw_between_equals_is_noop():
    rh, ra = update_pair(1600.0, 1600.0, 1, 1, k=40.0, neutral=True)
    assert math.isclose(rh, 1600.0)
    assert math.isclose(ra, 1600.0)


def test_update_pair_is_zero_sum():
    rh, ra = update_pair(1700.0, 1450.0, 0, 3, k=50.0, neutral=True)
    assert math.isclose((rh + ra), 1700.0 + 1450.0)
    assert rh < 1700.0 and ra > 1450.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `tests/test_elo_replay.py` 加载失败（`No module named 'worldcup.elo_replay'`）。

- [ ] **Step 3: 实现**

新建 `worldcup/elo_replay.py`：

```python
"""Replay eloratings-style ratings from historical international results.

纯离线：只读本地 CSV，不联网，不参与线上 pipeline。评分公式按
eloratings.net 公开规则实现，用于回测所需的赛前 Elo 推演。
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from worldcup.engine.elo import expected_score

DEFAULT_INITIAL_RATING = 1500.0
DEFAULT_HOME_ADV = 100.0

_FINALS_60 = {"FIFA World Cup"}
_MAJOR_50 = {
    "Copa América",
    "Copa America",
    "UEFA Euro",
    "African Cup of Nations",
    "AFC Asian Cup",
    "CONCACAF Championship",
    "Gold Cup",
    "Oceania Nations Cup",
    "Confederations Cup",
}
_LEAGUE_40 = {"UEFA Nations League", "CONCACAF Nations League"}


def k_factor(tournament: str) -> float:
    if tournament in _FINALS_60:
        return 60.0
    if tournament in _MAJOR_50:
        return 50.0
    if tournament in _LEAGUE_40 or "qualification" in tournament.lower():
        return 40.0
    if tournament == "Friendly":
        return 20.0
    return 30.0


def goal_index(margin: int) -> float:
    m = abs(margin)
    if m <= 1:
        return 1.0
    if m == 2:
        return 1.5
    return (11.0 + m) / 8.0


def update_pair(
    rating_home: float,
    rating_away: float,
    home_score: int,
    away_score: int,
    k: float,
    neutral: bool,
    home_adv: float = DEFAULT_HOME_ADV,
) -> tuple[float, float]:
    dr = rating_home - rating_away + (0.0 if neutral else home_adv)
    we = expected_score(dr)
    if home_score > away_score:
        w = 1.0
    elif home_score == away_score:
        w = 0.5
    else:
        w = 0.0
    delta = k * goal_index(home_score - away_score) * (w - we)
    return rating_home + delta, rating_away - delta
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/elo_replay.py tests/test_elo_replay.py
git commit -m "feat: add eloratings-style rating formulas"
```

### Task 2: 数据集解析与按时间重放

**Files:**
- Modify: `worldcup/elo_replay.py`
- Test: `tests/test_elo_replay.py`

- [ ] **Step 1: 写失败测试**

`tests/test_elo_replay.py` 追加：

```python
def test_load_results_skips_na_scores_and_parses_neutral():
    import tempfile

    from worldcup.elo_replay import load_results

    content = (
        "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
        "2024-01-01,Alpha,Beta,2,1,Friendly,Town,Alpha,FALSE\n"
        "2026-06-11,Alpha,Beta,NA,NA,FIFA World Cup,Town,Gamma,TRUE\n"
        "2024-02-01,Alpha,Gamma,0,0,FIFA World Cup qualification,Town,Gamma,TRUE\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(content)
        path = fh.name
    matches = load_results(path)
    assert len(matches) == 2
    assert matches[0].home_team == "Alpha" and matches[0].home_score == 2
    assert matches[0].neutral is False
    assert matches[1].neutral is True


def test_replay_orders_by_date_and_tracks_pre_match_ratings():
    import math

    from worldcup.elo_replay import ReplayMatch, replay, update_pair

    later = ReplayMatch("2024-02-01", "Alpha", "Beta", 1, 1, "Friendly", True)
    earlier = ReplayMatch("2024-01-01", "Alpha", "Beta", 3, 0, "Friendly", True)
    replayed, ratings = replay([later, earlier])

    first_match, rh1, ra1 = replayed[0]
    assert first_match.date == "2024-01-01"
    assert rh1 == 1500.0 and ra1 == 1500.0

    expected_rh, expected_ra = update_pair(1500.0, 1500.0, 3, 0, k=20.0, neutral=True)
    second_match, rh2, ra2 = replayed[1]
    assert second_match.date == "2024-02-01"
    assert math.isclose(rh2, expected_rh)
    assert math.isclose(ra2, expected_ra)
    assert set(ratings) == {"Alpha", "Beta"}


def test_replay_smoke_on_probe_dataset():
    from pathlib import Path as _Path

    probe = _Path(__file__).resolve().parent.parent / "data" / "probe" / "intl_results_martj42.csv"
    if not probe.exists():
        return
    from worldcup.elo_replay import load_results, replay

    matches = load_results(probe)
    assert len(matches) > 40000
    replayed, ratings = replay(matches)
    assert len(replayed) == len(matches)
    assert len(ratings) > 250
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`load_results` / `ReplayMatch` / `replay` 不存在）。

- [ ] **Step 3: 实现**

`worldcup/elo_replay.py` 追加：

```python
@dataclass(frozen=True)
class ReplayMatch:
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool


def load_results(path: str | Path) -> list[ReplayMatch]:
    out: list[ReplayMatch] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            home_score = (row.get("home_score") or "").strip()
            away_score = (row.get("away_score") or "").strip()
            if not home_score.isdigit() or not away_score.isdigit():
                continue
            out.append(
                ReplayMatch(
                    date=row["date"].strip(),
                    home_team=row["home_team"].strip(),
                    away_team=row["away_team"].strip(),
                    home_score=int(home_score),
                    away_score=int(away_score),
                    tournament=(row.get("tournament") or "").strip(),
                    neutral=(row.get("neutral") or "").strip().upper() == "TRUE",
                )
            )
    return out


def replay(
    matches: list[ReplayMatch],
    initial: float = DEFAULT_INITIAL_RATING,
    home_adv: float = DEFAULT_HOME_ADV,
) -> tuple[list[tuple[ReplayMatch, float, float]], dict[str, float]]:
    """按日期顺序重放历史，返回每场赛前评分和最终评分表。"""
    ratings: dict[str, float] = {}
    replayed: list[tuple[ReplayMatch, float, float]] = []
    for match in sorted(matches, key=lambda m: m.date):
        rating_home = ratings.get(match.home_team, initial)
        rating_away = ratings.get(match.away_team, initial)
        replayed.append((match, rating_home, rating_away))
        new_home, new_away = update_pair(
            rating_home,
            rating_away,
            match.home_score,
            match.away_score,
            k=k_factor(match.tournament),
            neutral=match.neutral,
            home_adv=home_adv,
        )
        ratings[match.home_team] = new_home
        ratings[match.away_team] = new_away
    return replayed, ratings
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（probe smoke 在本机应实际执行而非跳过）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/elo_replay.py tests/test_elo_replay.py
git commit -m "feat: replay historical results into pre-match ratings"
```

### Task 3: 官方榜对照 CLI（人工审阅用，不做硬断言）

**Files:**
- Modify: `worldcup/elo_replay.py`

- [ ] **Step 1: 实现 CLI**

`worldcup/elo_replay.py` 末尾追加（对照逻辑复用 collectors 的解析与 alias 规范化）：

```python
def main(argv: list[str] | None = None) -> int:
    from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
    from worldcup.collectors.team_aliases import canonicalize_team

    parser = argparse.ArgumentParser(description="Replay ratings and compare with official eloratings")
    parser.add_argument("--source", default="data/probe/intl_results_martj42.csv")
    parser.add_argument("--elo", default="data/probe/elo_world.tsv")
    parser.add_argument("--aliases", default="data/probe/elo_teams.tsv")
    parser.add_argument("--top", type=int, default=10, help="official top-N to check")
    parser.add_argument("--pool", type=int, default=30, help="replayed top-M pool for overlap")
    args = parser.parse_args(argv)

    replayed, ratings = replay(load_results(args.source))
    official = parse_elo_ratings(Path(args.elo).read_text(encoding="utf-8"))
    aliases = parse_elo_team_aliases(Path(args.aliases).read_text(encoding="utf-8"))
    code_by_canonical = {canonicalize_team(name): code for name, code in aliases.items()}

    replayed_by_code: dict[str, float] = {}
    for team, rating in ratings.items():
        code = code_by_canonical.get(canonicalize_team(team))
        if code is not None:
            replayed_by_code[code] = max(rating, replayed_by_code.get(code, rating))
    replay_rank = {
        code: idx + 1
        for idx, (code, _) in enumerate(
            sorted(replayed_by_code.items(), key=lambda item: -item[1])
        )
    }

    top_official = sorted(official.values(), key=lambda r: r.rank)[: args.top]
    lines = []
    hits = 0
    for entry in top_official:
        rank = replay_rank.get(entry.code)
        if rank is not None and rank <= args.pool:
            hits += 1
        lines.append(
            {
                "code": entry.code,
                "official_rank": entry.rank,
                "official_rating": entry.rating,
                "replay_rank": rank,
                "replay_rating": round(replayed_by_code.get(entry.code, 0.0), 1),
            }
        )
    print(
        json.dumps(
            {
                "matches_replayed": len(replayed),
                "teams_rated": len(ratings),
                "teams_mapped_to_codes": len(replayed_by_code),
                "official_top": args.top,
                "replay_pool": args.pool,
                "overlap_hits": hits,
                "detail": lines,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 运行 CLI 并人工记录结果**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.elo_replay`
Expected: 输出 JSON 对照表。把 `overlap_hits / official_top` 和 detail 表原样保存（Task 7 写进研究文档）。合理预期是官方 top-10 大部分落在 replay top-30 内；**如果 overlap 很低（如 <5/10），停下来在 RECENT_WORK 记录现象，不要继续后续任务**，等用户判断。

- [ ] **Step 3: 全量测试 + Commit**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

```bash
git add worldcup/elo_replay.py
git commit -m "feat: add replay vs official ratings comparison cli"
```

### Task 4: 历史结果 → 回测 CSV 转换器

**Files:**
- Create: `worldcup/backtest_data.py`
- Create: `tests/test_backtest_data.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_backtest_data.py`：

```python
import tempfile
from pathlib import Path

from worldcup.backtest_data import convert, known_canonicals, write_csv
from worldcup.elo_replay import ReplayMatch

ALIASES_TSV = "AA\tAlpha\nBB\tBeta\nCC\tGamma\n"


def _sample_matches() -> list[ReplayMatch]:
    return [
        ReplayMatch("2009-05-01", "Alpha", "Beta", 1, 0, "Friendly", False),
        ReplayMatch("2024-01-01", "Alpha", "Beta", 2, 1, "Friendly", False),
        ReplayMatch("2024-02-01", "Alpha", "Unknownia", 5, 0, "Friendly", True),
        ReplayMatch("2024-03-01", "Beta", "Gamma", 0, 0, "FIFA World Cup qualification", True),
    ]


def test_convert_filters_by_date_and_known_teams():
    known = known_canonicals(ALIASES_TSV)
    rows = convert(_sample_matches(), known, since="2010-01-01")
    assert [r["match_id"] for r in rows] == [
        "2024-01-01_alpha_beta",
        "2024-03-01_beta_gamma",
    ]
    first = rows[0]
    assert first["kickoff_at_utc"] == "2024-01-01T12:00:00Z"
    assert first["neutral"] == 0
    assert rows[1]["neutral"] == 1
    # 2009 年那场虽然被 since 过滤，但仍参与 replay：2024 那场的赛前评分不是初始值
    assert first["home_elo_before"] != 1500.0


def test_write_csv_roundtrips_through_backtest_loader():
    from worldcup.backtest import load_matches

    known = known_canonicals(ALIASES_TSV)
    rows = convert(_sample_matches(), known, since="2010-01-01")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "history.csv"
        write_csv(rows, out)
        loaded = load_matches(out)
    assert len(loaded) == 2
    assert loaded[0].home_team == "Alpha"
    assert loaded[0].odds_1x2 is None and loaded[0].odds_ou is None
    assert loaded[0].neutral is False
    assert loaded[1].neutral is True
    assert loaded[0].home_elo_before > loaded[0].away_elo_before
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `tests/test_backtest_data.py` 加载失败（模块不存在）。

- [ ] **Step 3: 实现**

新建 `worldcup/backtest_data.py`：

```python
"""Convert international results history into the offline backtest CSV contract.

只读本地文件，不联网。输出 CSV 只含比赛结果与 replay 赛前 Elo，
赔率列留空（历史收盘赔率来源未确认前不填）。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from worldcup.collectors.eloratings import parse_elo_team_aliases
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.elo_replay import (
    DEFAULT_HOME_ADV,
    DEFAULT_INITIAL_RATING,
    ReplayMatch,
    load_results,
    replay,
)

OUTPUT_COLUMNS = (
    "match_id",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_elo_before",
    "away_elo_before",
    "neutral",
)


def known_canonicals(alias_text: str) -> set[str]:
    return {canonicalize_team(name) for name in parse_elo_team_aliases(alias_text)}


def _slug(match: ReplayMatch) -> str:
    return f"{match.date}_{match.home_team}_{match.away_team}".replace(" ", "_").lower()


def convert(
    matches: list[ReplayMatch],
    known: set[str],
    since: str,
    initial: float = DEFAULT_INITIAL_RATING,
    home_adv: float = DEFAULT_HOME_ADV,
) -> list[dict]:
    replayed, _ = replay(matches, initial=initial, home_adv=home_adv)
    rows: list[dict] = []
    for match, rating_home, rating_away in replayed:
        if match.date < since:
            continue
        if (
            canonicalize_team(match.home_team) not in known
            or canonicalize_team(match.away_team) not in known
        ):
            continue
        rows.append(
            {
                "match_id": _slug(match),
                "kickoff_at_utc": f"{match.date}T12:00:00Z",
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_score": match.home_score,
                "away_score": match.away_score,
                "home_elo_before": round(rating_home, 1),
                "away_elo_before": round(rating_away, 1),
                "neutral": 1 if match.neutral else 0,
            }
        )
    return rows


def write_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build backtest CSV from historical results")
    parser.add_argument("--source", default="data/probe/intl_results_martj42.csv")
    parser.add_argument("--aliases", default="data/probe/elo_teams.tsv")
    parser.add_argument("--out", default="data/local/backtest/intl_history.csv")
    parser.add_argument("--since", default="2010-01-01")
    args = parser.parse_args(argv)

    matches = load_results(args.source)
    known = known_canonicals(Path(args.aliases).read_text(encoding="utf-8"))
    rows = convert(matches, known, since=args.since)
    write_csv(rows, args.out)
    print(
        json.dumps(
            {"source_rows": len(matches), "output_rows": len(rows), "since": args.since, "out": args.out},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest_data.py tests/test_backtest_data.py
git commit -m "feat: convert historical results into backtest csv"
```

### Task 5: 回测 CLI 参数扫描 `--sweep`

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_cli_sweep_writes_variant_reports():
    import tempfile

    from worldcup.backtest import main

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.json"
        code = main(
            [
                "--csv",
                str(SAMPLE_CSV),
                "--out",
                str(out_path),
                "--min-sample",
                "5",
                "--sweep",
                "poisson.dc_rho=0,-0.1",
            ]
        )
        assert code == 0
        assert (Path(tmp) / "report.poisson.dc_rho.0.json").exists()
        assert (Path(tmp) / "report.poisson.dc_rho.-0.1.json").exists()


def test_cli_sweep_invalid_format_exits_nonzero():
    from worldcup.backtest import main

    try:
        main(["--csv", str(SAMPLE_CSV), "--sweep", "poisson.dc_rho"])
    except SystemExit as exc:
        assert exc.code not in (0, None)
    else:
        raise AssertionError("expected SystemExit")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 第一个新测试 FAIL（CLI 不认识 `--sweep`，argparse 直接 SystemExit(2)）；第二个可能误 PASS（同样因 argparse 报错），实现后两个都必须真 PASS。

- [ ] **Step 3: 实现**

`worldcup/backtest.py` 的 `main` 中，在 `--set` 参数定义之后追加：

```python
    parser.add_argument(
        "--sweep",
        default=None,
        metavar="SECTION.KEY=V1,V2,...",
        help="run once per value and write report.<key>.<value>.json variants",
    )
```

在 `cfg = apply_overrides(load_config(args.config), args.overrides)` 之后、单次 `run_backtest` 之前插入：

```python
    if args.sweep:
        key, sep, raw_values = args.sweep.partition("=")
        if not sep or not key.strip() or not raw_values.strip():
            raise SystemExit(f"invalid --sweep (expect section.key=v1,v2): {args.sweep!r}")
        matches = load_matches(args.csv)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary = []
        for raw in raw_values.split(","):
            value = raw.strip()
            cfg_variant = apply_overrides(cfg, [f"{key}={value}"])
            report = run_backtest(matches, cfg_variant, min_sample=args.min_sample)
            variant_path = out.with_name(f"{out.stem}.{key}.{value}{out.suffix}")
            variant_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            summary.append(
                {
                    "value": value,
                    "1x2_model": report["markets"]["1x2"]["model"],
                    "ou_model": report["markets"]["ou_2_5"]["model"],
                }
            )
        print(json.dumps({"sweep": key, "results": summary}, indent=2, ensure_ascii=False))
        return 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add parameter sweep to backtest cli"
```

### Task 6: 生成真实历史数据并跑首份回测

**Files:**
- 产物均写入被忽略的 `data/local/backtest/`，不进 git。

- [ ] **Step 1: 生成回测 CSV**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest_data --since 2010-01-01`
Expected: 打印 `output_rows` 约 1.2 万上下（2010 年后且两队都能映射 Elo alias 的比赛）；记录确切数字。

- [ ] **Step 2: 跑基线回测**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv data/local/backtest/intl_history.csv --out data/local/backtest/intl_report.json`
Expected: `sample_too_small: false`。记录 `markets.1x2.model` 与 `uniform` 的 Brier / Log Loss（无赔率数据，`market` 与 `model_matched` 的 n 为 0 属预期）、`calibration_1x2` 全表、`totals_by_abs_dr` 全表。

- [ ] **Step 3: 跑 dc_rho 扫描**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv data/local/backtest/intl_history.csv --out data/local/backtest/intl_report.json --sweep "poisson.dc_rho=0,-0.05,-0.1,-0.15"`
Expected: 输出 4 个取值的对比摘要；记录每个取值的 1X2 与 OU 模型 Brier / Log Loss。

- [ ] **Step 4: 不提交任何参数修改**

确认 `git status` 中 `config/settings.yaml` 无改动（`dc_rho` 保持 `0.0`）。扫描只产出证据。

### Task 7: 研究文档与收尾

**Files:**
- Create: `docs/research/2026-06-10-intl-backtest-baseline.md`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: 写研究报告**

新建 `docs/research/2026-06-10-intl-backtest-baseline.md`，结构如下，数字全部用 Task 3 / Task 6 的真实命令输出填充，禁止编造：

```markdown
# 国际比赛历史回测基线（2026-06-10）

- 数据来源：martj42/international_results 本地样例（`data/probe/intl_results_martj42.csv`，更新至 2026-06-08）
- 赛前 Elo：本仓库 `worldcup.elo_replay` 按 eloratings 公开公式从 1872 年重放推演（非官方历史评分）
- 样本范围：2010-01-01 起、两队均可映射 Elo alias 的国家队比赛，共 N 场（填实际值）
- 局限：无历史收盘赔率，本报告只评估模型概率质量（vs uniform 基线），不评估 EV/Edge 阈值
- 免责：仅用于研究分析，不构成投注建议

## Elo replay 与官方榜对照

（粘贴 `python3 -m worldcup.elo_replay` 输出摘要：overlap_hits / detail 表）

## 1X2 模型质量基线

（model vs uniform 的 Brier / Log Loss；calibration_1x2 表）

## 总进球与 |dr| 的关系（mu 模型证据）

（totals_by_abs_dr 表 + 一句客观描述：mean_total_goals 是否随 |dr| 上升）

## dc_rho 扫描

（4 个取值的 1X2 / OU Brier、Log Loss 对照表）

## 结论候选（待用户决策，不自动改生产配置）

（客观列出：哪个 dc_rho 在 1X2 Log Loss 上最优、幅度多大；calibration 显示的平局/主胜偏差方向）
```

- [ ] **Step 2: README 与 RECENT_WORK**

README「离线回测」小节追加两行：

```markdown
- 历史数据链路：`python3 -m worldcup.backtest_data` 把 `data/probe/` 的国际比赛结果样例（含 `worldcup.elo_replay` 推演的赛前 Elo）转换成回测 CSV；`python3 -m worldcup.elo_replay` 输出 replay 与官方 eloratings 榜单的对照。
- 参数扫描：`--sweep poisson.dc_rho=0,-0.05,-0.1,-0.15` 一次产出多取值对比报告；首份真实回测证据见 `docs/research/2026-06-10-intl-backtest-baseline.md`。
```

RECENT_WORK.md 顶部插入新条目：完成事项（elo replay、转换器、sweep、首份真实回测）、关键数字（样本量、对照 overlap、基线 Brier/LogLoss、dc_rho 最优取值）、验证结果（`tests/run_tests.py` 全绿）、下次继续事项（用户决策 dc_rho 是否启用；赛果回填模块攒自有赔率历史）、风险提示（replay 评分非官方历史值；无赔率样本不能验证 EV 阈值）。

- [ ] **Step 3: 全量验证 + Commit**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

```bash
git add docs/research/2026-06-10-intl-backtest-baseline.md README.md RECENT_WORK.md
git commit -m "docs: record first real-data backtest baseline"
```

---

## 风险与说明

1. **生产行为零变化**：本计划全部是离线工具与证据产出，不改 pipeline、不改 `settings.yaml` 参数、不触发 live refresh、不调用 The Odds API、不部署。
2. **replay 评分是近似**：与官方 eloratings 的历史值存在差异（初始种子、个别赛事 K 分类、停赛处理等），Task 3 的对照报告就是用来量化这个差异的；overlap 异常低时必须停下等用户判断。
3. **无赔率样本的边界**：这批数据只能校准模型概率（dc_rho、平局形状、mu-vs-dr 证据），不能校准 EV/Edge 阈值和 `mu_market_weight`——那要等世界杯期间自有赔率快照积累（赛果回填是下一个计划）。
4. **`dc_rho` 决策权在用户**：报告给出候选结论，启用与否、取值多少由用户看证据后单独确认，届时只需改一行 `settings.yaml` 并重新部署。
