"""Retained conversation documents, independent of connection readiness."""

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget

from gigas.ui.conversation_sidebar import ConversationHeading
from gigas.ui.conversation_transcript import ConversationTranscript
from gigas.ui.plan_panel import PlanPanel

if TYPE_CHECKING:
    from gigas.runtime.codex.history import HistoryEntry
    from gigas.runtime.codex.models import ConversationStatus, GoalState, PlanEvent


class ConversationDisplay(QWidget):
    """Own the displayed documents without making their Goal execution state."""

    def __init__(self, parent: QWidget) -> None:
        """Create empty documents and a separately placeable Goal/Plan panel."""
        super().__init__(parent)
        self.goal: GoalState | None = None
        self.thread_id: str | None = None
        self.plan: PlanEvent | None = None
        self.loading = False
        self._show_reasoning = True
        self._target: tuple[str, str] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = ConversationHeading(self)
        self.heading.hide()
        self.transcript = ConversationTranscript(self)
        self.heading.auto_scroll.toggled.connect(self.transcript.set_auto_scroll)
        self.transcript.setAccessibleName("会話")
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.heading)
        self.history_button = QPushButton("以前の履歴", self)
        self.history_button.hide()
        layout.addWidget(self.history_button)
        layout.addWidget(self.transcript, 1)
        self.plan_panel = PlanPanel(self)
        self.transcript.reasoning_visibility_changed.connect(self._reasoning_visibility)
        self.plan_panel.hide()  # MainWindow places this in the right pane.
        self._contents = (
            self.transcript,
            self.plan_panel.goal_value,
            self.plan_panel.goal_status,
            self.plan_panel.view,
            self.plan_panel.status,
        )
        for widget in self._contents:
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)

    def replace(
        self,
        project: str,
        title: str,
        thread_id: str | None,
        entries: "tuple[HistoryEntry, ...]",
        goal: "GoalState | None",
    ) -> None:
        """Replace documents only after the caller confirms a different target."""
        self.transcript.clear()
        self.transcript.prepend_items(entries, initial=True)
        self._target = project, title
        self.thread_id = thread_id
        self.clear_plan()
        self.plan_panel.reasoning.start(None)
        self.set_goal(goal)
        self.cancel_loading()

    def set_goal(self, goal: "GoalState | None") -> None:
        """Display a confirmed Goal or its confirmed absence."""
        self.goal = goal
        self.plan_panel.set_goal(goal)

    def set_heading(self, project: str, title: str) -> None:
        """Update confirmed metadata without replacing the conversation documents."""
        self._target = project, title
        if not self.loading:
            self.heading.set_target(project, title)
            self.heading.show()

    def begin_loading(self, project: str, title: str) -> None:
        """Show the candidate name while hiding the unchanged documents."""
        selected = self.transcript.textCursor()
        scroll = self.transcript.verticalScrollBar().value()
        self.loading = True
        self.heading.auto_scroll.setEnabled(False)
        self.plan_panel.reasoning.hide()
        self.heading.set_target(project, title, loading=True)
        self.heading.show()
        for widget in self._contents:
            widget.hide()
        # QTextBrowser clears selection while transferring focus on hide.
        # Restore after all widgets have completed that synchronous transition.
        self.transcript.setTextCursor(selected)
        self.transcript.verticalScrollBar().setValue(scroll)

    def cancel_loading(self) -> None:
        """Reveal the same documents and confirmed heading after a failed read."""
        self.loading = False
        self.heading.auto_scroll.setEnabled(True)
        self.plan_panel.reasoning.setVisible(self._show_reasoning)
        for widget in self._contents:
            widget.show()
        if self._target is not None:
            self.heading.set_target(*self._target)
            self.heading.show()
        else:
            self.heading.hide()

    def observe(
        self, previous: "ConversationStatus", current: "ConversationStatus", *, stopping: bool
    ) -> None:
        """Apply runtime facts without erasing a browsing Goal on an empty snapshot."""
        if current.turn_id != previous.turn_id:
            self.archive_plan(previous.turn_id)
            if current.turn_id is not None:
                self.clear_plan()
                self.plan_panel.reasoning.start(current.turn_id)
            elif previous.turn_id is not None:
                interrupted = current.stopping or previous.stopping or stopping
                self.plan_panel.reasoning.finish(interrupted=interrupted)
                self.plan_panel.set_plan(
                    self.plan, interrupted=interrupted, finished=not interrupted
                )
        if current.thread_id is not None:
            self.thread_id = current.thread_id
        if current.thread_id is not None or current.goal is not None:
            self.set_goal(current.goal)

    def _reasoning_visibility(self, visible: bool) -> None:
        self._show_reasoning = visible
        self.plan_panel.reasoning.setVisible(visible and not self.loading)

    def receive_plan(self, event: "PlanEvent", turn_id: str | None, *, interrupted: bool) -> None:
        """Keep the current Plan separate from retained plans of earlier turns."""
        if event.turn_id == turn_id:
            self.plan = event
            self.plan_panel.set_plan(event, interrupted=interrupted)
        else:
            self.transcript.update_plan(
                f"plan:{event.turn_id}",
                event.steps,
                event.explanation,
                inactive=True,
            )

    def clear_plan(self) -> None:
        """Clear only the current-work document."""
        self.plan = None
        self.plan_panel.set_plan(None)

    def archive_plan(self, turn_id: str | None) -> None:
        """Retain one inactive Plan snapshot when its owning turn leaves the view."""
        if self.plan is not None and self.plan.turn_id == turn_id:
            self.transcript.update_plan(
                f"plan:{self.plan.turn_id}",
                self.plan.steps,
                self.plan.explanation,
                inactive=True,
            )
