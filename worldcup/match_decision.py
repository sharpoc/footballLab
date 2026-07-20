from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable

from worldcup.engine import odds, poisson
from worldcup.models import MarketType, OddsQuote
from worldcup.decision_settlement import settlement_unit as _settlement_unit

if TYPE_CHECKING:
    from worldcup.pipeline import MatchAnalysis


PICK_LABEL = "MATCH_PICK"
NO_PICK_LABEL = "NO_CLEAN_MARKET"
POLICY_VERSION = "match_pick_v3"

_RATING_FALLBACK_BLOCKERS = {
    "club_rating_pending",
    "club_rating_missing",
    "club_rating_sample_too_small",
    "club_rating_invalid",
    "club_rating_missing_team",
    "club_rating_team_sample_too_small",
}


@dataclass(frozen=True)
class _SettlementProbabilities:
    full_win: float = 0.0
    half_win: float = 0.0
    push: float = 0.0
    half_loss: float = 0.0
    full_loss: float = 0.0

    @property
    def p_hit(self) -> float:
        return self.full_win + self.half_win

    @property
    def p_no_loss(self) -> float:
        return self.p_hit + self.push

    @property
    def p_loss(self) -> float:
        return self.half_loss + self.full_loss

    @property
    def loss_units(self) -> float:
        return self.full_loss + (0.5 * self.half_loss)

    def to_dict(self) -> dict[str, float]:
        return {
            "full_win": _round_metric(self.full_win) or 0.0,
            "half_win": _round_metric(self.half_win) or 0.0,
            "push": _round_metric(self.push) or 0.0,
            "half_loss": _round_metric(self.half_loss) or 0.0,
            "full_loss": _round_metric(self.full_loss) or 0.0,
        }


@dataclass
class _Option:
    market_type: MarketType
    market: str
    selection: str
    line: float | None
    odds: float
    model: _SettlementProbabilities
    p_hit_market: float | None
    p_no_loss_market: float | None
    n_books: int
    dispersion_ratio: float | None = None
    odds_latest_at: datetime | None = None
    hard_vetoes: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    p_hit: float | None = None
    p_hit_safe: float | None = None
    p_no_loss_safe: float | None = None
    uncertainty_penalty: float = 0.0
    market_quality: float = 0.0
    model_market_agreement: float = 0.0
    evidence_score: float = 0.0


def _decision_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    user_cfg = cfg.get("match_decision") or {}
    quality_cfg = cfg.get("quality") or {}
    return {
        "min_books": int(user_cfg.get("min_books", cfg.get("odds", {}).get("min_books", 3))),
        "outlier_ratio": float(
            user_cfg.get("outlier_ratio", cfg.get("odds", {}).get("outlier_ratio", 2.0))
        ),
        "dispersion_ratio_max": float(
            user_cfg.get(
                "dispersion_ratio_max",
                quality_cfg.get("odds_dispersion_ratio_max", 1.18),
            )
        ),
        "odds_max_age_seconds": float(
            user_cfg.get(
                "odds_max_age_seconds",
                cfg.get("value", {}).get("odds_max_age_seconds", 12600),
            )
        ),
        "future_quote_tolerance_seconds": float(
            user_cfg.get("future_quote_tolerance_seconds", 300)
        ),
        "base_uncertainty": float(user_cfg.get("base_uncertainty", 0.02)),
        "medium_dispersion_min": float(user_cfg.get("medium_dispersion_min", 1.10)),
        "medium_dispersion_penalty": float(user_cfg.get("medium_dispersion_penalty", 0.02)),
        "marginal_books_penalty": float(user_cfg.get("marginal_books_penalty", 0.015)),
        "thin_market_penalty": float(user_cfg.get("thin_market_penalty", 0.03)),
        "dispersion_unknown_penalty": float(
            user_cfg.get("dispersion_unknown_penalty", 0.03)
        ),
        "severe_dispersion_penalty": float(
            user_cfg.get("severe_dispersion_penalty", 0.04)
        ),
        "internal_disagreement_penalty": float(
            user_cfg.get("internal_disagreement_penalty", 0.03)
        ),
        "quarter_line_penalty": float(user_cfg.get("quarter_line_penalty", 0.03)),
        "extreme_handicap_penalty": float(
            user_cfg.get("extreme_handicap_penalty", 0.04)
        ),
        "rating_fallback_penalty": float(user_cfg.get("rating_fallback_penalty", 0.04)),
        "near_top_hit_tolerance": float(user_cfg.get("near_top_hit_tolerance", 0.02)),
        "model_disagreement_mild_delta": float(
            user_cfg.get("model_disagreement_mild_delta", 0.08)
        ),
        "model_disagreement_severe_delta": float(
            user_cfg.get("model_disagreement_severe_delta", 0.15)
        ),
        "model_disagreement_mild_penalty": float(
            user_cfg.get("model_disagreement_mild_penalty", 0.015)
        ),
        "model_disagreement_severe_penalty": float(
            user_cfg.get("model_disagreement_severe_penalty", 0.03)
        ),
        "worldcup_market_weight": float(user_cfg.get("worldcup_market_weight", 0.55)),
        "worldcup_model_weight": float(user_cfg.get("worldcup_model_weight", 0.45)),
        "default_market_weight": float(user_cfg.get("default_market_weight", 0.55)),
        "default_model_weight": float(user_cfg.get("default_model_weight", 0.45)),
        # These deliberately inherit the old high-confidence guardrails. They are
        # migration defaults, not parameters fitted on the current small sample.
        "min_p_hit_safe": float(user_cfg.get("min_p_hit_safe", 0.58)),
        "min_p_no_loss_safe": float(user_cfg.get("min_p_no_loss_safe", 0.62)),
        "min_odds": float(user_cfg.get("min_odds", 1.30)),
        "max_odds": float(user_cfg.get("max_odds", 2.20)),
        "internal_disagreement_delta": float(
            user_cfg.get(
                "internal_disagreement_delta",
                quality_cfg.get("disagreement_prob_delta", 0.12),
            )
        ),
        "extreme_handicap_abs_line": float(
            user_cfg.get(
                "extreme_handicap_abs_line",
                quality_cfg.get("extreme_favorite_ah_abs_line_min", 2.5),
            )
        ),
    }


def _round_metric(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _line_key(line: float | None) -> str:
    if line is None:
        return ""
    value = 0.0 if abs(line) < 1e-12 else line
    return f"{value:g}"


def _option_id(option: _Option) -> str:
    return f"{option.market_type.value}|{option.selection}|{_line_key(option.line)}"


def _parse_observed_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_quote_fresh(
    quote: OddsQuote,
    observed_at: datetime | None,
    max_age_seconds: float,
    future_tolerance_seconds: float = 300,
) -> bool:
    if observed_at is None:
        return True
    if quote.fetched_at is None:
        return False
    fetched_at = quote.fetched_at.astimezone(timezone.utc)
    age_seconds = (observed_at - fetched_at).total_seconds()
    return -future_tolerance_seconds <= age_seconds <= max_age_seconds


def _fresh_deduplicated_quotes(
    quotes: Iterable[OddsQuote],
    observed_at: datetime | None,
    max_age_seconds: float,
    future_tolerance_seconds: float = 300,
) -> list[OddsQuote]:
    indexed: dict[tuple[str, MarketType, str, float | None], OddsQuote] = {}
    for quote in quotes:
        if not _is_quote_fresh(
            quote,
            observed_at,
            max_age_seconds,
            future_tolerance_seconds,
        ):
            continue
        key = (quote.bookmaker, quote.market_type, quote.selection, quote.line)
        previous = indexed.get(key)
        if previous is None:
            indexed[key] = quote
            continue
        previous_at = previous.fetched_at or datetime.min.replace(tzinfo=timezone.utc)
        quote_at = quote.fetched_at or datetime.min.replace(tzinfo=timezone.utc)
        if quote_at >= previous_at:
            indexed[key] = quote
    return list(indexed.values())


def prepare_match_input_for_pick(
    match_input: Any,
    cfg: dict[str, Any],
    observed_at: datetime | str | None,
) -> Any:
    """Remove stale, missing-time and future quotes before model analysis.

    Filtering only after ``analyze_match_input`` is too late because OU prices
    may already have anchored the Poisson total. The returned frozen dataclasses
    preserve every non-quote field and the invalid-quote diagnostics.
    """

    observed = _parse_observed_at(observed_at)
    decision_cfg = _decision_cfg(cfg)
    fresh_quotes = _fresh_deduplicated_quotes(
        match_input.quotes,
        observed,
        decision_cfg["odds_max_age_seconds"],
        decision_cfg["future_quote_tolerance_seconds"],
    )
    odds_event = replace(match_input.odds_event, quotes=fresh_quotes)
    return replace(match_input, quotes=fresh_quotes, odds_event=odds_event)


def _complete_market_quotes(
    quotes: list[OddsQuote],
    market_type: MarketType,
    required: tuple[tuple[str, float | None], ...],
) -> list[OddsQuote]:
    complete_books = {
        quote.bookmaker
        for quote in quotes
        if quote.market_type == market_type
    }
    for selection, line in required:
        present = {
            quote.bookmaker
            for quote in quotes
            if quote.market_type == market_type
            and quote.selection == selection
            and quote.line == line
        }
        complete_books &= present
    required_keys = set(required)
    return [
        quote
        for quote in quotes
        if quote.bookmaker in complete_books
        and quote.market_type == market_type
        and (quote.selection, quote.line) in required_keys
    ]


def _main_ou_line(quotes: list[OddsQuote], fallback_line: float) -> float | None:
    lines = sorted(
        {
            float(quote.line)
            for quote in quotes
            if quote.market_type == MarketType.OU and quote.line is not None
        }
    )
    ranked: list[tuple[int, float, float]] = []
    for line in lines:
        paired = _complete_market_quotes(
            quotes,
            MarketType.OU,
            (("over", line), ("under", line)),
        )
        books = {quote.bookmaker for quote in paired}
        if books:
            ranked.append((-len(books), abs(line - fallback_line), line))
    return min(ranked)[2] if ranked else None


def _main_home_ah_line(quotes: list[OddsQuote]) -> float | None:
    lines = sorted(
        {
            float(quote.line)
            for quote in quotes
            if quote.market_type == MarketType.AH
            and quote.selection == "home"
            and quote.line is not None
        }
    )
    ranked: list[tuple[int, float, float, float]] = []
    for line in lines:
        paired = _complete_market_quotes(
            quotes,
            MarketType.AH,
            (("home", line), ("away", -line)),
        )
        books = {quote.bookmaker for quote in paired}
        if books:
            home = odds.aggregate(paired, MarketType.AH, "home", line=line)
            away = odds.aggregate(paired, MarketType.AH, "away", line=-line)
            home_odds = float(home.get("odds") or 0.0)
            away_odds = float(away.get("odds") or 0.0)
            balance_distance = abs(home_odds - 2.0) + abs(away_odds - 2.0)
            ranked.append((-len(books), balance_distance, abs(line), line))
    return min(ranked)[3] if ranked else None


def _invert_dist(dist: dict[int, float]) -> dict[int, float]:
    return {-diff: prob for diff, prob in dist.items()}




def _settlement_probabilities(
    dist: dict[int, float],
    line: float,
) -> _SettlementProbabilities:
    buckets = {
        "full_win": 0.0,
        "half_win": 0.0,
        "push": 0.0,
        "half_loss": 0.0,
        "full_loss": 0.0,
    }
    for margin, probability in dist.items():
        unit = _settlement_unit(float(margin), line)
        if unit >= 0.75:
            buckets["full_win"] += probability
        elif unit > 0.0:
            buckets["half_win"] += probability
        elif unit <= -0.75:
            buckets["full_loss"] += probability
        elif unit < 0.0:
            buckets["half_loss"] += probability
        else:
            buckets["push"] += probability
    return _SettlementProbabilities(**buckets)


def _total_distribution(matrix: list[list[float]]) -> dict[int, float]:
    dist: dict[int, float] = {}
    for home_goals, row in enumerate(matrix):
        for away_goals, probability in enumerate(row):
            total = home_goals + away_goals
            dist[total] = dist.get(total, 0.0) + probability
    return dist


def _ou_settlement_probabilities(
    total_dist: dict[int, float],
    selection: str,
    line: float,
) -> _SettlementProbabilities:
    if selection == "under":
        return _settlement_probabilities(
            {-total: probability for total, probability in total_dist.items()},
            line,
        )
    return _settlement_probabilities(total_dist, -line)


def _is_quarter_line(line: float | None) -> bool:
    if line is None:
        return False
    return round(line * 4) % 2 != 0


def _unconditional_market_probabilities(
    conditional_hit: float | None,
    model: _SettlementProbabilities,
    *,
    quarter_line: bool,
) -> tuple[float | None, float | None]:
    if conditional_hit is None:
        return None, None
    if quarter_line:
        # Asian quarter-line prices do not identify the split between full/half
        # settlement outcomes. Use the de-vigged two-sided price as a conservative
        # equivalent-win proxy, keep no-loss equal to hit, and mark the option so
        # downstream scoring applies an explicit uncertainty penalty.
        proxy = max(0.0, min(1.0, conditional_hit))
        return proxy, proxy
    p_hit = max(0.0, min(1.0, conditional_hit * (1.0 - model.push)))
    return p_hit, min(1.0, p_hit + model.push)


def _raw_model_family(analysis: MatchAnalysis) -> dict[str, Any]:
    return (
        ((analysis.probability_families or {}).get("families") or {}).get("model_raw")
        or {}
    )


def _raw_lambdas(analysis: MatchAnalysis) -> tuple[float, float]:
    lambdas = _raw_model_family(analysis).get("lambdas") or {}
    home = lambdas.get("home")
    away = lambdas.get("away")
    if isinstance(home, (int, float)) and isinstance(away, (int, float)):
        return float(home), float(away)
    active_total = analysis.lambdas[0] + analysis.lambdas[1]
    if active_total <= 0:
        return analysis.lambdas
    scale = analysis.mu_prior_used / active_total
    return analysis.lambdas[0] * scale, analysis.lambdas[1] * scale


def _market_quality_values(market: dict[str, Any]) -> tuple[int, float | None]:
    counts = [int(value or 0) for value in (market.get("n_books_by_selection") or {}).values()]
    dispersions = [
        float(value)
        for value in (market.get("dispersion_by_selection") or {}).values()
        if isinstance(value, (int, float))
    ]
    return (min(counts) if counts else 0, max(dispersions) if dispersions else None)


def _latest_quote_at(quotes: Iterable[OddsQuote]) -> datetime | None:
    fetched = [
        quote.fetched_at.astimezone(timezone.utc)
        for quote in quotes
        if quote.fetched_at is not None
    ]
    return max(fetched) if fetched else None


def _build_1x2_options(
    analysis: MatchAnalysis,
    quotes: list[OddsQuote],
    decision_cfg: dict[str, Any],
) -> list[_Option]:
    paired = _complete_market_quotes(
        quotes,
        MarketType.X12,
        (("home", None), ("draw", None), ("away", None)),
    )
    market = odds.aggregate_market(
        paired,
        MarketType.X12,
        None,
        ["home", "draw", "away"],
        ratio=decision_cfg["outlier_ratio"],
    )
    market_books, market_dispersion = _market_quality_values(market)
    market_latest_at = _latest_quote_at(paired)
    raw_1x2 = _raw_model_family(analysis).get("combined_1x2") or analysis.combined_1x2
    out: list[_Option] = []
    for selection in ("home", "draw", "away"):
        decimal_odds = (market.get("odds") or {}).get(selection)
        p_model = raw_1x2.get(selection)
        if decimal_odds is None or p_model is None:
            continue
        model = _SettlementProbabilities(
            full_win=float(p_model),
            full_loss=max(0.0, 1.0 - float(p_model)),
        )
        p_market = (market.get("market_probs") or {}).get(selection)
        out.append(
            _Option(
                market_type=MarketType.X12,
                market="1X2",
                selection=selection,
                line=None,
                odds=float(decimal_odds),
                model=model,
                p_hit_market=p_market,
                p_no_loss_market=p_market,
                n_books=market_books,
                dispersion_ratio=market_dispersion,
                odds_latest_at=market_latest_at,
            )
        )
    return out


def _build_ou_options(
    analysis: MatchAnalysis,
    cfg: dict[str, Any],
    quotes: list[OddsQuote],
    decision_cfg: dict[str, Any],
) -> list[_Option]:
    fallback_line = float(cfg.get("ou_main_line", analysis.ou_line))
    line = _main_ou_line(quotes, fallback_line)
    if line is None:
        return []
    paired = _complete_market_quotes(
        quotes,
        MarketType.OU,
        (("over", line), ("under", line)),
    )
    market = odds.aggregate_market(
        paired,
        MarketType.OU,
        line,
        ["over", "under"],
        ratio=decision_cfg["outlier_ratio"],
    )
    market_books, market_dispersion = _market_quality_values(market)
    market_latest_at = _latest_quote_at(paired)
    raw_home_lambda, raw_away_lambda = _raw_lambdas(analysis)
    matrix, _tail = poisson.score_matrix(raw_home_lambda, raw_away_lambda, cfg["poisson"])
    total_dist = _total_distribution(matrix)
    out: list[_Option] = []
    for selection in ("over", "under"):
        decimal_odds = (market.get("odds") or {}).get(selection)
        if decimal_odds is None:
            continue
        model = _ou_settlement_probabilities(total_dist, selection, line)
        conditional = (market.get("market_probs") or {}).get(selection)
        market_hit, market_no_loss = _unconditional_market_probabilities(
            conditional,
            model,
            quarter_line=_is_quarter_line(line),
        )
        out.append(
            _Option(
                market_type=MarketType.OU,
                market="OU",
                selection=selection,
                line=line,
                odds=float(decimal_odds),
                model=model,
                p_hit_market=market_hit,
                p_no_loss_market=market_no_loss,
                n_books=market_books,
                dispersion_ratio=market_dispersion,
                odds_latest_at=market_latest_at,
            )
        )
    return out


def _build_ah_options(
    analysis: MatchAnalysis,
    cfg: dict[str, Any],
    quotes: list[OddsQuote],
    decision_cfg: dict[str, Any],
) -> list[_Option]:
    home_line = _main_home_ah_line(quotes)
    if home_line is None:
        return []
    paired = _complete_market_quotes(
        quotes,
        MarketType.AH,
        (("home", home_line), ("away", -home_line)),
    )
    aggregates = {
        "home": odds.aggregate(
            paired,
            MarketType.AH,
            "home",
            line=home_line,
            ratio=decision_cfg["outlier_ratio"],
        ),
        "away": odds.aggregate(
            paired,
            MarketType.AH,
            "away",
            line=-home_line,
            ratio=decision_cfg["outlier_ratio"],
        ),
    }
    prices = {
        selection: aggregate.get("odds")
        for selection, aggregate in aggregates.items()
        if aggregate.get("odds") is not None
    }
    market_probs = odds.devig(prices) if set(prices) == {"home", "away"} else {}
    market_books = min(int(aggregate.get("n_books") or 0) for aggregate in aggregates.values())
    market_dispersions = [
        float(aggregate["dispersion_ratio"])
        for aggregate in aggregates.values()
        if isinstance(aggregate.get("dispersion_ratio"), (int, float))
    ]
    market_dispersion = max(market_dispersions) if market_dispersions else None
    market_latest_at = _latest_quote_at(paired)
    raw_home_lambda, raw_away_lambda = _raw_lambdas(analysis)
    raw_matrix, _tail = poisson.score_matrix(raw_home_lambda, raw_away_lambda, cfg["poisson"])
    raw_handicap_dist: dict[int, float] = {}
    for home_goals, row in enumerate(raw_matrix):
        for away_goals, probability in enumerate(row):
            margin = home_goals - away_goals
            raw_handicap_dist[margin] = raw_handicap_dist.get(margin, 0.0) + probability
    out: list[_Option] = []
    for selection, line in (("home", home_line), ("away", -home_line)):
        target = aggregates[selection]
        decimal_odds = target.get("odds")
        if decimal_odds is None:
            continue
        side_dist = raw_handicap_dist if selection == "home" else _invert_dist(raw_handicap_dist)
        model = _settlement_probabilities(side_dist, line)
        market_hit, market_no_loss = _unconditional_market_probabilities(
            market_probs.get(selection),
            model,
            quarter_line=_is_quarter_line(line),
        )
        market_name = "DNB" if abs(line) < 1e-12 else "AH"
        option = _Option(
            market_type=MarketType.AH,
            market=market_name,
            selection=selection,
            line=0.0 if abs(line) < 1e-12 else line,
            odds=float(decimal_odds),
            model=model,
            p_hit_market=market_hit,
            p_no_loss_market=market_no_loss,
            n_books=market_books,
            dispersion_ratio=market_dispersion,
            odds_latest_at=market_latest_at,
        )
        if _is_quarter_line(line):
            option.risk_flags.append("quarter_line_market_probability_proxy")
        out.append(option)
    return out


def _market_model_weights(
    analysis: MatchAnalysis,
    decision_cfg: dict[str, Any],
) -> tuple[float, float]:
    sport_key = analysis.match_input.odds_event.sport_key
    if sport_key == "soccer_fifa_world_cup":
        model_weight = decision_cfg["worldcup_model_weight"]
        market_weight = decision_cfg["worldcup_market_weight"]
    else:
        model_weight = decision_cfg["default_model_weight"]
        market_weight = decision_cfg["default_market_weight"]
    model_weight = max(0.0, float(model_weight))
    market_weight = max(0.0, float(market_weight))
    total = model_weight + market_weight
    if total <= 0:
        return 0.5, 0.5
    return model_weight / total, market_weight / total


def _uncertainty_penalty(
    option: _Option,
    decision_cfg: dict[str, Any],
    *,
    use_model_comparison: bool = True,
) -> float:
    penalty = decision_cfg["base_uncertainty"]
    ratio = option.dispersion_ratio
    if ratio is None:
        penalty += decision_cfg["dispersion_unknown_penalty"]
    elif ratio >= decision_cfg["medium_dispersion_min"]:
        penalty += decision_cfg["medium_dispersion_penalty"]
    if ratio is not None and ratio > decision_cfg["dispersion_ratio_max"]:
        penalty += decision_cfg["severe_dispersion_penalty"]
    if option.n_books == decision_cfg["min_books"]:
        penalty += decision_cfg["marginal_books_penalty"]
    elif option.n_books < decision_cfg["min_books"]:
        penalty += decision_cfg["thin_market_penalty"]
    if use_model_comparison and option.p_hit_market is not None:
        delta = abs(option.model.p_hit - option.p_hit_market)
        if delta >= decision_cfg["model_disagreement_severe_delta"]:
            penalty += decision_cfg["model_disagreement_severe_penalty"]
        elif delta >= decision_cfg["model_disagreement_mild_delta"]:
            penalty += decision_cfg["model_disagreement_mild_penalty"]
    if use_model_comparison and "internal_model_disagreement" in option.risk_flags:
        penalty += decision_cfg["internal_disagreement_penalty"]
    if "quarter_line_market_probability_proxy" in option.risk_flags:
        penalty += decision_cfg["quarter_line_penalty"]
    if "extreme_handicap" in option.risk_flags:
        penalty += decision_cfg["extreme_handicap_penalty"]
    if "market_only_rating_fallback" in option.risk_flags:
        penalty += decision_cfg["rating_fallback_penalty"]
    return penalty


def _market_quality_score(option: _Option, decision_cfg: dict[str, Any]) -> float:
    if option.dispersion_ratio is None:
        return 0.0
    span = max(1e-9, decision_cfg["dispersion_ratio_max"] - 1.0)
    return max(0.0, min(1.0, 1.0 - ((option.dispersion_ratio - 1.0) / span)))


def _model_agreement_score(option: _Option) -> float:
    if option.p_hit_market is None:
        return 0.5
    delta = abs(option.model.p_hit - option.p_hit_market)
    return max(0.0, min(1.0, 1.0 - delta / 0.20))


def _evidence_score(
    option: _Option,
    decision_cfg: dict[str, Any],
    *,
    market_only: bool = False,
) -> float:
    book_target = max(decision_cfg["min_books"] + 2, 1)
    book_coverage = max(0.0, min(1.0, option.n_books / book_target))
    if market_only:
        return (option.market_quality + book_coverage) / 2.0
    return (
        option.market_quality
        + option.model_market_agreement
        + book_coverage
    ) / 3.0


def _score_option(
    option: _Option,
    analysis: MatchAnalysis,
    decision_cfg: dict[str, Any],
    *,
    market_only: bool = False,
) -> None:
    if market_only:
        p_hit = option.p_hit_market
        p_no_loss = option.p_no_loss_market
        if p_hit is None or p_no_loss is None:
            raise ValueError("market-only option requires market probabilities")
    elif option.p_hit_market is None or option.p_no_loss_market is None:
        p_hit = option.model.p_hit
        p_no_loss = option.model.p_no_loss
    else:
        model_weight, market_weight = _market_model_weights(analysis, decision_cfg)
        p_hit = (model_weight * option.model.p_hit) + (market_weight * option.p_hit_market)
        p_no_loss = (model_weight * option.model.p_no_loss) + (
            market_weight * option.p_no_loss_market
        )
    penalty = _uncertainty_penalty(
        option,
        decision_cfg,
        use_model_comparison=not market_only,
    )
    option.p_hit = max(0.0, min(1.0, p_hit))
    option.p_hit_safe = max(0.0, min(1.0, p_hit - penalty))
    option.p_no_loss_safe = max(0.0, min(1.0, p_no_loss - penalty))
    option.uncertainty_penalty = penalty
    option.market_quality = _market_quality_score(option, decision_cfg)
    option.model_market_agreement = 0.5 if market_only else _model_agreement_score(option)
    option.evidence_score = _evidence_score(
        option,
        decision_cfg,
        market_only=market_only,
    )


def _x12_internal_disagreement(
    option: _Option,
    analysis: MatchAnalysis,
    decision_cfg: dict[str, Any],
) -> bool:
    if option.market_type != MarketType.X12:
        return False
    raw_family = _raw_model_family(analysis)
    elo_probs = raw_family.get("elo_1x2") or analysis.elo_1x2
    poisson_probs = raw_family.get("poisson_1x2") or analysis.poisson_1x2
    elo_top = max(elo_probs, key=elo_probs.get)
    poisson_top = max(poisson_probs, key=poisson_probs.get)
    if elo_top != poisson_top:
        return True
    elo_probability = elo_probs.get(option.selection)
    poisson_probability = poisson_probs.get(option.selection)
    if elo_probability is None or poisson_probability is None:
        return False
    return (
        abs(elo_probability - poisson_probability)
        >= decision_cfg["internal_disagreement_delta"]
    )


def _apply_hard_vetoes(
    option: _Option,
    analysis: MatchAnalysis,
    decision_cfg: dict[str, Any],
    *,
    use_model: bool = True,
) -> None:
    if option.odds <= 1.0:
        option.hard_vetoes.append("invalid_odds")
    if option.n_books <= 0:
        option.hard_vetoes.append("bookmaker_count_zero")
    elif option.n_books < decision_cfg["min_books"]:
        option.risk_flags.append("thin_market")
    if option.dispersion_ratio is None:
        option.risk_flags.append("market_dispersion_unknown")
    elif option.dispersion_ratio > decision_cfg["dispersion_ratio_max"]:
        option.risk_flags.append("severe_dispersion")
    if use_model and _x12_internal_disagreement(option, analysis, decision_cfg):
        option.risk_flags.append("internal_model_disagreement")
    if (
        option.market == "AH"
        and option.line is not None
        and abs(option.line) >= decision_cfg["extreme_handicap_abs_line"]
    ):
        option.risk_flags.append("extreme_handicap")


def _all_options(
    analysis: MatchAnalysis,
    cfg: dict[str, Any],
    decision_cfg: dict[str, Any],
    observed_at: datetime | None,
) -> list[_Option]:
    quotes = _fresh_deduplicated_quotes(
        analysis.match_input.quotes,
        observed_at,
        decision_cfg["odds_max_age_seconds"],
        decision_cfg["future_quote_tolerance_seconds"],
    )
    return [
        *_build_1x2_options(analysis, quotes, decision_cfg),
        *_build_ou_options(analysis, cfg, quotes, decision_cfg),
        *_build_ah_options(analysis, cfg, quotes, decision_cfg),
    ]


def _decorate_reason(option: _Option, reason: str) -> None:
    if reason not in option.reasons:
        option.reasons.append(reason)


def _base_decision(label: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "policy_version": POLICY_VERSION,
        "label": label,
        "selected_option_id": None,
        "market": None,
        "selection": None,
        "line": None,
        "odds": None,
        "p_hit": None,
        "p_hit_safe": None,
        "p_no_loss_safe": None,
        "reasons": [],
        "risks": [],
        "method": "coverage_evidence_ranked",
    }


def _no_pick(reasons: Iterable[str], *, rejected_count: int = 0) -> dict[str, Any]:
    decision = _base_decision(NO_PICK_LABEL)
    decision["reasons"] = list(dict.fromkeys(str(reason) for reason in reasons if reason))
    decision["rejected_count"] = rejected_count
    return decision


def _make_pick(
    option: _Option,
    analysis: MatchAnalysis,
    observed_at: datetime | None,
    decision_cfg: dict[str, Any],
) -> dict[str, Any]:
    _decorate_reason(option, "main_market_only")
    _decorate_reason(option, "market_data_available")
    _decorate_reason(option, "evidence_ranked_near_top_probability")
    valid_until: datetime | None = None
    if option.odds_latest_at is not None:
        valid_until = option.odds_latest_at + timedelta(
            seconds=decision_cfg["odds_max_age_seconds"]
        )
        kickoff = analysis.match_input.fixture.kickoff_at_utc.astimezone(timezone.utc)
        valid_until = min(valid_until, kickoff)
    decision = {
        **_base_decision(PICK_LABEL),
        "selected_option_id": _option_id(option),
        "market": option.market,
        "selection": option.selection,
        "line": option.line,
        "odds": _round_metric(option.odds),
        "p_hit": _round_metric(option.p_hit),
        "p_hit_safe": _round_metric(option.p_hit_safe),
        "p_no_loss_safe": _round_metric(option.p_no_loss_safe),
        "uncertainty_penalty": _round_metric(option.uncertainty_penalty),
        "evidence_score": _round_metric(option.evidence_score),
        "computed_at": observed_at.isoformat() if observed_at is not None else None,
        "odds_latest_at": option.odds_latest_at.isoformat()
        if option.odds_latest_at is not None
        else None,
        "valid_until": valid_until.isoformat() if valid_until is not None else None,
        "reasons": list(option.reasons),
        "risks": list(option.risk_flags),
    }
    if "market_only_rating_fallback" not in option.risk_flags:
        decision["model_settlement"] = option.model.to_dict()
    return decision


def _is_stale_odds_source(source: Any) -> bool:
    value = str(source or "").strip().lower()
    return "odds" in value or value in {"theoddsapi", "the_odds_api"}


def _global_blockers(
    stale_sources: Iterable[Any],
    hard_blockers: Iterable[Any],
) -> list[str]:
    blockers = [str(reason) for reason in hard_blockers if str(reason or "").strip()]
    for source in stale_sources:
        if _is_stale_odds_source(source):
            blockers.append(f"stale_odds:{source}")
    return list(dict.fromkeys(blockers))


def _rating_fallback_reasons(hard_blockers: Iterable[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(reason)
            for reason in hard_blockers
            if str(reason or "").strip() in _RATING_FALLBACK_BLOCKERS
        )
    )


def _option_rank(option: _Option) -> tuple[Any, ...]:
    return (
        -(option.p_hit_safe or 0.0),
        -(option.p_no_loss_safe or 0.0),
        option.model.loss_units,
        option.model.full_loss,
        -option.market_quality,
        -option.model_market_agreement,
        _option_id(option),
    )


def _best_available_option(
    options: list[_Option],
    decision_cfg: dict[str, Any],
) -> _Option:
    standard_options = [
        option
        for option in options
        if "quarter_line_market_probability_proxy" not in option.risk_flags
        and "extreme_handicap" not in option.risk_flags
    ]
    comparable_options = standard_options or options
    preferred = [
        option
        for option in comparable_options
        if decision_cfg["min_odds"] <= option.odds <= decision_cfg["max_odds"]
    ]
    pool = preferred or comparable_options
    top_hit = max(option.p_hit_safe or 0.0 for option in pool)
    tolerance = max(0.0, decision_cfg["near_top_hit_tolerance"])
    evidence_pool = [
        option
        for option in pool
        if (option.p_hit_safe or 0.0) >= top_hit - tolerance
    ]
    return sorted(
        evidence_pool,
        key=lambda option: (
            -option.evidence_score,
            *_option_rank(option),
        ),
    )[0]


def decide_match_pick(
    analysis: MatchAnalysis,
    cfg: dict[str, Any],
    *,
    observed_at: datetime | str | None = None,
    stale_sources: Iterable[Any] = (),
    hard_blockers: Iterable[Any] = (),
) -> dict[str, Any]:
    """Return one evidence-ranked pick, or an explicit data-availability abstention.

    This v3 decision is independent of the legacy S/A/B/C signal engine. Every
    match with at least one complete, fresh, settleable main market gets a pick.
    """

    observed = _parse_observed_at(observed_at)
    decision_cfg = _decision_cfg(cfg)
    raw_hard_blockers = [
        str(reason) for reason in hard_blockers if str(reason or "").strip()
    ]
    rating_fallback_reasons = _rating_fallback_reasons(raw_hard_blockers)
    blockers = _global_blockers(
        stale_sources,
        [
            reason
            for reason in raw_hard_blockers
            if reason not in _RATING_FALLBACK_BLOCKERS
        ],
    )
    kickoff = analysis.match_input.fixture.kickoff_at_utc.astimezone(timezone.utc)
    if observed is not None and observed >= kickoff:
        blockers.append("match_started")
    if blockers:
        return _no_pick(blockers)

    options = _all_options(analysis, cfg, decision_cfg, observed)
    clean_options: list[_Option] = []
    rejected_count = 0
    for option in options:
        if rating_fallback_reasons:
            option.risk_flags.append("market_only_rating_fallback")
        _apply_hard_vetoes(
            option,
            analysis,
            decision_cfg,
            use_model=not bool(rating_fallback_reasons),
        )
        if rating_fallback_reasons and (
            option.p_hit_market is None or option.p_no_loss_market is None
        ):
            option.hard_vetoes.append("rating_fallback_requires_market_probability")
        if option.hard_vetoes:
            rejected_count += 1
            continue
        _score_option(
            option,
            analysis,
            decision_cfg,
            market_only=bool(rating_fallback_reasons),
        )
        clean_options.append(option)

    if not clean_options:
        return _no_pick(["no_clean_option"], rejected_count=rejected_count)

    high_confidence = [
        option
        for option in clean_options
        if decision_cfg["min_odds"] <= option.odds <= decision_cfg["max_odds"]
        and (option.p_hit_safe or 0.0) >= decision_cfg["min_p_hit_safe"]
        and (option.p_no_loss_safe or 0.0) >= decision_cfg["min_p_no_loss_safe"]
    ]
    selected = _best_available_option(
        high_confidence or clean_options,
        decision_cfg,
    )
    if not high_confidence:
        _decorate_reason(selected, "best_available_main_market")
        selected.risk_flags.append("below_high_confidence_observation")
    if rating_fallback_reasons:
        _decorate_reason(selected, "market_consensus_rating_fallback")
        for reason in rating_fallback_reasons:
            if reason not in selected.risk_flags:
                selected.risk_flags.append(reason)

    return _make_pick(
        selected,
        analysis,
        observed,
        decision_cfg,
    )


def decide_match(
    analysis: MatchAnalysis,
    legacy_signals_or_cfg: Any,
    cfg: dict[str, Any] | None = None,
    observed_at: datetime | str | None = None,
    *,
    stale_sources: Iterable[Any] = (),
    hard_blockers: Iterable[Any] = (),
) -> dict[str, Any]:
    """Compatibility wrapper; legacy signals are accepted but never read."""

    if cfg is None:
        if not isinstance(legacy_signals_or_cfg, dict):
            raise TypeError("cfg is required")
        resolved_cfg = legacy_signals_or_cfg
    else:
        resolved_cfg = cfg
    return decide_match_pick(
        analysis,
        resolved_cfg,
        observed_at=observed_at,
        stale_sources=stale_sources,
        hard_blockers=hard_blockers,
    )
