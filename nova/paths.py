"""Path resolution for the Nova framework."""

from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent

AGENTS_DIR = FRAMEWORK_ROOT / "agents"
CONFIG_DIR = FRAMEWORK_ROOT / "config"
KNOWLEDGE_DIR = FRAMEWORK_ROOT / "knowledge"
PROJECTS_DIR = FRAMEWORK_ROOT / "projects"

MODELS_CONFIG = CONFIG_DIR / "models.json"
PIPELINES_DIR = CONFIG_DIR / "pipelines"
FRAMEWORK_PREFERENCES = CONFIG_DIR / "framework_preferences.yaml"


def get_project_root(project_name: str) -> Path:
    return PROJECTS_DIR / project_name


def get_project_docs(project_name: str) -> Path:
    return get_project_root(project_name) / "docs"


def get_project_logs(project_name: str) -> Path:
    return get_project_root(project_name) / "logs"


def get_project_preferences(project_name: str) -> Path:
    return get_project_root(project_name) / "preferences.yaml"


def get_project_src(project_name: str) -> Path:
    return get_project_root(project_name) / "src"
