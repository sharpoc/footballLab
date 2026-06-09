# Plan 2 Collectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first pure-Python collector parsing layer for openfootball fixtures, The Odds API odds, eloratings Elo, and team alias matching, using saved probe samples as offline inputs.

**Architecture:** Collectors are pure parsers: no network, no `.env`, no database, no cloud. They convert raw JSON/TSV structures into small dataclasses that the existing engine can consume later. Network fetching, quota ledger, scheduler, cache persistence, and ECS ingest remain out of scope for this plan.

**Tech Stack:** Python 3.11+, standard library only, existing `tests/run_tests.py` runner.

---

## File Structure

- Create `worldcup/collectors/__init__.py`: package exports.
- Create `worldcup/collectors/models.py`: collector dataclasses (`Fixture`, `EloRating`, `ParsedOddsEvent`, `TeamAlias`, `TeamAliasResult`).
- Create `worldcup/collectors/team_aliases.py`: canonical team normalization and required MVP aliases.
- Create `worldcup/collectors/openfootball.py`: parse openfootball 2026 JSON into `Fixture`.
- Create `worldcup/collectors/theoddsapi.py`: parse The Odds API events into `ParsedOddsEvent` and existing `OddsQuote`.
- Create `worldcup/collectors/eloratings.py`: parse `World.tsv` and `en.teams.tsv` into Elo ratings and aliases.
- Create `tests/collectors/test_team_aliases.py`: alias behavior.
- Create `tests/collectors/test_openfootball.py`: fixture parsing and timezone behavior.
- Create `tests/collectors/test_theoddsapi.py`: market mapping and quote parsing.
- Create `tests/collectors/test_eloratings.py`: Elo TSV parsing.
- Create `tests/collectors/test_probe_samples.py`: optional smoke test against ignored `data/probe/` samples when present.

## Task 1: Collector Models And Team Aliases

**Files:**
- Create: `worldcup/collectors/models.py`
- Create: `worldcup/collectors/team_aliases.py`
- Create: `worldcup/collectors/__init__.py`
- Test: `tests/collectors/test_team_aliases.py`

- [ ] **Step 1: Write failing tests**

```python
from worldcup.collectors.team_aliases import canonicalize_team, match_team_alias


def test_canonicalize_team_handles_required_aliases():
    assert canonicalize_team("Czech Republic") == "czech_republic"
    assert canonicalize_team("Czechia") == "czech_republic"
    assert canonicalize_team("USA") == "united_states"
    assert canonicalize_team("United States") == "united_states"
    assert canonicalize_team("Bosnia & Herzegovina") == "bosnia_herzegovina"


def test_match_team_alias_reports_unmatched_name():
    result = match_team_alias("Imaginary FC")
    assert result.canonical_key is None
    assert result.unmatched_name == "Imaginary FC"
```

- [ ] **Step 2: Run red test**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: fails because `worldcup.collectors` does not exist.

- [ ] **Step 3: Implement models and alias helpers**

Implement dataclasses and normalization with explicit aliases for `Czech Republic`, `Czechia`, `USA`, `United States`, and `Bosnia & Herzegovina`.

- [ ] **Step 4: Run green test**

Run the full local test runner and expect all tests to pass.

## Task 2: Openfootball Fixture Parser

**Files:**
- Create: `worldcup/collectors/openfootball.py`
- Test: `tests/collectors/test_openfootball.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import timezone

from worldcup.collectors.openfootball import parse_openfootball_fixtures


def test_parse_openfootball_fixture_converts_offset_to_utc():
    raw = {
        "name": "World Cup 2026",
        "matches": [
            {
                "round": "Matchday 1",
                "date": "2026-06-11",
                "time": "13:00 UTC-6",
                "team1": "Mexico",
                "team2": "South Africa",
                "group": "Group A",
                "ground": "Mexico City",
            }
        ],
    }
    fixtures = parse_openfootball_fixtures(raw)
    fixture = fixtures[0]
    assert fixture.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert fixture.kickoff_at_utc.tzinfo is timezone.utc
    assert fixture.home_team_name == "Mexico"
    assert fixture.away_team_name == "South Africa"
    assert fixture.home_canonical == "mexico"
    assert fixture.away_canonical == "south_africa"


def test_parse_openfootball_fixture_marks_placeholders():
    raw = {
        "matches": [
            {
                "round": "Final",
                "date": "2026-07-19",
                "time": "15:00 UTC-4",
                "team1": "W101",
                "team2": "W102",
                "ground": "New York/New Jersey (East Rutherford)",
            }
        ]
    }
    fixture = parse_openfootball_fixtures(raw)[0]
    assert fixture.has_placeholder_team is True
    assert fixture.home_canonical is None
    assert fixture.away_canonical is None
```

- [ ] **Step 2: Run red test**

Expected: fails because parser does not exist.

- [ ] **Step 3: Implement parser**

Parse `date` + `time` strings like `13:00 UTC-6` into timezone-aware UTC datetimes. Mark placeholder teams such as `W101`, `1A`, and `3C/E/F/H/I`.

- [ ] **Step 4: Run green test**

Run the full local test runner and expect all tests to pass.

## Task 3: The Odds API Parser

**Files:**
- Create: `worldcup/collectors/theoddsapi.py`
- Test: `tests/collectors/test_theoddsapi.py`

- [ ] **Step 1: Write failing tests**

```python
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.models import MarketType


def test_parse_theoddsapi_maps_markets_to_odds_quotes():
    raw = [
        {
            "id": "event-1",
            "sport_key": "soccer_fifa_world_cup",
            "commence_time": "2026-06-11T19:00:00Z",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "last_update": "2026-06-08T14:25:34Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Mexico", "price": 1.42},
                                {"name": "South Africa", "price": 8.69},
                                {"name": "Draw", "price": 4.59},
                            ],
                        },
                        {
                            "key": "spreads",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Mexico", "price": 2.05, "point": -1.25},
                                {"name": "South Africa", "price": 1.86, "point": 1.25},
                            ],
                        },
                        {
                            "key": "totals",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Over", "price": 1.95, "point": 2.25},
                                {"name": "Under", "price": 1.93, "point": 2.25},
                            ],
                        },
                        {"key": "h2h_lay", "outcomes": [{"name": "Mexico", "price": 1.46}]},
                    ],
                }
            ],
        }
    ]
    event = parse_theoddsapi_events(raw)[0]
    assert event.source_event_id == "event-1"
    assert event.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert len(event.quotes) == 7
    assert {q.market_type for q in event.quotes} == {MarketType.X12, MarketType.AH, MarketType.OU}
    assert any(q.selection == "draw" and q.market_type == MarketType.X12 for q in event.quotes)
    assert any(q.selection == "home" and q.market_type == MarketType.AH and q.line == -1.25 for q in event.quotes)
    assert any(q.selection == "over" and q.market_type == MarketType.OU and q.line == 2.25 for q in event.quotes)
```

- [ ] **Step 2: Run red test**

Expected: fails because parser does not exist.

- [ ] **Step 3: Implement parser**

Map `h2h` to `MarketType.X12`, `spreads` to `MarketType.AH`, and `totals` to `MarketType.OU`. Normalize selections to `home`, `away`, `draw`, `over`, and `under`. Ignore unsupported markets such as `h2h_lay`.

- [ ] **Step 4: Run green test**

Run the full local test runner and expect all tests to pass.

## Task 4: Eloratings Parser

**Files:**
- Create: `worldcup/collectors/eloratings.py`
- Test: `tests/collectors/test_eloratings.py`

- [ ] **Step 1: Write failing tests**

```python
from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases


def test_parse_elo_ratings_uses_rank_code_and_rating_columns():
    ratings = parse_elo_ratings("1\t1\tES\t2155\n2\t2\tAR\t2114\n")
    assert ratings["ES"].rank == 1
    assert ratings["ES"].rating == 2155
    assert ratings["AR"].rating == 2114


def test_parse_elo_team_aliases_reads_all_name_columns():
    aliases = parse_elo_team_aliases("CZ\tCzechia\nUS\tUnited States\tUSA\n")
    assert aliases["Czechia"] == "CZ"
    assert aliases["USA"] == "US"
```

- [ ] **Step 2: Run red test**

Expected: fails because parser does not exist.

- [ ] **Step 3: Implement parser**

Read TSV using tab splitting. Ignore malformed rows; do not guess ratings from nonnumeric columns.

- [ ] **Step 4: Run green test**

Run the full local test runner and expect all tests to pass.

## Task 5: Probe Sample Smoke Test And Docs

**Files:**
- Create: `tests/collectors/test_probe_samples.py`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Write sample smoke test**

Use `data/probe/` only when files exist. The test should parse:

- `openfootball_2026.json`: expect 104 fixtures.
- `theoddsapi_wc_odds.json`: expect 72 events and at least one quote per event.
- `elo_world.tsv` + `elo_teams.tsv`: expect Spain (`ES`) rating and alias entries.

- [ ] **Step 2: Run test runner**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 3: Update `RECENT_WORK.md`**

Add a concise entry that Plan 2 collector parsing layer was started, with pure offline parsers and saved sample smoke tests.

## Completion Verification

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
git status --short
```

Expected:

- Test runner reports all tests passed.
- `git diff --check` exits 0.
- Only intentional source, tests, and docs are modified.
