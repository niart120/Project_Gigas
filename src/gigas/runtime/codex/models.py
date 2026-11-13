"""Application-facing values independent of Codex wire schemas and Qt."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject, RpcId

type ConnectionPhase = Literal[
    "connecting",
    "login_required",
    "authenticating",
    "ready",
    "starting",
    "running",
    "waiting",
    "stopping",
    "stopped",
    "error",
    "closed",
]


@dataclass(frozen=True, slots=True)
class ConnectionOptions:
    """Settings for one Codex connection.

    Attributes:
        cwd: Existing absolute directory for the runtime process, separate from source code.
            Also used by threads when no preparation callback supplies an archive.
        model: Catalog model name, or empty to select the catalog default.
        effort: Catalog reasoning effort, or empty to select the model default.
        auth_method: Authentication preference. Only chatgpt is currently supported.
    """

    cwd: "Path"
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"


@dataclass(frozen=True, slots=True)
class ThreadConfiguration:
    """Prepared chat location and application-owned MCP overrides.

    Attributes:
        cwd: Existing absolute archive directory, independent of the process cwd.
        mcp_config: App-server config overrides for the application's MCP server.
            May contain credentials; omitted from repr and never emitted as events.
        memory_dir: Existing absolute cwd / "memory" directory for exclusive runtime
            writes, or None for read-only operation.
    """

    cwd: "Path"
    mcp_config: "JsonObject" = field(default_factory=dict, repr=False)
    memory_dir: "Path | None" = None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """Current application status derived from operations and runtime notifications.

    Attributes:
        phase: Connection or turn phase. Stopped means no pending start or active turn
            and completion of any started terminal cleanup. Interrupt receipt alone
            does not establish it.
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
        item_id: Stable item identifier within this connection.
        text: User-provided or runtime-provided conversation text.
        role: Source of the text.
        replace: Whether this text replaces earlier deltas for the same item.
    """

    item_id: str
    text: str
    role: Literal["assistant", "user"] = "assistant"
    replace: bool = False


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
        turn_id: Owning turn; may be an earlier completed turn, not the current one.
        steps: Complete ordered list. An empty tuple clears that turn's plan.
        explanation: Runtime-provided explanation, or empty when absent.
    """

    turn_id: str
    steps: tuple[PlanStep, ...]
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class ReasoningEvent:
    """A replacement snapshot containing only a reasoning item's public summary.

    Attributes:
        turn_id: Owning accepted turn, including earlier completed turns.
        item_id: Runtime item identity within that turn.
        summary: Public paragraphs in summary-index order; empty removes the summary.
            Raw reasoning content is never included.
    """

    turn_id: str
    item_id: str
    summary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """A replacement display value for a public command, file or MCP item.

    Attributes:
        turn_id: Owning accepted turn, including earlier completed turns.
        item_id: Runtime item identity within that turn.
        kind: Supported public item type.
        title: Actual command, changed paths, or server/tool name.
        status: Status reported by the runtime, not inferred game progress.
        details: Public textual result preview, command location or file diff.
            MCP arguments, image data, metadata and structured results are omitted.
            A preview may be truncated by the runtime.
    """

    turn_id: str
    item_id: str
    kind: Literal["commandExecution", "fileChange", "mcpToolCall"]
    title: str
    status: Literal["inProgress", "completed", "failed", "declined"]
    details: str = ""


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
        detail: Application-owned error reason, empty when no failure is known.
    """

    readiness: Literal["ready", "notConfigured", "updateRequired"] | None = None
    setting_up: bool = False
    detail: str = ""


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
    """One ordinary turn whose identity was accepted from the runtime.

    Emitted once, before events for that turn, even when completion precedes the
    start response. A submitted or canceled start request alone does not emit it.

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
    | TextEvent
    | PlanEvent
    | ReasoningEvent
    | ActivityEvent
    | LoginEvent
    | HumanRequest
    | RequestResolved
    | TurnStarted
)
