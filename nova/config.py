"""Configuration loading and management."""

import json
import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from nova.paths import MODELS_CONFIG, PIPELINES_DIR, FRAMEWORK_PREFERENCES


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ModelConfig(BaseModel):
    model: str
    provider: str = "anthropic"
    max_tokens: int = 4096
    temperature: float = 0.0


class ModelsConfig(BaseModel):
    roles: dict[str, ModelConfig]


class PipelineStep(BaseModel):
    role: str
    required: bool = True
    on_fail: str = "escalate"


class PipelineConfig(BaseModel):
    name: str
    description: str = ""
    steps: list[PipelineStep]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_models_config(path: Path | None = None) -> ModelsConfig:
    path = path or MODELS_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"Models config not found: {path}")
    data = json.loads(path.read_text())
    return ModelsConfig.model_validate(data)


def load_pipeline_config(name: str = "full", path: Path | None = None) -> PipelineConfig:
    path = path or (PIPELINES_DIR / f"{name}.json")
    if not path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {path}")
    data = json.loads(path.read_text())
    return PipelineConfig.model_validate(data)


def load_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


# ---------------------------------------------------------------------------
# Preference merge
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override wins for scalar values.

    Keys prefixed with must_ in base that conflict with override
    are collected and returned separately for human resolution.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def is_structured_pref(value: Any) -> bool:
    """Check if a preference uses the structured format (value/description/agent_instruction)."""
    return isinstance(value, dict) and "value" in value


def get_pref_value(value: Any) -> Any:
    """Extract the actual value from a preference (structured or plain)."""
    if is_structured_pref(value):
        return value["value"]
    return value


def find_must_conflicts(base: dict, override: dict, prefix: str = "") -> list[str]:
    """Find must_* keys in base that are overridden by project preferences."""
    conflicts: list[str] = []
    for key, value in base.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if key.startswith("must_") and key in override:
            base_val = get_pref_value(value)
            override_val = get_pref_value(override[key])
            if override_val != base_val:
                conflicts.append(full_key)
        elif isinstance(value, dict) and not key.startswith("must_") and isinstance(override.get(key), dict):
            conflicts.extend(find_must_conflicts(value, override[key], full_key))
    return conflicts


def merge_preferences(framework_path: Path | None = None, project_path: Path | None = None) -> dict[str, Any]:
    """Merge framework + project preferences. Project overrides framework.

    Raises ValueError if project tries to override must_* rules.
    """
    framework_path = framework_path or FRAMEWORK_PREFERENCES
    fw_prefs = load_preferences(framework_path)
    proj_prefs = load_preferences(project_path) if project_path else {}

    if not proj_prefs:
        return fw_prefs

    conflicts = find_must_conflicts(fw_prefs, proj_prefs)
    if conflicts:
        raise ValueError(
            f"Project preferences conflict with must_* framework rules: {conflicts}. "
            "These require human resolution."
        )

    return _deep_merge(fw_prefs, proj_prefs)
