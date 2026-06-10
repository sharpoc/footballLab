from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.engine import elo, ensemble, handicap, odds, poisson, value
from worldcup.models import MarketType, OddsQuote, Signal

_WORLD_CUP_2026_HOST_VENUES = {
    "atlanta": "united_states",
    "boston": "united_states",
    "dallas": "united_states",
    "guadalajara": "mexico",
    "houston": "united_states",
    "kansas city": "united_states",
    "los angeles": "united_states",
    "mexico city": "mexico",
    "miami": "united_states",
    "monterrey": "mexico",
    "new jersey": "united_states",
    "new york": "united_states",
    "philadelphia": "united_states",
    "san francisco": "united_states",
    "seattle": "united_states",
    "toronto": "canada",
    "vancouver": "canada",
}


@dataclass(frozen=True)
class MatchAnalysisInput:
    fixture: Fixture
    odds_event: ParsedOddsEvent
    home_elo: EloRating
    away_elo: EloRating
    quotes: list[OddsQuote] = field(default_factory=list)
    neutral: bool = True
    home_advantage_elo: float = 0.0


@dataclass(frozen=True)
class BuildMatchInputsResult:
    inputs: list[MatchAnalysisInput]
    missing_odds: list[str] = field(default_factory=list)
    missing_elo: list[str] = field(default_factory=list)
    time_mismatches: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchAnalysis:
    match_input: MatchAnalysisInput
    lambdas: tuple[float, float]
    poisson_tail: float
    elo_1x2: dict[str, float]
    poisson_1x2: dict[str, float]
    combined_1x2: dict[str, float]
    ou_2_5: dict[str, float]
    handicap_dist: dict[int, float]
    market_1x2: dict
    market_ou_2_5: dict


def _match_label(fixture: Fixture) -> str:
    return f"{fixture.home_team_name} vs {fixture.away_team_name}"


def _event_key(kickoff_at_utc: datetime, home_canonical: str | None, away_canonical: str | None) -> tuple:
    return (kickoff_at_utc, home_canonical, away_canonical)


def _pair_key(home_canonical: str | None, away_canonical: str | None) -> tuple:
    return (home_canonical, away_canonical)


def _canonical_to_elo_code(elo_aliases: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, code in elo_aliases.items():
        mapping[canonicalize_team(name)] = code
    return mapping


def _world_cup_2026_host_canonical(venue_name: str | None) -> str | None:
    normalized = (venue_name or "").lower()
    for fragment, canonical in _WORLD_CUP_2026_HOST_VENUES.items():
        if fragment in normalized:
            return canonical
    return None


def _fixture_home_advantage_elo(fixture: Fixture, host_advantage_elo: float) -> float:
    host = _world_cup_2026_host_canonical(fixture.venue_name)
    if host is None:
        return 0.0
    if fixture.home_canonical == host and fixture.away_canonical != host:
        return host_advantage_elo
    if fixture.away_canonical == host and fixture.home_canonical != host:
        return -host_advantage_elo
    return 0.0


def build_match_inputs(
    fixtures: list[Fixture],
    odds_events: list[ParsedOddsEvent],
    elo_ratings: dict[str, EloRating],
    elo_aliases: dict[str, str],
    host_advantage_elo: float = 100.0,
) -> BuildMatchInputsResult:
    event_by_key = {
        _event_key(event.kickoff_at_utc, event.home_canonical, event.away_canonical): event
        for event in odds_events
    }
    events_by_pair: dict[tuple, list[ParsedOddsEvent]] = {}
    for event in odds_events:
        events_by_pair.setdefault(_pair_key(event.home_canonical, event.away_canonical), []).append(event)
    elo_code_by_canonical = _canonical_to_elo_code(elo_aliases)

    inputs: list[MatchAnalysisInput] = []
    missing_odds: list[str] = []
    missing_elo: list[str] = []
    time_mismatches: list[str] = []

    for fixture in fixtures:
        if fixture.has_placeholder_team:
            continue
        home_code = elo_code_by_canonical.get(fixture.home_canonical or "")
        away_code = elo_code_by_canonical.get(fixture.away_canonical or "")
        home_elo = elo_ratings.get(home_code or "")
        away_elo = elo_ratings.get(away_code or "")
        if home_elo is None:
            missing_elo.append(fixture.home_team_name)
        if away_elo is None:
            missing_elo.append(fixture.away_team_name)

        event = event_by_key.get(
            _event_key(fixture.kickoff_at_utc, fixture.home_canonical, fixture.away_canonical)
        )
        if event is None:
            pair_events = events_by_pair.get(_pair_key(fixture.home_canonical, fixture.away_canonical), [])
            if len(pair_events) == 1:
                event = pair_events[0]
                time_mismatches.append(_match_label(fixture))
        if event is None:
            missing_odds.append(_match_label(fixture))

        if event is None or home_elo is None or away_elo is None:
            continue

        inputs.append(
            MatchAnalysisInput(
                fixture=fixture,
                odds_event=event,
                home_elo=home_elo,
                away_elo=away_elo,
                quotes=event.quotes,
                home_advantage_elo=_fixture_home_advantage_elo(fixture, host_advantage_elo),
            )
        )

    return BuildMatchInputsResult(
        inputs=inputs,
        missing_odds=missing_odds,
        missing_elo=missing_elo,
        time_mismatches=time_mismatches,
    )


def _adjusted_dr(match_input: MatchAnalysisInput, cfg: dict) -> float:
    dr = match_input.home_elo.rating - match_input.away_elo.rating
    if match_input.home_advantage_elo:
        dr += match_input.home_advantage_elo
    elif not match_input.neutral:
        dr += cfg["elo"]["home_adv"]
    return dr


def _elo_home_advantage(match_input: MatchAnalysisInput, cfg: dict) -> float | None:
    if match_input.home_advantage_elo:
        return match_input.home_advantage_elo
    if not match_input.neutral:
        return cfg["elo"]["home_adv"]
    return None


def analyze_match_input(match_input: MatchAnalysisInput, cfg: dict) -> MatchAnalysis:
    dr = _adjusted_dr(match_input, cfg)
    lh, la = poisson.lambdas(dr, cfg["poisson"])
    matrix, tail = poisson.score_matrix(lh, la, cfg["poisson"])
    handicap_dist = handicap.diff_distribution(matrix)
    poisson_1x2 = poisson.probs_1x2(matrix)
    elo_1x2 = elo.win_draw_loss(
        match_input.home_elo.rating,
        match_input.away_elo.rating,
        neutral=match_input.neutral,
        cfg=cfg["elo"],
        home_advantage=_elo_home_advantage(match_input, cfg),
    )
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2,
        poisson_1x2,
        cfg["ensemble"]["w_elo"],
        cfg["ensemble"]["w_poisson"],
    )
    ou_line = cfg.get("ou_main_line", 2.5)
    p_over = poisson.prob_over(matrix, ou_line)
    ratio = cfg["odds"]["outlier_ratio"]
    return MatchAnalysis(
        match_input=match_input,
        lambdas=(lh, la),
        poisson_tail=tail,
        elo_1x2=elo_1x2,
        poisson_1x2=poisson_1x2,
        combined_1x2=combined_1x2,
        ou_2_5={"over": p_over, "under": 1.0 - p_over},
        handicap_dist=handicap_dist,
        market_1x2=odds.aggregate_market(
            match_input.quotes,
            market_type=MarketType.X12,
            line=None,
            selections=["home", "draw", "away"],
            ratio=ratio,
        ),
        market_ou_2_5=odds.aggregate_market(
            match_input.quotes,
            market_type=MarketType.OU,
            line=ou_line,
            selections=["over", "under"],
            ratio=ratio,
        ),
    )


def _value_cfg(cfg: dict) -> dict:
    value_cfg = dict(cfg["value"])
    value_cfg["min_books"] = cfg["odds"]["min_books"]
    return value_cfg


def _normalize_observed_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        observed = value
    else:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _line_matches(quote_line: float | None, line: float | None) -> bool:
    if line is None:
        return True
    return quote_line is not None and abs(quote_line - line) < 1e-9


def _odds_age_seconds(
    quotes: list[OddsQuote],
    market_type: MarketType,
    selection: str,
    line: float | None,
    observed_at: datetime | None,
) -> float | None:
    if observed_at is None:
        return None
    fetched_times = [
        quote.fetched_at.astimezone(timezone.utc)
        for quote in quotes
        if quote.market_type == market_type
        and quote.selection == selection
        and _line_matches(quote.line, line)
        and quote.fetched_at is not None
    ]
    if not fetched_times:
        return None
    latest_fetched_at = max(fetched_times)
    return max(0.0, (observed_at - latest_fetched_at).total_seconds())


def _signal_ctx(
    match_input: MatchAnalysisInput,
    market_type: MarketType,
    selection: str,
    line: float | None,
    n_books: int,
    observed_at: datetime | None,
    depends_on_backup: bool,
) -> dict:
    ctx = {
        "status": "OK",
        "n_books": n_books,
        "depends_on_backup": depends_on_backup,
        "line_changed_unknown": False,
    }
    odds_age_seconds = _odds_age_seconds(
        match_input.quotes,
        market_type,
        selection,
        line,
        observed_at,
    )
    if odds_age_seconds is not None:
        ctx["odds_age_seconds"] = odds_age_seconds
    return ctx


def _line_label(line: float) -> str:
    return f"{line:+g}" if line > 0 else f"{line:g}"


def _main_home_ah_line(quotes: list[OddsQuote]) -> float | None:
    counts: dict[float, int] = {}
    for quote in quotes:
        if quote.market_type == MarketType.AH and quote.selection == "home" and quote.line is not None:
            counts[quote.line] = counts.get(quote.line, 0) + 1
    if not counts:
        return None
    return sorted(counts, key=lambda line: (-counts[line], abs(line), line))[0]


def _invert_dist(dist: dict[int, float]) -> dict[int, float]:
    return {-diff: prob for diff, prob in dist.items()}


def _integer_market_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    market_type: MarketType,
    selections: list[str],
    model_probs: dict[str, float],
    market: dict,
    line: float | None = None,
    observed_at: datetime | None = None,
    depends_on_backup: bool = False,
) -> list[Signal]:
    value_cfg = _value_cfg(cfg)
    out: list[Signal] = []
    market_probs = market.get("market_probs", {})
    market_odds = market.get("odds", {})
    n_books = market.get("n_books_by_selection", {})
    for selection in selections:
        out.append(
            value.grade_signal(
                market_type,
                selection,
                model_probs[selection],
                market_probs.get(selection),
                market_odds.get(selection),
                _signal_ctx(
                    analysis.match_input,
                    market_type,
                    selection,
                    line,
                    n_books.get(selection, 0),
                    observed_at,
                    depends_on_backup,
                ),
                value_cfg,
                line=line,
            )
        )
    return out


def _ah_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    observed_at: datetime | None = None,
    depends_on_backup: bool = False,
) -> list[Signal]:
    home_line = _main_home_ah_line(analysis.match_input.quotes)
    if home_line is None:
        return []
    ratio = cfg["odds"]["outlier_ratio"]
    value_cfg = _value_cfg(cfg)
    out: list[Signal] = []

    home_agg = odds.aggregate(
        analysis.match_input.quotes,
        MarketType.AH,
        "home",
        line=home_line,
        ratio=ratio,
    )
    if home_agg["odds"] is not None:
        ah_ev = handicap.ev_handicap(analysis.handicap_dist, home_line, home_agg["odds"])
        out.append(
            value.grade_signal(
                MarketType.AH,
                f"home_{_line_label(home_line)}",
                0.0,
                None,
                home_agg["odds"],
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "home",
                    home_line,
                    home_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                ),
                value_cfg,
                ah_ev=ah_ev,
                line=home_line,
            )
        )

    away_line = -home_line
    away_agg = odds.aggregate(
        analysis.match_input.quotes,
        MarketType.AH,
        "away",
        line=away_line,
        ratio=ratio,
    )
    if away_agg["odds"] is not None:
        away_ev = handicap.ev_handicap(_invert_dist(analysis.handicap_dist), away_line, away_agg["odds"])
        out.append(
            value.grade_signal(
                MarketType.AH,
                f"away_{_line_label(away_line)}",
                0.0,
                None,
                away_agg["odds"],
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "away",
                    away_line,
                    away_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                ),
                value_cfg,
                ah_ev=away_ev,
                line=away_line,
            )
        )
    return out


def generate_value_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    observed_at: datetime | str | None = None,
    stale_sources: list[str] | None = None,
) -> list[Signal]:
    observed = _normalize_observed_at(observed_at)
    depends_on_backup = bool(stale_sources)
    return [
        *_integer_market_signals(
            analysis,
            cfg,
            MarketType.X12,
            ["home", "draw", "away"],
            analysis.combined_1x2,
            analysis.market_1x2,
            observed_at=observed,
            depends_on_backup=depends_on_backup,
        ),
        *_integer_market_signals(
            analysis,
            cfg,
            MarketType.OU,
            ["over", "under"],
            analysis.ou_2_5,
            analysis.market_ou_2_5,
            line=cfg.get("ou_main_line", 2.5),
            observed_at=observed,
            depends_on_backup=depends_on_backup,
        ),
        *_ah_signals(analysis, cfg, observed_at=observed, depends_on_backup=depends_on_backup),
    ]
