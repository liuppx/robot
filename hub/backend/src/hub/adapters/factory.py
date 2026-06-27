from __future__ import annotations

from pathlib import Path
from typing import Callable

from hub.adapters.base import RobotWorkspaceAdapter
from hub.adapters.messenger import MessengerWorkspaceAdapter
from hub.adapters.trader import TraderAdapter

WorkspaceAdapterFactory = Callable[[Path, Path], RobotWorkspaceAdapter]


WORKSPACE_ADAPTER_FACTORIES: dict[str, WorkspaceAdapterFactory] = {
    "trader": lambda root_dir, _repo_root: TraderAdapter(root_dir),
    "messenger": lambda root_dir, repo_root: MessengerWorkspaceAdapter(root_dir, repo_root),
}


def builtin_robot_root(repo_root: Path, robot_key: str) -> Path | None:
    custom_root = repo_root / "robots" / "custom"
    roots = {
        "trader": custom_root / "trader",
        "messenger": custom_root / "messenger",
    }
    return roots.get(robot_key)


def get_robot_workspace_adapter(repo_root: Path, robot_key: str) -> RobotWorkspaceAdapter | None:
    robot_root = builtin_robot_root(repo_root, robot_key)
    if robot_root is None:
        return None
    factory = WORKSPACE_ADAPTER_FACTORIES.get(robot_key)
    if factory is None:
        return None
    return factory(robot_root, repo_root)
