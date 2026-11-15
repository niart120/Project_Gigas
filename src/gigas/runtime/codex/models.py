"""Application-facing values independent of Codex wire schemas and Qt."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId

type ConnectionPhase = Literal[
    "connecting",
    "login_required",
    "authenticating",
    "ready",
    "error",
    "closed",
]

type MessagePhase = Literal["commentary", "partial_answer", "final_answer"]


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    """Application-owned options for runtime startup and new conversation execution.

    Attributes:
        web_search_enabled: Use live Web search when true; disable it when false.
        memory_enabled: Enable standard memory generation and use together.
            False does not delete existing memory files.
        approvals_reviewer: Delegate on-request approvals to auto_review, or ask the user.
            Delegation does not grant unconditional permission.
    """

    web_search_enabled: bool = True
    memory_enabled: bool = True
    approvals_reviewer: Literal["auto_review", "user"] = "auto_review"


@dataclass(frozen=True, slots=True)
class ConnectionOptions:
    """Settings for one Codex connection.

    Attributes:
        cwd: Existing absolute directory for the runtime process, separate from source code
            and the prepared chat archive.
        model: Catalog model name, or empty to select the catalog default.
        effort: Catalog reasoning effort, or empty to select the model default.
        auth_method: Authentication preference. Only chatgpt is currently supported.
        execution_settings: Immutable execution defaults for startup and new threads.
    """

    cwd: "Path"
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"
    execution_settings: ExecutionSettings = field(default_factory=ExecutionSettings)


@dataclass(frozen=True, slots=True)
class ThreadConfiguration:
    """Prepared workspace, Project assignment, and application-owned MCP overrides.

    Attributes:
        cwd: Prepared absolute workspace, independent of the process cwd.
            This directory is the thread's workspace and only normal write root.
        mcp_config: App-server config overrides for the application's MCP server.
            May contain credentials; omitted from repr and never emitted as events.
        project_id: Server-owned Project identity, or None for an unassigned thread.
    """

    cwd: "Path"
    mcp_config: "JsonObject" = field(default_factory=dict, repr=False)
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """Current application status derived from operations and runtime notifications.

    Attributes:
        phase: Connection/authentication state, independent of conversation execution.
        model: Selected model for future turns, empty before catalog validation.
        effort: Selected reasoning level for future turns, empty before validation.
        authenticated: Whether account/read confirmed the chosen authentication type.
        detail: A short application-owned reason safe for display.
        failure_id: Connection-local failure identity shared with an operation result;
            None when no correlated failure identity is supplied.
    """

    phase: ConnectionPhase
    model: str = ""
    effort: str = ""
    authenticated: bool = False
    detail: str = ""
    failure_id: int | None = None


@dataclass(frozen=True, slots=True)
class TextEvent:
    """User or assistant text intended for the conversation view.

    Attributes:
        item_id: Runtime item identifier, or a local ID for submitted user text.
        text: User-provided or runtime-provided conversation text.
        role: Source of the text.
        replace: Whether this text replaces earlier deltas for the same item.
        turn_id: Containing public turn, or None for locally submitted text.
        phase: Public assistant phase, or None when absent or unknown.
        completed: Whether an item/completed notification supplied this text.
    """

    item_id: str
    text: str
    role: Literal["assistant", "user"] = "assistant"
    replace: bool = False
    turn_id: str | None = None
    phase: MessagePhase | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step and its runtime-reported public plan status.

    Attributes:
        step: Model-provided step description.
        status: Runtime-provided progress, unchanged by application interruption.
    """

    step: str
    status: Literal["pending", "inProgress", "completed"]


@dataclass(frozen=True, slots=True)
class PlanEvent:
    """A complete replacement of one accepted turn's public plan.

    Attributes:
        turn_id: Current accepted turn, before its completion notification.
        steps: Complete ordered list. An empty tuple clears that turn's plan.
        explanation: Runtime-provided explanation, or empty when absent.
    """

    turn_id: str
    steps: tuple[PlanStep, ...]
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class SummaryPart:
    """One public summary paragraph with its original nonnegative wire index."""

    index: int
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningEvent:
    """A replacement snapshot containing only a reasoning item's public summary.

    Attributes:
        turn_id: Current accepted turn, before its completion notification.
        item_id: Runtime item identity within that turn.
        summary: Indexed public paragraphs in index order; empty removes the summary.
            Raw reasoning content is never included.
        completed: Whether an item/completed notification supplied this snapshot.
    """

    turn_id: str
    item_id: str
    summary: tuple[SummaryPart, ...]
    completed: bool = False


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """A replacement display value for a public command, file, MCP or Web item.

    Attributes:
        turn_id: Current accepted turn, before its completion notification.
        item_id: Runtime item identity within that turn.
        kind: Supported public item type.
        title: Actual command, changed paths, server/tool name, or Web search query.
        status: Public item status or notification lifecycle; None when a saved Web
            item has no lifecycle status. Never inferred game progress.
        details: Public textual result preview or file diff, separate from invocation data.
            Image data, metadata and structured results are omitted.
            A preview may be truncated by the runtime.
        cwd: Command working directory when supplied.
        exit_code: Command exit code when supplied; zero is retained.
        arguments: Independent copy of public MCP arguments, for expanded details.
        completed: Received item lifecycle, or None when unavailable in saved history.
    """

    turn_id: str
    item_id: str
    kind: Literal["commandExecution", "fileChange", "mcpToolCall", "webSearch"]
    title: str
    status: Literal["inProgress", "completed", "failed", "declined"] | None
    details: str = ""
    cwd: str | None = None
    exit_code: int | None = None
    arguments: "JsonValue" = field(default=None, repr=False)
    completed: bool | None = None


@dataclass(frozen=True, slots=True)
class LoginEvent:
    """A login URL the user can choose to open in a browser.

    Attributes:
        url: HTTP or HTTPS URL from the explicit ChatGPT login operation.
    """

    url: str


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """Public ChatGPT account identity; absent fields remain unknown."""

    email: str | None = None
    plan_type: str | None = None
    workspace_name: str | None = None
    authenticated: bool = False


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """Reported usage percentage, window length in minutes and Unix reset seconds."""

    used_percent: int
    window_minutes: int | None = None
    resets_at: int | None = None


@dataclass(frozen=True, slots=True)
class UsageCredits:
    """Reported additional credits, independent of included-usage permission."""

    has_credits: bool
    unlimited: bool
    balance: str | None = None


@dataclass(frozen=True, slots=True)
class UsageBucket:
    """One backend quota bucket without inferred names, windows or balances."""

    limit_id: str | None = None
    limit_name: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: UsageCredits | None = None


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """Account usage read result, separate from authentication and turn state.

    Attributes:
        buckets: Last successfully fetched buckets; retained after read failure.
        ordinary_usage_allowed: Explicit backend permission, or None when unknown.
            A known False persists through null or failed reads until an explicit
            True is received; elapsed reset times never establish recovery.
        available: Whether the most recent read succeeded. False does not by itself
            prohibit sending or stop execution.
    """

    buckets: tuple[UsageBucket, ...] = ()
    ordinary_usage_allowed: bool | None = None
    available: bool = False


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    """Windows sandbox readiness and an explicitly requested setup operation.

    Attributes:
        readiness: Last public readiness result, or None when unavailable.
        setting_up: Whether this connection is awaiting setup acceptance/completion.
            Closing the connection ends local waiting, not the OS-owned setup.
        reconnect_required: Setup succeeded but this connection must be reopened.
        detail: Public setup failure or application-owned readiness error reason;
            empty when no failure is known.
    """

    readiness: Literal["ready", "notConfigured", "updateRequired"] | None = None
    setting_up: bool = False
    detail: str = ""
    reconnect_required: bool = False


@dataclass(frozen=True, slots=True)
class QuestionOption:
    """One model-provided choice with its description."""

    label: str
    description: str


@dataclass(frozen=True, slots=True)
class Question:
    """One user question retaining its identifier and allowed answer controls.

    Attributes:
        id: Key used to send the answer to Codex.
        header: Short model-provided heading.
        question: Model-provided question text.
        options: Selectable model-provided choices; empty allows text input.
        is_other: Whether text outside the supplied options is allowed.
    """

    id: str
    header: str
    question: str
    options: tuple[QuestionOption, ...] = ()
    is_other: bool = False


@dataclass(frozen=True, slots=True)
class HumanRequest:
    """A live question or approval, identified by a connection-unique local token.

    Attributes:
        token: Opaque local identity passed to answer; never reuse it after resolution.
        rpc_id: Original RPC identity, preserving integer versus string.
        thread_id: Runtime conversation containing the request.
        turn_id: Runtime turn containing the request, including nonblocking questions.
        kind: Question, command approval or file-change approval.
        questions: Structured questions; empty for approvals.
        details: Runtime-provided command, paths, reason or proposed diff for display.
        decisions: Supported one-time decision names; no automatic default approval.
        is_blocking: Whether the runtime is waiting for this request to finish.
    """

    token: str
    rpc_id: "RpcId"
    thread_id: str
    turn_id: str
    kind: Literal["question", "command", "file"]
    questions: tuple[Question, ...] = ()
    details: str = ""
    decisions: tuple[str, ...] = ()
    is_blocking: bool = True


@dataclass(frozen=True, slots=True)
class RequestResolved:
    """A local request token that no longer accepts answers.

    Resolution includes reply submission, runtime dismissal, stop or disconnection;
    it does not assert approval or successful execution of the requested operation.
    """

    token: str


@dataclass(frozen=True, slots=True)
class TurnStarted:
    """An ordinary or autonomous turn adopted from a server start notification.

    Emitted once, before events for that turn, even when completion precedes the
    start response. Unknown item, question and completion notifications do not
    establish a turn. A submitted or canceled start request alone does not emit it.

    Attributes:
        turn_id: Runtime turn identity within this connection's conversation.
    """

    turn_id: str


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A catalog model and its supported reasoning levels."""

    model: str
    efforts: tuple[str, ...]
    default_effort: str


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Validated choices and selection for the next ordinary turn."""

    choices: tuple[ModelChoice, ...]
    model: str
    effort: str


type GoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]


@dataclass(frozen=True, slots=True)
class GoalState:
    """Public Goal state and observed cumulative usage, without inferred completion."""

    objective: str
    status: GoalStatus
    tokens_used: int = 0
    time_used_seconds: int = 0
    token_budget: int | None = None


@dataclass(frozen=True, slots=True)
class ConversationStatus:
    """Immutable display projection of server facts and owned operation tasks.

    No field grants input or chooses between starting and steering a turn.
    Sending and stopping are projected from live tasks, not stored phases.
    """

    thread_id: str | None = None
    turn_id: str | None = None
    goal: GoalState | None = None
    sending: bool = False
    stopping: bool = False
    waiting_for_answer: bool = False


def can_submit(connection: SessionStatus, conversation: ConversationStatus) -> bool:
    """Whether an authenticated conversation can accept one new send operation.

    Model/sandbox readiness is checked for execution, not for selecting a valid
    model. Answers use their original request and are independent of this slot.
    """
    return (
        connection.phase == "ready"
        and connection.authenticated
        and not conversation.sending
        and not conversation.stopping
    )


@dataclass(frozen=True, slots=True)
class GoalEvent:
    """A confirmed Goal snapshot, or None after confirmed removal."""

    goal: GoalState | None


@dataclass(frozen=True, slots=True)
class DialogueEvent:
    """A public final or partial item snapshot suitable for asynchronous recording."""

    kind: str
    payload: "JsonObject"
    turn_id: str | None = None


type ConnectionEvent = (
    AccountInfo
    | UsageEvent
    | SandboxEvent
    | GoalEvent
    | DialogueEvent
    | ModelSelection
    | SessionStatus
    | ConversationStatus
    | TextEvent
    | PlanEvent
    | ReasoningEvent
    | ActivityEvent
    | LoginEvent
    | HumanRequest
    | RequestResolved
    | TurnStarted
)
