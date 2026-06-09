# Plan 4 Research Ledger UI Design

- Date: 2026-06-09
- Status: Accepted for implementation planning
- Scope: first public-facing UI for the 2026 World Cup research site
- Selected Product Design direction: Research Ledger
- Selected visual target: `/Users/eagod/.codex/generated_images/019ea799-53e9-7221-955b-8426e2237822/ig_0db4522c5e4b94c0016a278789984c8191aae7d2bd11524177.png`

## Goal

Plan 4 turns the current local preview into a polished, public-facing research page. The first version helps a visitor quickly scan upcoming World Cup match value signals while understanding that the page is a research tool, not betting advice.

The successful first screen should answer:

1. Which upcoming matches currently have model-market signal?
2. How strong is each signal?
3. Is the data fresh enough to trust?
4. What method produced the signal?
5. What caveats should a reader keep in mind?

## Product Positioning

This project is a 2026 World Cup analysis and research site. It should feel like an analyst ledger: clear, restrained, evidence-oriented, and public-safe.

The UI must not feel like a betting slip, tipster page, casino product, or social hype feed.

Hard rules:

- Keep the research disclaimer visible.
- Do not show stake, bet amount, unit size, bankroll, payout, or profit fields.
- Do not include betting CTAs such as bet now, place bet, follow pick, tail, or lock.
- Do not frame model output as guaranteed advice.
- Do not hide stale data or missing odds.

## Chosen Concept

Use the Research Ledger direction:

- Top header with product name, last updated time, refresh/readiness state, and disclaimer.
- Summary metric band for upcoming matches, signal counts, stale sources, and overall data quality.
- Main signal ledger table grouped by date.
- Right rail for methodology, source health, caveats, and time zone note.
- Calm white/off-white base, slate text, subtle separators, and restrained green/amber/red status accents.

This concept is slightly slower to scan than the pure Signal Terminal table, but it is better for a public site because it explains why the page is credible and keeps risk context visible.

## Non-Goals

- No account system.
- No personalized watchlist.
- No full match detail page in the first version.
- No complex charts.
- No live auto-refresh.
- No odds history chart.
- No bankroll, staking, or wager management.
- No push notifications.
- No new modeling logic.
- No backend or cloud deployment as part of this design spec.

## Page Structure

### Header

Content:

- Product name: `World Cup 2026`
- Surface name: `Research Ledger`
- Last updated timestamp in UTC.
- Small refresh/readiness icon or label.
- Disclaimer: `Research only, not betting advice.`

Behavior:

- Static for the first version.
- Timestamp comes from snapshot `snapshot_at` or run metadata.
- Do not expose API keys, quota internals beyond safe summary, or raw source payloads.

### Summary Metric Band

Show compact metrics:

- Upcoming matches.
- Strong signals, mapped to Grade A or stronger.
- Watch signals, mapped to Grade B.
- Weak / no edge, mapped to Grade C or lower.
- Stale sources count.
- Overall data quality badge.

Data mapping:

- Upcoming matches can use snapshot counts or projected match rows.
- Signal counts come from per-match `signals`.
- Stale source count comes from `data_quality.stale_sources`.
- Overall quality is:
  - `GOOD` when no source errors and no stale sources.
  - `WARN` when stale sources, missing odds, missing Elo, or time mismatches exist.
  - `ATTENTION` when source errors exist or the latest snapshot is missing.

### Signal Ledger

This is the primary content.

Columns:

| Column | Source / rule |
|---|---|
| Matchup | home and away team names |
| Kickoff UTC | fixture kickoff |
| Market | signal market type, such as `1X2 - Home`, `O/U 2.5 - Over`, or AH line |
| Model Prob | model fair probability when available |
| Market Prob | devigged market probability when available |
| EV / Edge | signal EV and edge, formatted as percentages |
| Grade | existing signal grade |
| Freshness | odds/source freshness or stale flag |
| Why this is a signal | short generated explanation from existing model/market fields |

Rows should be grouped by kickoff date. Keep row height compact but readable. Use row separators instead of separate cards for every match.

For the first version, `why this is a signal` should be deterministic and template-based, not AI-generated at runtime. Example rules:

- Positive 1X2 edge: `Model probability is above the devigged market probability.`
- Positive totals edge: `Model total-goals distribution differs from the market total.`
- Asian handicap edge: `Settlement EV is positive at the current handicap line.`
- Stale or incomplete data: `Signal is capped because one or more inputs are stale or missing.`

### Controls

Fast static MVP controls:

- Segmented view: `All`, `Strong (A)`, `Watch (B)`, `Weak (C)`.
- Group selector.
- Market selector.
- Date range selector for `Next 14 days`.
- Search by team name.
- Simple pagination or rows-per-page.

Implementation can start with client-side filtering over the loaded projected dataset.

### Right Rail

Sections:

1. Methodology
   - Elo Ratings.
   - Poisson Goal Model.
   - Market De-vig.

2. Source Health
   - openfootball fixtures.
   - Elo ratings.
   - The Odds API.
   - Quota / usage.

3. Caveats
   - Model probabilities are estimates, not guarantees.
   - Market prices can move for reasons not captured by the model.
   - Injuries, lineups, weather, and late news can change edge.

4. Time Zone Note
   - `All times in UTC` for MVP.

The right rail should remain explanatory, not promotional.

## Data Requirements

The first implementation should use existing local/API-safe data only:

- `GET /api/matches` for match rows.
- `GET /api/snapshot/latest` only if needed for summary metrics and source health.
- Existing snapshot fields for `counts`, `data_quality`, `run`, `model`, `market`, and `signals`.

If a field is missing, show a neutral unavailable state rather than inventing values.

Do not add public UI fields for stake, bet amount, bankroll, payout, or unit size.

## Component Boundaries

Recommended frontend components:

- `ResearchLedgerPage`
- `LedgerHeader`
- `SummaryMetrics`
- `LedgerControls`
- `SignalLedgerTable`
- `GradeBadge`
- `FreshnessBadge`
- `SourceHealthRail`
- `MethodologyPanel`
- `CaveatsPanel`
- `EmptyState`
- `DataQualityBanner`

Keep data formatting helpers separate from visual components:

- grade to label/color.
- percent formatting.
- timestamp formatting.
- quality status derivation.
- deterministic signal explanation builder.

## Layout Behavior

Desktop:

- Header fixed at top of content flow, not sticky for MVP.
- Summary band spans full width.
- Main area uses a wide ledger table and a right rail.
- Right rail can be about 280-340 px wide.

Tablet:

- Right rail moves below the table or collapses into sections below controls.
- Table can horizontally scroll when needed.

Mobile:

- Keep the first mobile version simple.
- Header, metrics, controls, then ledger rows as compact stacked rows.
- Right rail sections appear after the ledger.

No separate mobile app design is required for this phase, but the page must remain usable on narrow screens.

## Visual Rules

- Use a white or near-white base.
- Use slate/dark navy text for hierarchy.
- Use green for positive/healthy status, amber for watch/warn, red only for errors or negative edge.
- Avoid purple-heavy, dark-blue-heavy, beige-heavy, or decorative gradient palettes.
- Avoid large hero sections.
- Avoid cards inside cards.
- Avoid decorative orbs, bokeh, and SVG-only decoration.
- Use icons only where they improve scanning: status, time, source health, search, filters.
- Keep typography compact and readable; body text should sit around 14-16 px.

## Error and Empty States

Required states:

- Snapshot missing: show `No analysis snapshot available yet`.
- No matching filters: show `No signals match these filters`.
- Missing odds: visible warning in source health and affected rows.
- Stale sources: visible warning in summary and affected rows.
- API read failure: show a non-secret error message and keep the disclaimer visible.

Error messages must not include API keys, HMAC secrets, database URLs, raw headers, cookies, or tokens.

## Testing Strategy

Before implementation is considered complete:

1. Unit-test deterministic formatting helpers and signal explanation builder.
2. Test that the page renders from a representative local snapshot.
3. Test that public rows contain no stake, bet amount, bankroll, payout, or unit fields.
4. Test empty and stale data states.
5. Run the existing Python suite to ensure API/data contracts still pass.
6. Use browser/screenshot QA to confirm the first viewport is not blank and layout works on desktop and mobile widths.

## Acceptance Criteria

Plan 4 design is implementation-ready when:

- The Research Ledger concept is accepted.
- The page structure, columns, controls, right rail, and data mapping are fixed.
- Safety constraints are explicit.
- Missing/stale data behavior is defined.
- Testing criteria are clear.
- The next step can be a separate implementation plan.

Plan 4 implementation will be complete later when:

- A real frontend page exists.
- It renders current local/API-safe data.
- It has the required disclaimer and excludes money-management fields.
- It passes automated tests and browser QA.
- It is still local-only until deployment is separately confirmed.
