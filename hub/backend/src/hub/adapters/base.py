from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from hub.models import (
    RobotWorkspaceActionResponse,
    RobotWorkspaceConfigUpdateResponse,
    RobotWorkspaceSummaryResponse,
)


class RobotWorkspaceAdapter(Protocol):
    root_dir: Path

    def exists(self) -> bool: ...

    def summary(self) -> RobotWorkspaceSummaryResponse: ...

    def update_config(
        self,
        broker: str,
        strategy: dict[str, Any],
        strategy_id: str | None = None,
    ) -> RobotWorkspaceConfigUpdateResponse: ...

    def run_action(self, action: str) -> RobotWorkspaceActionResponse: ...
