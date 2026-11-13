"""Runtime model catalog and one confirmed next-turn selection."""

from typing import TYPE_CHECKING

from gigas.runtime.codex.models import ModelChoice, ModelSelection
from gigas.runtime.codex.transport import RpcError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcPeer


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise ValueError("Codexの応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError("Codexの応答形式が不正")


class ModelCatalog:
    """Validate choices and retain only the confirmed immutable selection."""

    def __init__(self, peer: "RpcPeer", emit: "Callable[[ModelSelection], None]") -> None:
        """Keep the RPC boundary and fact sink without loading models."""
        self._peer = peer
        self._emit = emit
        self._selection = ModelSelection((), "", "")

    @property
    def snapshot(self) -> ModelSelection:
        """Current choices and confirmed next-turn model and effort."""
        return self._selection

    def propose(self, model: str, effort: str) -> ModelSelection:
        """Validate a selection without changing the confirmed snapshot."""
        if not any(
            choice.model == model and effort in choice.efforts for choice in self._selection.choices
        ):
            raise ValueError("モデル・推論レベルを選択できない状態")
        return ModelSelection(self._selection.choices, model, effort)

    def commit(self, selection: ModelSelection) -> None:
        """Publish a validated selection after required runtime settings succeed."""
        self._selection = selection
        self._emit(selection)

    async def load(self, preferred_model: str, preferred_effort: str) -> str:
        """Load choices and defaults, returning a nonfatal selection diagnostic."""
        self._selection = ModelSelection((), "", "")
        models: list[JsonObject] = []
        cursor: str | None = None
        visited: set[str] = set()
        while True:
            try:
                response = _object(await self._peer.request("model/list", {"cursor": cursor}))
            except RpcError:
                self._emit(self._selection)
                return "モデル一覧を取得できない"
            data = response.get("data")
            if not isinstance(data, list):
                message = "モデル一覧が不正"
                raise ValueError(message)  # noqa: TRY004 -- Invalid JSON field, not API type.
            models.extend(_object(model) for model in data)
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                break
            cursor = _text(next_cursor)
            if cursor in visited:
                message = "モデル一覧のページが重複"
                raise ValueError(message)
            visited.add(cursor)
        choices = []
        for model in models:
            levels = model.get("supportedReasoningEfforts")
            if isinstance(levels, list):
                choices.append(
                    ModelChoice(
                        _text(model.get("model")),
                        tuple(_text(_object(level).get("reasoningEffort")) for level in levels),
                        _text(model.get("defaultReasoningEffort")),
                    )
                )
        catalog = tuple(choices)
        selected = next(
            (
                model
                for model in models
                if (
                    model.get("model") == preferred_model
                    if preferred_model
                    else model.get("isDefault") is True
                )
            ),
            None,
        )
        detail = ""
        selected_model = selected_effort = ""
        if selected is None:
            detail = "指定モデルを利用できない"
        else:
            selected_model = _text(selected.get("model"))
            effort = preferred_effort or _text(selected.get("defaultReasoningEffort"))
            efforts = selected.get("supportedReasoningEfforts")
            if not isinstance(efforts, list) or not any(
                isinstance(option, dict) and option.get("reasoningEffort") == effort
                for option in efforts
            ):
                detail = "指定推論レベルを利用できない"
            else:
                selected_effort = effort

        self.commit(ModelSelection(catalog, selected_model, selected_effort))
        return detail
