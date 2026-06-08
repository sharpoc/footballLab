from __future__ import annotations


def diff_rounds(prev: dict, curr: dict, cfg: dict) -> list[dict]:
    events: list[dict] = []
    prev_ids = set(prev)
    curr_ids = set(curr)

    for match_id in sorted(curr_ids - prev_ids):
        events.append({"type": "match_added", "match_id": match_id})
    for match_id in sorted(prev_ids - curr_ids):
        events.append({"type": "match_removed", "match_id": match_id})

    for match_id in sorted(prev_ids & curr_ids):
        psel = prev[match_id].get("selections", {})
        csel = curr[match_id].get("selections", {})
        for key in sorted(psel.keys() & csel.keys()):
            before, after = psel[key], csel[key]
            market, _, selection = key.partition("|")
            if before.get("grade") != after.get("grade"):
                events.append({
                    "type": "grade_change",
                    "match_id": match_id,
                    "market": market,
                    "selection": selection,
                    "from": before.get("grade"),
                    "to": after.get("grade"),
                })
            if abs((after.get("ev") or 0) - (before.get("ev") or 0)) >= cfg["ev_change"]:
                events.append({
                    "type": "ev_change",
                    "match_id": match_id,
                    "market": market,
                    "selection": selection,
                    "from": before.get("ev"),
                    "to": after.get("ev"),
                })
            old_odds, new_odds = before.get("odds"), after.get("odds")
            if old_odds and new_odds and abs(new_odds - old_odds) / old_odds >= cfg["odds_move"]:
                events.append({
                    "type": "odds_move",
                    "match_id": match_id,
                    "market": market,
                    "selection": selection,
                    "from": old_odds,
                    "to": new_odds,
                })
    return events
