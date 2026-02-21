"""Prompt composition engine for agent invocations."""

from pathlib import Path
from typing import Any

from nova.config import get_pref_value, is_structured_pref
from nova.models import AgentRole, Task
from nova.paths import AGENTS_DIR, KNOWLEDGE_DIR


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

def load_agent_template(role: AgentRole) -> str:
    path = AGENTS_DIR / f"{role.value}.md"
    if not path.exists():
        raise FileNotFoundError(f"Agent template not found: {path}")
    return path.read_text()


# ---------------------------------------------------------------------------
# Preference extraction
# ---------------------------------------------------------------------------

def extract_preference_instructions(preferences: dict[str, Any]) -> list[str]:
    """Walk merged preferences and collect all agent_instruction values."""
    instructions: list[str] = []
    for category, rules in preferences.items():
        if not isinstance(rules, dict):
            continue
        for key, value in rules.items():
            if is_structured_pref(value) and value.get("agent_instruction"):
                instructions.append(f"[{category}.{key}] {value['agent_instruction']}")
    return instructions


# ---------------------------------------------------------------------------
# Knowledge loading
# ---------------------------------------------------------------------------

def load_knowledge() -> str:
    """Load relevant knowledge (lessons, failed patterns, escalation patterns)."""
    sections: list[str] = []

    lessons_dir = KNOWLEDGE_DIR / "lessons"
    if lessons_dir.exists():
        for f in sorted(lessons_dir.glob("*.md")):
            sections.append(f.read_text().strip())

    for filename in ("escalation-patterns.md", "failed-patterns.md"):
        path = KNOWLEDGE_DIR / filename
        if path.exists():
            sections.append(path.read_text().strip())

    return "\n\n".join(sections) if sections else ""


# ---------------------------------------------------------------------------
# Task context
# ---------------------------------------------------------------------------

def compose_task_context(
    task: Task,
    spec_content: str = "",
    plan_content: str = "",
    diff: str = "",
    file_tree: str = "",
    prior_feedback: str = "",
) -> str:
    """Format the task-specific context block for pipeline agents."""
    parts: list[str] = []

    parts.append(f"## Current Task\n\n"
                 f"- **ID:** {task.id}\n"
                 f"- **Title:** {task.title}\n"
                 f"- **Description:** {task.description}\n"
                 f"- **Attempt:** {task.attempt}\n"
                 f"- **Version:** {task.version}")

    if task.acceptance_criteria:
        criteria = "\n".join(f"  - {c}" for c in task.acceptance_criteria)
        parts.append(f"- **Acceptance Criteria:**\n{criteria}")

    if task.dependencies:
        deps = ", ".join(task.dependencies)
        parts.append(f"- **Dependencies:** {deps}")

    if spec_content:
        parts.append(f"\n## Approved Spec\n\n{spec_content}")

    if plan_content:
        parts.append(f"\n## Approved Plan\n\n{plan_content}")

    if file_tree:
        parts.append(f"\n## Project File Tree\n\n```\n{file_tree}\n```")

    if diff:
        parts.append(f"\n## Code Changes (Git Diff)\n\n```diff\n{diff}\n```")

    if prior_feedback:
        parts.append(f"\n## Prior Attempt Feedback\n\n"
                     f"The previous attempt was rejected. Address these issues:\n\n"
                     f"{prior_feedback}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main composer
# ---------------------------------------------------------------------------

CONTEXT_WINDOW = {
    AgentRole.PLANNER: 180_000,
    AgentRole.CODER: 180_000,
    AgentRole.QA: 180_000,
    AgentRole.DISTILLER: 180_000,
}

CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def compose_system_prompt(
    role: AgentRole,
    preferences: dict[str, Any],
    task: Task | None = None,
    spec_content: str = "",
    plan_content: str = "",
    diff: str = "",
    file_tree: str = "",
    prior_feedback: str = "",
    extra_context: str = "",
) -> str:
    """Assemble the full system prompt for an agent invocation.

    Sections are added in priority order (highest first).
    If the prompt exceeds the token budget, lower-priority sections are trimmed.
    """
    template = load_agent_template(role)

    pref_instructions = extract_preference_instructions(preferences)
    pref_block = ""
    if pref_instructions:
        formatted = "\n".join(f"- {inst}" for inst in pref_instructions)
        pref_block = f"\n\n## Active Preferences\n\nYou MUST follow these instructions:\n\n{formatted}"

    knowledge = load_knowledge()
    knowledge_block = ""
    if knowledge:
        knowledge_block = f"\n\n## Knowledge Base\n\n{knowledge}"

    task_block = ""
    if task:
        task_block = "\n\n" + compose_task_context(
            task=task,
            spec_content=spec_content,
            plan_content=plan_content,
            diff=diff,
            file_tree=file_tree,
            prior_feedback=prior_feedback,
        )

    extra_block = ""
    if extra_context:
        extra_block = f"\n\n## Additional Context\n\n{extra_context}"

    # Assemble in priority order: template > preferences > task > extra > knowledge
    # Knowledge is lowest priority and gets trimmed first if over budget
    sections = [
        ("template", template),
        ("preferences", pref_block),
        ("task", task_block),
        ("extra", extra_block),
        ("knowledge", knowledge_block),
    ]

    budget = CONTEXT_WINDOW.get(role, 180_000)
    # Reserve 30% of context window for the model's output
    prompt_budget_tokens = int(budget * 0.7)

    prompt = ""
    for name, section in sections:
        if not section:
            continue
        candidate = prompt + section
        if _estimate_tokens(candidate) > prompt_budget_tokens:
            remaining_chars = (prompt_budget_tokens - _estimate_tokens(prompt)) * CHARS_PER_TOKEN_ESTIMATE
            if remaining_chars > 200:
                prompt += section[:remaining_chars] + "\n\n[... truncated due to context budget ...]"
            break
        prompt = candidate

    return prompt.strip()
