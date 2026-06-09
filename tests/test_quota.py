import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.quota import load_quota_ledger, update_quota_from_headers


def test_update_quota_from_headers_records_theoddsapi_values():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"

        entry = update_quota_from_headers(
            path,
            "theoddsapi",
            {
                "x-requests-used": "3",
                "x-requests-remaining": "497",
                "x-requests-last": "3",
            },
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert entry["used"] == 3
        assert entry["remaining"] == 497
        assert entry["last"] == 3
        assert entry["observed_at"] == "2026-06-08T00:00:00+00:00"
        assert load_quota_ledger(path)["providers"]["theoddsapi"] == entry


def test_update_quota_from_headers_uses_estimate_when_headers_missing():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.json"

        update_quota_from_headers(
            path,
            "theoddsapi",
            {},
            estimated_last=3,
            observed_at="2026-06-08T00:00:00+00:00",
        )

        data = json.loads(path.read_text())
        assert data["providers"]["theoddsapi"]["last"] == 3
        assert data["providers"]["theoddsapi"]["remaining"] is None
