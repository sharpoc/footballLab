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
    headers = dict(getattr(response, "headers", {}))

    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)

    return SportsFetchResult(
        status=int(getattr(response, "status", 200)),
        json_body=json_body,
        headers=headers,
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
    competitions = (
        [get_competition(competition_id) for competition_id in competition_ids]
        if competition_ids
        else list_competitions()
    )
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
