"""Persistent public plan display with selection and reading-position retention."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from gigas.runtime.codex.models import PlanEvent


class PlanPanel(QFrame):
    """Display the current turn's published work without inferring a Goal.

    Goal and current-work headings remain present without received content.
    The Goal value stays empty until Goal integration is provided. The plan's
    single document retains selection and the visible passage across updates.

    Args:
        parent: Owning right-hand pane.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty plan without runtime or capture dependencies."""
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        self.goal_heading = QLabel("目標", self)
        layout.addWidget(self.goal_heading)
        self.goal_value = QLabel("—", self)
        self.goal_value.setAccessibleName("目標")
        self.goal_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.goal_value)
        layout.addSpacing(6)
        heading = QHBoxLayout()
        self.heading = QLabel("今回の作業", self)
        heading.addWidget(self.heading)
        heading.addStretch()
        self.status = QLabel(self)
        heading.addWidget(self.status)
        layout.addLayout(heading)
        self.view = Transcript(self, show_headings=False)
        self.view.setAccessibleName("今回の作業")
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        palette = self.view.palette()
        palette.setColor(QPalette.ColorRole.Base, self.palette().color(QPalette.ColorRole.Window))
        self.view.setPalette(palette)
        layout.addWidget(self.view, 1)
        self.set_plan(None)

    def set_plan(
        self,
        event: "PlanEvent | None",
        *,
        interrupted: bool = False,
        finished: bool = False,
    ) -> None:
        """Display one complete public snapshot, preserving the existing document.

        Args:
            event: Accepted current-turn plan; None clears the displayed snapshot.
            interrupted: Show interruption separately without changing public step
                states. In-progress emphasis is suppressed for this retained plan.
            finished: Show normal turn completion without completing unfinished
                public steps. In-progress emphasis is suppressed.
        """
        steps = event.steps if event is not None else ()
        self.status.setText(
            "中断"
            if interrupted and event is not None
            else "完了"
            if steps and all(step.status == "completed" for step in steps)
            else "終了"
            if finished and event is not None
            else ""
        )
        if event is not None and steps:
            self.view.update_plan(
                "current", steps, event.explanation, inactive=interrupted or finished
            )
        else:
            explanation = event.explanation if event is not None else ""
            self.view.update_item(
                "current", f"{explanation}\n\n—" if explanation else "—", kind="plan"
            )
