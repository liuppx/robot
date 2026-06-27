from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hub.models import RobotListItem, RobotListResponse


@dataclass(slots=True)
class RobotDescriptor:
    key: str
    display_name: str
    root_dir: Path
    category: str = "custom"


class RobotRegistry:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def builtins(self) -> list[RobotDescriptor]:
        robots_root = self.repo_root / "robots" / "custom"
        return [
            RobotDescriptor("trader", "交易员", robots_root / "trader"),
            RobotDescriptor("messenger", "信使", robots_root / "messenger"),
        ]

    def list_items(self) -> RobotListResponse:
        items = [
            RobotListItem(
                key=robot.key,
                display_name=robot.display_name,
                category=robot.category,
                path=str(robot.root_dir),
                available=robot.root_dir.exists(),
            )
            for robot in self.builtins()
        ]
        return RobotListResponse(items=items)
