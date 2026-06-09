from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SnapshotStore(Protocol):
    def initialize(self) -> None:
        pass

    def put_snapshot(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
        stored_at: str | None = None,
    ) -> dict[str, Any]:
        pass

    def count_snapshots(self) -> int:
        pass

    def latest_snapshot(self) -> dict[str, Any] | None:
        pass
