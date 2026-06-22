# Domestic League Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-competition foundation and a local CSL 2026 MVP adapter without breaking the current World Cup research ledger.

**Architecture:** Introduce a small `CompetitionConfig` registry, a The Odds API sports-key probe, club-specific aliases, and a league odds adapter that converts league events into the existing `Fixture` / `ParsedOddsEvent` pipeline contracts. World Cup snapshots gain a backward-compatible `competition` block; CSL snapshots start as local-only, odds-event-backed, and `club_rating_pending`, with strong signals capped until club ratings exist.

**Tech Stack:** Python 3.11 standard library, current `worldcup` package, current `tests/run_tests.py`, saved JSON samples under ignored `data/probe/` or test fixtures, no new runtime dependency.

---

## Scope And Safety

This plan implements Phase 0, Phase 1, and the local-only part of Phase 2 from `docs/superpowers/specs/2026-06-22-domestic-league-adapters-design.md`.

Do not deploy, push, update LaunchAgent, run live odds refresh, publish to ECS, or write online state in this plan. Any command that calls The Odds API with a real key must be separately confirmed by the user.

The current repository may already have unrelated dirty files. Before each task, run `git status --short`, touch only the files listed for that task, and do not revert user changes.

## File Structure

Create or modify these files:

- Create `worldcup/competitions.py`: competition dataclass, registry, public helpers, default competition metadata blocks.
- Create `tests/test_competitions.py`: registry and metadata block tests.
- Create `worldcup/sources/theoddsapi_sports.py`: sports URL builder, cached fetch helper, sports summary parser, CLI dry-run/probe entry.
- Create `tests/sources/test_theoddsapi_sports_source.py`: sports parser and URL tests.
- Create `worldcup/collectors/club_aliases.py`: club alias canonicalization scoped by competition.
- Create `tests/collectors/test_club_aliases.py`: CSL alias and national-team isolation tests.
- Create `worldcup/collectors/league_odds.py`: odds-event-only league fixture and odds parsing.
- Create `tests/collectors/test_league_odds.py`: CSL saved sample parser tests.
- Modify `worldcup/local_runner.py`: add default World Cup `competition` block to existing snapshots, expose a signal cap helper reusable by the league runner.
- Modify `tests/test_local_runner.py`: assert World Cup compatibility and `competition` block.
- Create `worldcup/league_runner.py`: build local league snapshots from cached odds events for a configured competition.
- Create `tests/test_league_runner.py`: CSL local snapshot tests, event-only quality tests, club-rating-pending signal cap tests.
- Modify `worldcup/ledger.py` and `worldcup/ledger_html.py`: render competition labels and competition filter from snapshot data while preserving old snapshots.
- Modify `tests/test_preview.py` and/or `tests/test_ledger.py`: competition label/filter tests.
- Modify `README.md`, `RECENT_WORK.md`: document local-only workflow and recent work.

## Task 1: Competition Registry

**Files:**
- Create: `worldcup/competitions.py`
- Create: `tests/test_competitions.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/test_competitions.py`:

```python
from worldcup.competitions import (
    CompetitionConfig,
    competition_block,
    get_competition,
    list_competitions,
)


def test_registry_contains_worldcup_csl_and_big_five_constraints():
    ids = [item.id for item in list_competitions()]

    assert "fifa_world_cup_2026" in ids
    assert "csl_2026" in ids
    assert "epl_2026_27" in ids
    assert "laliga_2026_27" in ids
    assert "bundesliga_2026_27" in ids
    assert "serie_a_2026_27" in ids
    assert "ligue_1_2026_27" in ids


def test_csl_config_is_domestic_league_and_local_first():
    cfg = get_competition("csl_2026")

    assert isinstance(cfg, CompetitionConfig)
    assert cfg.name == "中超 2026"
    assert cfg.kind == "domestic_league"
    assert cfg.country == "CN"
    assert cfg.season == "2026"
    assert cfg.fixture_policy == "odds_event_window"
    assert cfg.rating_policy == "club_rating_pending"
    assert cfg.window_days == 14
    assert "Chinese Super League" in cfg.theoddsapi_search_terms


def test_competition_block_is_snapshot_safe_and_serializable():
    block = competition_block("fifa_world_cup_2026")

    assert block == {
        "id": "fifa_world_cup_2026",
        "name": "2026 世界杯",
        "kind": "tournament",
        "country": "international",
        "season": "2026",
        "source": "openfootball + theoddsapi",
        "fixture_source": "openfootball",
        "rating_policy": "national_team_elo",
    }
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.competitions'`.

- [ ] **Step 3: Implement `worldcup/competitions.py`**

Create `worldcup/competitions.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CompetitionConfig:
    id: str
    name: str
    kind: str
    country: str
    season: str
    timezone: str = "UTC"
    source: str = "theoddsapi"
    fixture_source: str = "explicit_fixture_source"
    fixture_policy: str = "explicit_fixture_source"
    rating_policy: str = "club_rating_pending"
    refresh_policy: str = "local_daily"
    window_days: int = 14
    theoddsapi_sport_key: str | None = None
    theoddsapi_candidate_keys: tuple[str, ...] = ()
    theoddsapi_search_terms: tuple[str, ...] = ()
    markets: tuple[str, ...] = ("h2h", "spreads", "totals")
    metadata: Mapping[str, str] = field(default_factory=dict)

    def snapshot_block(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "country": self.country,
            "season": self.season,
            "source": self.source,
            "fixture_source": self.fixture_source,
            "rating_policy": self.rating_policy,
        }


_REGISTRY: dict[str, CompetitionConfig] = {
    "fifa_world_cup_2026": CompetitionConfig(
        id="fifa_world_cup_2026",
        name="2026 世界杯",
        kind="tournament",
        country="international",
        season="2026",
        source="openfootball + theoddsapi",
        fixture_source="openfootball",
        fixture_policy="openfootball",
        rating_policy="national_team_elo",
        refresh_policy="worldcup_free_tier",
        window_days=60,
        theoddsapi_sport_key="soccer_fifa_world_cup",
        theoddsapi_candidate_keys=("soccer_fifa_world_cup",),
        theoddsapi_search_terms=("FIFA World Cup", "World Cup"),
    ),
    "csl_2026": CompetitionConfig(
        id="csl_2026",
        name="中超 2026",
        kind="domestic_league",
        country="CN",
        season="2026",
        timezone="Asia/Shanghai",
        source="theoddsapi",
        fixture_source="odds_event_only",
        fixture_policy="odds_event_window",
        rating_policy="club_rating_pending",
        refresh_policy="local_daily",
        window_days=14,
        theoddsapi_sport_key=None,
        theoddsapi_candidate_keys=("soccer_china_superleague", "soccer_china_super_league"),
        theoddsapi_search_terms=("Chinese Super League", "China Super League", "CSL"),
    ),
    "epl_2026_27": CompetitionConfig(
        id="epl_2026_27",
        name="英超 2026/27",
        kind="domestic_league",
        country="GB-ENG",
        season="2026/27",
        timezone="Europe/London",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_epl",),
        theoddsapi_search_terms=("English Premier League", "EPL", "Premier League"),
    ),
    "laliga_2026_27": CompetitionConfig(
        id="laliga_2026_27",
        name="西甲 2026/27",
        kind="domestic_league",
        country="ES",
        season="2026/27",
        timezone="Europe/Madrid",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_spain_la_liga",),
        theoddsapi_search_terms=("La Liga", "Spanish La Liga"),
    ),
    "bundesliga_2026_27": CompetitionConfig(
        id="bundesliga_2026_27",
        name="德甲 2026/27",
        kind="domestic_league",
        country="DE",
        season="2026/27",
        timezone="Europe/Berlin",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_germany_bundesliga",),
        theoddsapi_search_terms=("Bundesliga", "German Bundesliga"),
    ),
    "serie_a_2026_27": CompetitionConfig(
        id="serie_a_2026_27",
        name="意甲 2026/27",
        kind="domestic_league",
        country="IT",
        season="2026/27",
        timezone="Europe/Rome",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_italy_serie_a",),
        theoddsapi_search_terms=("Serie A", "Italian Serie A"),
    ),
    "ligue_1_2026_27": CompetitionConfig(
        id="ligue_1_2026_27",
        name="法甲 2026/27",
        kind="domestic_league",
        country="FR",
        season="2026/27",
        timezone="Europe/Paris",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_france_ligue_one",),
        theoddsapi_search_terms=("Ligue 1", "French Ligue 1"),
    ),
}


def list_competitions() -> list[CompetitionConfig]:
    return list(_REGISTRY.values())


def get_competition(competition_id: str) -> CompetitionConfig:
    try:
        return _REGISTRY[competition_id]
    except KeyError as exc:
        raise KeyError(f"competition_not_configured: {competition_id}") from exc


def competition_block(competition_id: str) -> dict[str, str]:
    return get_competition(competition_id).snapshot_block()
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for `tests/test_competitions.py`; unrelated pre-existing failures must be investigated before continuing.

- [ ] **Step 5: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/competitions.py tests/test_competitions.py
git commit -m "feat: add competition registry"
```

## Task 2: The Odds API Sports-Key Probe

**Files:**
- Create: `worldcup/sources/theoddsapi_sports.py`
- Create: `tests/sources/test_theoddsapi_sports_source.py`

- [ ] **Step 1: Write failing sports probe tests**

Create `tests/sources/test_theoddsapi_sports_source.py`:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.competitions import get_competition
from worldcup.sources.theoddsapi_sports import (
    build_sports_url,
    find_sports_for_competition,
    parse_sports_catalog,
)


def _sports_sample():
    return [
        {
            "key": "soccer_fifa_world_cup",
            "group": "Soccer",
            "title": "FIFA World Cup",
            "description": "FIFA World Cup 2026",
            "active": True,
            "has_outrights": False,
        },
        {
            "key": "soccer_epl",
            "group": "Soccer",
            "title": "EPL",
            "description": "English Premier League",
            "active": True,
            "has_outrights": False,
        },
        {
            "key": "soccer_china_superleague",
            "group": "Soccer",
            "title": "Chinese Super League",
            "description": "China Super League",
            "active": True,
            "has_outrights": False,
        },
    ]


def test_build_sports_url_does_not_log_or_embed_extra_params():
    url = build_sports_url("secret-key", all_sports=True)

    assert url.startswith("https://api.the-odds-api.com/v4/sports/")
    assert "apiKey=secret-key" in url
    assert "all=true" in url


def test_parse_sports_catalog_keeps_safe_summary_fields_only():
    summaries = parse_sports_catalog(_sports_sample())

    csl = next(item for item in summaries if item["key"] == "soccer_china_superleague")
    assert csl == {
        "key": "soccer_china_superleague",
        "group": "Soccer",
        "title": "Chinese Super League",
        "description": "China Super League",
        "active": True,
        "has_outrights": False,
    }


def test_find_sports_for_competition_matches_candidates_and_search_terms():
    summaries = parse_sports_catalog(_sports_sample())

    matches = find_sports_for_competition(summaries, get_competition("csl_2026"))

    assert matches["competition_id"] == "csl_2026"
    assert matches["status"] == "sport_key_found"
    assert matches["matches"][0]["key"] == "soccer_china_superleague"


def test_saved_sports_sample_can_be_written_without_secrets():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "theoddsapi_sports.json"
        path.write_text(json.dumps(_sports_sample()), encoding="utf-8")

        summaries = parse_sports_catalog(json.loads(path.read_text(encoding="utf-8")))

        assert all("apiKey" not in repr(item) for item in summaries)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.sources.theoddsapi_sports'`.

- [ ] **Step 3: Implement `worldcup/sources/theoddsapi_sports.py`**

Create `worldcup/sources/theoddsapi_sports.py`:

```python
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from worldcup.competitions import get_competition, list_competitions
from worldcup.sources.theoddsapi import BASE_URL


@dataclass(frozen=True)
class SportsFetchResult:
    status: int
    json_body: Any
    headers: dict[str, str]
    cache_path: Path | None = None


def build_sports_url(api_key: str, all_sports: bool = True) -> str:
    params = {"apiKey": api_key}
    if all_sports:
        params["all"] = "true"
    return f"{BASE_URL}/sports/?{urlencode(params)}"


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fetch_sports_catalog(
    api_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
) -> SportsFetchResult:
    response = (transport or _default_transport)(build_sports_url(api_key))
    body = response.read()
    json_body = json.loads(body.decode("utf-8"))
    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)
    return SportsFetchResult(
        status=int(getattr(response, "status", 200)),
        json_body=json_body,
        headers=dict(getattr(response, "headers", {})),
        cache_path=written_cache_path,
    )


def parse_sports_catalog(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in raw:
        summaries.append(
            {
                "key": str(item.get("key") or ""),
                "group": str(item.get("group") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "active": bool(item.get("active")),
                "has_outrights": bool(item.get("has_outrights")),
            }
        )
    return summaries


def _matches_term(summary: dict[str, Any], terms: tuple[str, ...]) -> bool:
    haystack = " ".join(
        str(summary.get(key) or "")
        for key in ("key", "title", "description")
    ).lower()
    return any(term.lower() in haystack for term in terms)


def find_sports_for_competition(
    summaries: list[dict[str, Any]],
    competition,
) -> dict[str, Any]:
    matches = [
        item
        for item in summaries
        if item.get("key") in competition.theoddsapi_candidate_keys
        or _matches_term(item, competition.theoddsapi_search_terms)
    ]
    active = [item for item in matches if item.get("active") is True]
    if active:
        status = "sport_key_found"
    elif matches:
        status = "sport_key_inactive"
    else:
        status = "sport_key_missing"
    return {
        "competition_id": competition.id,
        "competition_name": competition.name,
        "status": status,
        "matches": matches,
    }


def build_probe_summary(raw: list[dict[str, Any]], competition_ids: list[str] | None = None) -> dict[str, Any]:
    summaries = parse_sports_catalog(raw)
    competitions = [
        get_competition(competition_id)
        for competition_id in competition_ids
    ] if competition_ids else list_competitions()
    return {
        "status": "ok",
        "sports_count": len(summaries),
        "competitions": [
            find_sports_for_competition(summaries, competition)
            for competition in competitions
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe The Odds API sports catalog from a saved sample.")
    parser.add_argument("--sample", default="data/probe/theoddsapi_sports.json")
    parser.add_argument("--competition", action="append", default=None)
    args = parser.parse_args(argv)
    raw = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    print(json.dumps(build_probe_summary(raw, args.competition), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for sports source tests.

- [ ] **Step 5: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/sources/theoddsapi_sports.py tests/sources/test_theoddsapi_sports_source.py
git commit -m "feat: add odds sports catalog probe"
```

## Task 3: Club Alias Isolation

**Files:**
- Create: `worldcup/collectors/club_aliases.py`
- Create: `tests/collectors/test_club_aliases.py`

- [ ] **Step 1: Write failing club alias tests**

Create `tests/collectors/test_club_aliases.py`:

```python
from worldcup.collectors.club_aliases import canonicalize_club, match_club_alias
from worldcup.collectors.team_aliases import canonicalize_team


def test_csl_club_aliases_are_scoped_to_competition():
    assert canonicalize_club("csl_2026", "Shanghai Port") == "shanghai_port"
    assert canonicalize_club("csl_2026", "Shanghai SIPG") == "shanghai_port"
    assert canonicalize_club("csl_2026", "Beijing Guoan") == "beijing_guoan"
    assert canonicalize_club("csl_2026", "Shandong Taishan") == "shandong_taishan"


def test_club_aliases_do_not_change_national_team_aliases():
    assert canonicalize_team("Shanghai Port") == "shanghai_port"
    assert canonicalize_team("USA") == "united_states"
    assert canonicalize_club("csl_2026", "USA") == "usa"


def test_match_club_alias_reports_unmatched_unknown_clubs():
    result = match_club_alias("csl_2026", "Unknown FC")

    assert result.raw_name == "Unknown FC"
    assert result.canonical_key is None
    assert result.unmatched_name == "Unknown FC"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.collectors.club_aliases'`.

- [ ] **Step 3: Implement club aliases**

Create `worldcup/collectors/club_aliases.py`:

```python
from __future__ import annotations

import re
import unicodedata

from worldcup.collectors.models import TeamAliasResult


_CSL_ALIASES = {
    "shanghai port": "shanghai_port",
    "shanghai sipg": "shanghai_port",
    "shanghai port fc": "shanghai_port",
    "shanghai shenhua": "shanghai_shenhua",
    "shandong taishan": "shandong_taishan",
    "beijing guoan": "beijing_guoan",
    "chengdu rongcheng": "chengdu_rongcheng",
    "zhejiang professional": "zhejiang_professional",
    "henan fc": "henan",
    "tianjin jinmen tiger": "tianjin_jinmen_tiger",
    "wuhan three towns": "wuhan_three_towns",
    "meizhou hakka": "meizhou_hakka",
    "qingdao west coast": "qingdao_west_coast",
    "qingdao hainiu": "qingdao_hainiu",
    "changchun yatai": "changchun_yatai",
    "shenzhen peng city": "shenzhen_peng_city",
    "yunnan yukun": "yunnan_yukun",
    "dalian yingbo": "dalian_yingbo",
}

_KNOWN_BY_COMPETITION = {
    "csl_2026": _CSL_ALIASES,
}


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().replace("&", " and ")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", ascii_value)).strip("_")


def canonicalize_club(competition_id: str, name: str) -> str:
    stripped = name.strip()
    key = stripped.lower()
    aliases = _KNOWN_BY_COMPETITION.get(competition_id, {})
    return aliases.get(key, _slugify(stripped))


def match_club_alias(competition_id: str, name: str) -> TeamAliasResult:
    canonical = canonicalize_club(competition_id, name)
    known = set(_KNOWN_BY_COMPETITION.get(competition_id, {}).values())
    if canonical in known:
        return TeamAliasResult(name, canonical)
    return TeamAliasResult(name, None, name)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for club alias tests.

- [ ] **Step 5: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/collectors/club_aliases.py tests/collectors/test_club_aliases.py
git commit -m "feat: add scoped club aliases"
```

## Task 4: League Odds Event Adapter

**Files:**
- Create: `worldcup/collectors/league_odds.py`
- Create: `tests/collectors/test_league_odds.py`

- [ ] **Step 1: Write failing league odds tests**

Create `tests/collectors/test_league_odds.py`:

```python
from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.models import MarketType


def _csl_odds_sample():
    return [
        {
            "id": "csl-event-1",
            "sport_key": "soccer_china_superleague",
            "commence_time": "2026-07-04T11:35:00Z",
            "home_team": "Shanghai Port",
            "away_team": "Beijing Guoan",
            "bookmakers": [
                {
                    "key": "bk1",
                    "last_update": "2026-07-04T02:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Shanghai Port", "price": 1.9},
                                {"name": "Beijing Guoan", "price": 3.8},
                                {"name": "Draw", "price": 3.4},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Shanghai Port", "price": 1.95, "point": -0.5},
                                {"name": "Beijing Guoan", "price": 1.85, "point": 0.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.91, "point": 2.5},
                                {"name": "Under", "price": 1.89, "point": 2.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def test_parse_league_odds_events_builds_event_only_fixtures_and_quotes():
    result = parse_league_odds_events(_csl_odds_sample(), competition_id="csl_2026")

    assert result.fixture_source == "odds_event_only"
    assert len(result.fixtures) == 1
    assert len(result.odds_events) == 1
    fixture = result.fixtures[0]
    event = result.odds_events[0]
    assert fixture.source_match_no is None
    assert fixture.home_team_name == "Shanghai Port"
    assert fixture.away_team_name == "Beijing Guoan"
    assert fixture.home_canonical == "shanghai_port"
    assert fixture.away_canonical == "beijing_guoan"
    assert fixture.stage is None
    assert event.source_event_id == "csl-event-1"
    assert event.home_canonical == "shanghai_port"
    assert event.away_canonical == "beijing_guoan"
    assert {quote.market_type for quote in event.quotes} == {
        MarketType.X12,
        MarketType.AH,
        MarketType.OU,
    }


def test_parse_league_odds_events_records_unmatched_clubs():
    sample = _csl_odds_sample()
    sample[0]["home_team"] = "Unknown FC"

    result = parse_league_odds_events(sample, competition_id="csl_2026")

    assert result.unmatched_clubs == ["Unknown FC"]
    assert result.fixtures[0].home_canonical == "unknown_fc"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.collectors.league_odds'`.

- [ ] **Step 3: Implement league odds adapter**

Create `worldcup/collectors/league_odds.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.club_aliases import canonicalize_club, match_club_alias
from worldcup.collectors.models import Fixture, InvalidOddsQuote, ParsedOddsEvent
from worldcup.models import MarketType, OddsQuote


_MARKET_TYPES = {
    "h2h": MarketType.X12,
    "spreads": MarketType.AH,
    "totals": MarketType.OU,
}


@dataclass(frozen=True)
class LeagueOddsParseResult:
    fixtures: list[Fixture]
    odds_events: list[ParsedOddsEvent]
    fixture_source: str = "odds_event_only"
    unmatched_clubs: list[str] = field(default_factory=list)


def _parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _selection_for_outcome(market_type: MarketType, outcome_name: str, home_team: str, away_team: str) -> str | None:
    if market_type in (MarketType.X12, MarketType.AH):
        if outcome_name == home_team:
            return "home"
        if outcome_name == away_team:
            return "away"
        if market_type == MarketType.X12 and outcome_name.lower() == "draw":
            return "draw"
        return None
    if market_type == MarketType.OU:
        lowered = outcome_name.lower()
        return lowered if lowered in ("over", "under") else None
    return None


def _quotes_for_event(item: dict[str, Any], home: str, away: str) -> tuple[list[OddsQuote], list[InvalidOddsQuote]]:
    event_id = str(item.get("id", ""))
    commence_time = str(item["commence_time"])
    quotes: list[OddsQuote] = []
    invalid_odds: list[InvalidOddsQuote] = []
    for bookmaker in item.get("bookmakers", []):
        bookmaker_key = str(bookmaker.get("key", "")).strip()
        if not bookmaker_key:
            continue
        bookmaker_updated_at = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", "")).strip()
            market_type = _MARKET_TYPES.get(market_key)
            if market_type is None:
                continue
            fetched_at_raw = market.get("last_update") or bookmaker_updated_at
            fetched_at = _parse_iso_utc(fetched_at_raw) if fetched_at_raw else None
            for outcome in market.get("outcomes", []):
                outcome_name = str(outcome.get("name", "")).strip()
                selection = _selection_for_outcome(market_type, outcome_name, home, away)
                price = outcome.get("price")
                if selection is None or price is None:
                    continue
                line = outcome.get("point") if market_type in (MarketType.AH, MarketType.OU) else None
                if market_type in (MarketType.AH, MarketType.OU) and line is None:
                    continue
                odds_value = float(price)
                line_value = float(line) if line is not None else None
                if odds_value <= 1.0:
                    invalid_odds.append(
                        InvalidOddsQuote(
                            reason="odds_decimal_lte_one",
                            odds=odds_value,
                            bookmaker=bookmaker_key,
                            market=market_key,
                            api_market_key=market_key,
                            market_type=market_type,
                            selection=selection,
                            outcome=outcome_name,
                            line=line_value,
                            match_id=event_id,
                            home_team=home,
                            away_team=away,
                            commence_time=commence_time,
                            last_update=str(fetched_at_raw) if fetched_at_raw else None,
                        )
                    )
                    continue
                quotes.append(
                    OddsQuote(
                        bookmaker=bookmaker_key,
                        market_type=market_type,
                        selection=selection,
                        odds=odds_value,
                        line=line_value,
                        fetched_at=fetched_at,
                    )
                )
    return quotes, invalid_odds


def parse_league_odds_events(raw: list[dict[str, Any]], competition_id: str) -> LeagueOddsParseResult:
    fixtures: list[Fixture] = []
    odds_events: list[ParsedOddsEvent] = []
    unmatched: list[str] = []
    for item in raw:
        kickoff = _parse_iso_utc(str(item["commence_time"]))
        home = str(item.get("home_team", "")).strip()
        away = str(item.get("away_team", "")).strip()
        home_match = match_club_alias(competition_id, home)
        away_match = match_club_alias(competition_id, away)
        if home_match.unmatched_name:
            unmatched.append(home_match.unmatched_name)
        if away_match.unmatched_name:
            unmatched.append(away_match.unmatched_name)
        home_canonical = home_match.canonical_key or canonicalize_club(competition_id, home)
        away_canonical = away_match.canonical_key or canonicalize_club(competition_id, away)
        fixtures.append(
            Fixture(
                source_match_no=None,
                kickoff_at_utc=kickoff,
                kickoff_time_raw=kickoff.isoformat(),
                home_team_name=home,
                away_team_name=away,
                home_canonical=home_canonical,
                away_canonical=away_canonical,
                group=None,
                stage=None,
                venue_name=None,
                has_placeholder_team=False,
            )
        )
        quotes, invalid_odds = _quotes_for_event(item, home, away)
        odds_events.append(
            ParsedOddsEvent(
                source_event_id=str(item.get("id", "")),
                sport_key=str(item.get("sport_key", "")),
                kickoff_at_utc=kickoff,
                home_team_name=home,
                away_team_name=away,
                home_canonical=home_canonical,
                away_canonical=away_canonical,
                quotes=quotes,
                invalid_odds=invalid_odds,
            )
        )
    return LeagueOddsParseResult(
        fixtures=fixtures,
        odds_events=odds_events,
        unmatched_clubs=sorted(set(unmatched)),
    )
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for league odds tests.

- [ ] **Step 5: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/collectors/league_odds.py tests/collectors/test_league_odds.py
git commit -m "feat: parse league odds events"
```

## Task 5: Backward-Compatible World Cup Competition Block

**Files:**
- Modify: `worldcup/local_runner.py`
- Modify: `tests/test_local_runner.py`

- [ ] **Step 1: Write failing local runner tests**

Append to `tests/test_local_runner.py`:

```python
def test_worldcup_snapshot_matches_include_competition_block():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        competition = snapshot["matches"][0]["competition"]
        assert competition["id"] == "fifa_world_cup_2026"
        assert competition["name"] == "2026 世界杯"
        assert competition["kind"] == "tournament"
        assert competition["fixture_source"] == "openfootball"
        assert competition["rating_policy"] == "national_team_elo"
        assert snapshot["matches"][0]["stage"] == "Matchday 1"
        assert "signals" in snapshot["matches"][0]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL with `KeyError: 'competition'`.

- [ ] **Step 3: Modify `worldcup/local_runner.py`**

Add the import near existing imports:

```python
from worldcup.competitions import competition_block
```

Change `_analysis_to_dict()` signature and insert the competition block:

```python
def _analysis_to_dict(
    analysis,
    signals: list[Signal],
    result_index: dict[tuple[str, str | None, str | None], MatchResult] | None = None,
    competition_id: str = "fifa_world_cup_2026",
) -> dict[str, Any]:
    match_input = analysis.match_input
    fixture = match_input.fixture
    market: dict[str, Any] = {
        "1x2": analysis.market_1x2,
        "ou_2_5": analysis.market_ou_2_5,
    }
    if analysis.market_ah_main is not None:
        market["ah_main"] = analysis.market_ah_main
    match = {
        "competition": competition_block(competition_id),
        "source_event_id": match_input.odds_event.source_event_id,
        "source_match_no": fixture.source_match_no,
        "kickoff_at_utc": fixture.kickoff_at_utc.isoformat(),
        "stage": fixture.stage,
        "group": fixture.group,
        "venue_name": fixture.venue_name,
        "home_team": fixture.home_team_name,
        "away_team": fixture.away_team_name,
        "home_canonical": fixture.home_canonical,
        "away_canonical": fixture.away_canonical,
        "odds_updated_at": _latest_quote_update_iso(analysis),
        "elo": {
            "home": match_input.home_elo.rating,
            "away": match_input.away_elo.rating,
        },
        "model": {
            "lambdas": {
                "home": analysis.lambdas[0],
                "away": analysis.lambdas[1],
            },
            "poisson_tail": analysis.poisson_tail,
            "mu_total": analysis.mu_total_used,
            "mu_prior": analysis.mu_prior_used,
            "mu_market": analysis.mu_market_used,
            "mu_market_weight": analysis.mu_market_weight,
            "total_mu_source": analysis.total_mu_source,
            "same_market_total_anchor": analysis.same_market_total_anchor,
            "ou_line": analysis.ou_line,
            "elo_1x2": analysis.elo_1x2,
            "poisson_1x2": analysis.poisson_1x2,
            "combined_1x2": analysis.combined_1x2,
            "ou_2_5": analysis.ou_2_5,
            "probability_families": analysis.probability_families,
        },
        "market": market,
        "signals": [_signal_to_dict(signal) for signal in signals],
    }
```

Keep the existing `ou_total_shadow`, `lineup_shadow`, and `result` logic after this block unchanged.

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for local runner tests; existing snapshot serialization tests must continue to pass.

- [ ] **Step 5: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/local_runner.py tests/test_local_runner.py
git commit -m "feat: add competition metadata to snapshots"
```

## Task 6: Local CSL League Runner

**Files:**
- Create: `worldcup/league_runner.py`
- Create: `tests/test_league_runner.py`
- Modify: `worldcup/local_runner.py`

- [ ] **Step 1: Write failing league runner tests**

Create `tests/test_league_runner.py`:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_runner import (
    build_league_snapshot_from_cache,
    cap_signals_for_pending_club_rating,
)
from worldcup.models import Grade, MarketType, Signal


def _write_csl_cache(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "theoddsapi_csl_2026_odds.json").write_text(
        json.dumps(
            [
                {
                    "id": "csl-event-1",
                    "sport_key": "soccer_china_superleague",
                    "commence_time": "2026-07-04T11:35:00Z",
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                    "bookmakers": [
                        {
                            "key": "bk1",
                            "last_update": "2026-07-04T02:00:00Z",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Shanghai Port", "price": 1.9},
                                        {"name": "Beijing Guoan", "price": 3.8},
                                        {"name": "Draw", "price": 3.4},
                                    ],
                                },
                                {
                                    "key": "spreads",
                                    "outcomes": [
                                        {"name": "Shanghai Port", "price": 1.95, "point": -0.5},
                                        {"name": "Beijing Guoan", "price": 1.85, "point": 0.5},
                                    ],
                                },
                                {
                                    "key": "totals",
                                    "outcomes": [
                                        {"name": "Over", "price": 1.91, "point": 2.5},
                                        {"name": "Under", "price": 1.89, "point": 2.5},
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )


def test_cap_signals_for_pending_club_rating_caps_strong_grades():
    signals = [
        Signal(MarketType.X12, "home", Grade.S, 0.12, 0.05, "OK", reasons=["value"]),
        Signal(MarketType.OU, "over", Grade.A, 0.06, 0.03, "OK", reasons=["value"]),
        Signal(MarketType.AH, "away", Grade.B, 0.04, None, "OK", reasons=["value"], line=0.5),
    ]

    capped = cap_signals_for_pending_club_rating(signals)

    assert [signal.grade for signal in capped] == [Grade.B, Grade.B, Grade.B]
    assert "club_rating_pending" in capped[0].reasons
    assert "club_rating_pending" in capped[1].reasons
    assert capped[2].reasons == ["value"]


def test_build_league_snapshot_from_cache_builds_local_csl_snapshot():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_cache(cache_dir)

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            competition_id="csl_2026",
            snapshot_at="2026-07-01T00:00:00+00:00",
        )

        assert snapshot["snapshot_at"] == "2026-07-01T00:00:00+00:00"
        assert snapshot["counts"]["fixtures"] == 1
        assert snapshot["counts"]["matches"] == 1
        assert snapshot["data_quality"]["fixture_source"] == "odds_event_only"
        assert "club_rating_pending" in snapshot["data_quality"]["warnings"]
        match = snapshot["matches"][0]
        assert match["competition"]["id"] == "csl_2026"
        assert match["competition"]["name"] == "中超 2026"
        assert match["competition"]["rating_policy"] == "club_rating_pending"
        assert match["home_team"] == "Shanghai Port"
        assert match["home_canonical"] == "shanghai_port"
        assert match["away_canonical"] == "beijing_guoan"
        assert match["signals"]
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.league_runner'`.

- [ ] **Step 3: Add reusable signal cap in `worldcup/local_runner.py`**

Add `replace` to the imports if it is not already imported:

```python
from dataclasses import replace
```

Add this helper near `_signal_to_dict()`:

```python
def cap_signals_for_pending_club_rating(signals: list[Signal]) -> list[Signal]:
    capped: list[Signal] = []
    for signal in signals:
        if signal.grade in (Grade.S, Grade.A):
            reasons = list(signal.reasons)
            if "club_rating_pending" not in reasons:
                reasons.append("club_rating_pending")
            capped.append(replace(signal, grade=Grade.B, reasons=reasons))
        else:
            capped.append(signal)
    return capped
```

- [ ] **Step 4: Implement `worldcup/league_runner.py`**

Create `worldcup/league_runner.py`:

```python
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.collectors.models import EloRating
from worldcup.competitions import get_competition
from worldcup.config import load_config
from worldcup.local_runner import (
    _analysis_to_dict,
    cap_signals_for_pending_club_rating,
    write_snapshot,
)
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input, generate_value_signals


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _odds_cache_name(competition_id: str) -> str:
    return f"theoddsapi_{competition_id}_odds.json"


def _placeholder_rating(code: str) -> EloRating:
    return EloRating(code=code, rank=0, rating=1500)


def _match_input_from_fixture_event(fixture, event) -> MatchAnalysisInput:
    return MatchAnalysisInput(
        fixture=fixture,
        odds_event=event,
        home_elo=_placeholder_rating(fixture.home_canonical or fixture.home_team_name),
        away_elo=_placeholder_rating(fixture.away_canonical or fixture.away_team_name),
        quotes=event.quotes,
        neutral=False,
        home_advantage_elo=0.0,
    )


def build_league_snapshot_from_cache(
    cache_dir: str | Path,
    competition_id: str = "csl_2026",
    snapshot_at: str | None = None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    competition = get_competition(competition_id)
    cfg = cfg or load_config()
    observed_at = snapshot_at or _now_utc_iso()
    cache_path = Path(cache_dir)
    odds_path = cache_path / _odds_cache_name(competition_id)
    parsed = parse_league_odds_events(_read_json(odds_path), competition_id=competition_id)
    matches: list[dict[str, Any]] = []
    for fixture, event in zip(parsed.fixtures, parsed.odds_events):
        match_input = _match_input_from_fixture_event(fixture, event)
        analysis = analyze_match_input(match_input, cfg)
        signals = generate_value_signals(analysis, cfg, observed_at=observed_at)
        if competition.rating_policy == "club_rating_pending":
            signals = cap_signals_for_pending_club_rating(signals)
        matches.append(_analysis_to_dict(analysis, signals, competition_id=competition_id))
    data_quality = {
        "fixture_source": parsed.fixture_source,
        "warnings": [],
        "club_alias_unmatched": parsed.unmatched_clubs,
    }
    if parsed.fixture_source == "odds_event_only":
        data_quality["warnings"].append("fixture_source_odds_event_only")
    if competition.rating_policy == "club_rating_pending":
        data_quality["warnings"].append("club_rating_pending")
    return {
        "snapshot_at": observed_at,
        "counts": {
            "fixtures": len(parsed.fixtures),
            "odds_events": len(parsed.odds_events),
            "matches": len(matches),
        },
        "data_quality": data_quality,
        "matches": matches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local league analysis snapshot from cached odds events.")
    parser.add_argument("--competition", default="csl_2026")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--out", default="data/cache/league_analysis_snapshot.json")
    args = parser.parse_args(argv)
    snapshot = build_league_snapshot_from_cache(args.cache_dir, competition_id=args.competition)
    write_snapshot(snapshot, args.out)
    print(f"wrote {args.out} with {snapshot['counts']['matches']} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Fix the test import**

The test imports `cap_signals_for_pending_club_rating` from `worldcup.league_runner`, so re-export it by keeping this import in `league_runner.py`:

```python
from worldcup.local_runner import (
    _analysis_to_dict,
    cap_signals_for_pending_club_rating,
    write_snapshot,
)
```

- [ ] **Step 6: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for league runner tests. If the sample produces no signals because thresholds are conservative, adjust only the test sample odds to create a signal; do not lower project thresholds.

- [ ] **Step 7: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/local_runner.py worldcup/league_runner.py tests/test_league_runner.py
git commit -m "feat: add local csl league runner"
```

## Task 7: Competition Labels In Ledger UI

**Files:**
- Modify: `worldcup/ledger.py`
- Modify: `worldcup/ledger_html.py`
- Modify: `tests/test_preview.py`
- Modify: `tests/test_ledger.py` if existing ledger projection tests are a better fit in the current dirty tree.

- [ ] **Step 1: Write failing preview test for competition labels**

Add to `tests/test_preview.py`:

```python
def test_build_preview_html_uses_snapshot_competition_labels_and_filter_values():
    snapshot = _snapshot()
    snapshot["matches"][0]["competition"] = {
        "id": "csl_2026",
        "name": "中超 2026",
        "kind": "domestic_league",
        "country": "CN",
        "season": "2026",
        "source": "theoddsapi",
        "fixture_source": "odds_event_only",
        "rating_policy": "club_rating_pending",
    }

    html = build_preview_html(snapshot)

    assert "中超 2026" in html
    assert 'data-league="csl_2026"' in html
    assert '<option value="csl_2026">中超 2026</option>' in html
    assert "club_rating_pending" not in html
    assert "下注金额" not in html
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL because the existing filter uses hard-coded `worldcup` / `csl` values or does not render snapshot competition labels dynamically.

- [ ] **Step 3: Add projection helpers in `worldcup/ledger.py`**

Add these helpers near other formatting helpers:

```python
def competition_id_for_match(match: dict[str, Any]) -> str:
    competition = match.get("competition") or {}
    value = str(competition.get("id") or "").strip()
    if value:
        return value
    return "fifa_world_cup_2026"


def competition_label_for_match(match: dict[str, Any]) -> str:
    competition = match.get("competition") or {}
    value = str(competition.get("name") or "").strip()
    if value:
        return value
    return "2026 世界杯"


def competition_options(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    options: dict[str, str] = {}
    for match in snapshot.get("matches") or []:
        options[competition_id_for_match(match)] = competition_label_for_match(match)
    return [
        {"id": competition_id, "name": name}
        for competition_id, name in sorted(options.items(), key=lambda item: item[1])
    ]
```

When building row dictionaries in the existing ledger projection, add:

```python
"competition_id": competition_id_for_match(match),
"competition_label": competition_label_for_match(match),
```

If the current dirty version already has equivalent helpers, keep its names and only add missing behavior.

- [ ] **Step 4: Modify `worldcup/ledger_html.py`**

Where the league filter options are hard-coded, render from `competition_options(snapshot)`:

```python
competition_filter_options = competition_options(snapshot)
```

Generate option HTML equivalent to:

```python
league_options_html = "\n".join(
    f'<option value="{escape(option["id"])}">{escape(option["name"])}</option>'
    for option in competition_filter_options
)
```

The select should include:

```html
<option value="all">全部赛事</option>
```

and then the dynamic options.

For each row, use:

```html
data-league="{escape(row["competition_id"])}"
```

Display `row["competition_label"]` in the compact match meta area next to stage/round.

- [ ] **Step 5: Run the tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for preview/ledger tests; no raw `club_rating_pending` string should leak into the public page.

- [ ] **Step 6: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add worldcup/ledger.py worldcup/ledger_html.py tests/test_preview.py tests/test_ledger.py
git commit -m "feat: show competition labels in ledger"
```

## Task 8: Documentation And Local Workflow

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README local workflow**

Add a short section under local validation or data sources:

```markdown
## 俱乐部联赛本地 MVP

俱乐部联赛以 competition adapter 接入。当前设计先做中超 `csl_2026` 本地 MVP，并保留英超/西甲/德甲/意甲/法甲后续平滑接入的 registry 约束。

本地只读 sports key 探测使用保存样例，不消耗 The Odds API quota：

```bash
python3 -m worldcup.sources.theoddsapi_sports --sample data/probe/theoddsapi_sports.json
```

中超本地 snapshot 从缓存 odds event 构建，默认不联网：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

中超初期 `rating_policy=club_rating_pending` 时，强信号会降级或仅作为观察；不得把国家队 Elo 套用于俱乐部联赛。任何 live odds 探测、scheduled publish、ECS ingest 或 LaunchAgent 更新都需要单独确认。
```

- [ ] **Step 2: Update `RECENT_WORK.md`**

Add a new top entry:

```markdown
## 2026-06-22 P9.1 俱乐部联赛本地计划

- 新增俱乐部联赛 implementation plan：`docs/superpowers/plans/2026-06-22-domestic-league-adapters.md`。
- 计划范围限定为 Phase 0 sports key 只读探测、Phase 1 多联赛底座、Phase 2 中超本地 MVP；英超和五大联赛作为 adapter 扩展约束，不一次性上线。
- 计划明确 live odds 探测、The Odds API quota 消耗、线上发布、LaunchAgent、ECS ingest、部署、commit/push 都需要单独确认。
```

- [ ] **Step 3: Run documentation checks**

Run:

```bash
git diff --check -- README.md RECENT_WORK.md docs/superpowers/plans/2026-06-22-domestic-league-adapters.md
```

Expected: exit 0.

- [ ] **Step 4: Commit after user confirmation**

Only after the user confirms a local commit:

```bash
git add README.md RECENT_WORK.md docs/superpowers/plans/2026-06-22-domestic-league-adapters.md
git commit -m "docs: plan domestic league adapters"
```

## Final Verification

- [ ] **Step 1: Run the full test suite**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. If the count differs from the latest README because other work changed tests, use the command output as source of truth.

- [ ] **Step 2: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: exit 0.

- [ ] **Step 3: Run local league runner smoke without network**

Only after Task 6 sample cache exists locally:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

Expected: writes `data/cache/league_analysis_snapshot.json` from cached local odds only. If `data/cache/theoddsapi_csl_2026_odds.json` is absent, the command should fail locally with a missing file error and must not attempt network access.

- [ ] **Step 4: Review state**

Run:

```bash
git status --short
```

Expected: only files from this plan are changed, plus any unrelated pre-existing dirty files that were present before implementation.

## Self-Review Checklist

- Spec coverage:
  - Competition registry: Task 1.
  - The Odds API sports key dry-run probe: Task 2.
  - Club alias isolation: Task 3.
  - League odds adapter: Task 4.
  - Snapshot `competition` block compatibility: Task 5.
  - CSL local MVP and `club_rating_pending` cap: Task 6.
  - Ledger competition labels/filter: Task 7.
  - Documentation and recent work: Task 8.
- No live network call is required by any test or smoke command.
- No `.env`, API key, HMAC secret, token, cookie, RDS URL, or private response body is written to docs or tests.
- Strong CSL signals are capped while `rating_policy=club_rating_pending`.
- Existing World Cup fields remain backward-compatible.
