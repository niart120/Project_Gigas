"""Translate application execution options into the runtime's public config keys."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gigas.runtime.codex.models import ExecutionSettings
    from gigas.runtime.codex.transport import JsonObject


def config_overrides(settings: "ExecutionSettings") -> "JsonObject":
    """Return fresh startup/thread overrides without reading or changing stored settings."""
    return {
        "web_search": "live" if settings.web_search_enabled else "disabled",
        "features.memories": settings.memory_enabled,
        "memories.generate_memories": settings.memory_enabled,
        "memories.use_memories": settings.memory_enabled,
        "memories.disable_on_external_context": False,
        "features.apps": False,
        "features.plugins": False,
        "features.hooks": False,
        "features.codex_hooks": False,
        "features.plugin_hooks": False,
        "notify": [],
        "features.default_mode_request_user_input": True,
        "tools.update_plan.enabled": True,
        "approval_policy": "on-request",
        "approvals_reviewer": settings.approvals_reviewer,
        "sandbox_mode": "workspace-write",
        "sandbox_workspace_write.writable_roots": [],
        "sandbox_workspace_write.network_access": False,
        "sandbox_workspace_write.exclude_tmpdir_env_var": True,
        "sandbox_workspace_write.exclude_slash_tmp": True,
    }
