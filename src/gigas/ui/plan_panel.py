"""Persistent public plan display with selection and reading-position retention."""

from typing import TYPE_CHECKING

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from gigas.runtime.codex.models import GoalState, PlanEvent


class PlanPanel(QFrame):
    """Display the current turn's published work without inferring a Goal.

    Goal and current-work headings remain present without received content.
    Goal and Plan each use a document retaining selection and the visible passage
    across updates. Selection belongs to its document, not both panes.

    Args:
        parent: Owning right-hand pane.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty plan without runtime or capture dependencies."""
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        goal_heading = QHBoxLayout()
        self.goal_heading = QLabel("目標", self)
        goal_heading.addWidget(self.goal_heading)
        goal_heading.addStretch()
        self.goal_status = QLabel(self)
        goal_heading.addWidget(self.goal_status)
        self.resume_button = QToolButton(self)
        self.resume_button.setText("再開")
        self.resume_button.setEnabled(False)
        self.clear_button = QToolButton(self)
        self.clear_button.setText("削除")
        self.clear_button.setEnabled(False)
        goal_heading.addWidget(self.resume_button)
        goal_heading.addWidget(self.clear_button)
        layout.addLayout(goal_heading)
        self.goal_value = Transcript(self, show_headings=False)
        self.goal_value.setAccessibleName("目標")
        self.goal_value.setFrameShape(QFrame.Shape.NoFrame)
        self.goal_value.setMaximumHeight(100)
        self.goal_value.setMinimumHeight(35)
        layout.addWidget(self.goal_value)
        self.set_goal(None)
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
        self.goal_value.setPalette(palette)
        layout.addWidget(self.view, 1)
        self.set_plan(None)

    def set_goal(self, goal: "GoalState | None") -> None:
        """Render the confirmed public objective without changing the turn plan.

        Args:
            goal: Current public Goal, or None after confirmed clearing.
        """
        labels = {
            "active": "進行中",
            "paused": "一時停止",
            "blocked": "停止",
            "usageLimited": "利用制限",
            "budgetLimited": "予算上限",
            "complete": "完了",
        }
        self.goal_status.setText(labels.get(goal.status, goal.status) if goal else "")
        self.goal_value.update_item("goal", goal.objective if goal else "—", kind="plan")

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
