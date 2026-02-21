"""Pipeline runner — executes tasks through the Coder → Lint/Build → QA pipeline."""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from nova.agent import call_agent_single_shot
from nova.config import ModelConfig, ModelsConfig
from nova.models import (
    AgentRole,
    AgentStatus,
    CoderOutput,
    CommandResult,
    DistillerOutput,
    Escalation,
    FileOperation,
    PlannerOutput,
    ProjectPhase,
    ProjectState,
    QAOutput,
    RunLog,
    Task,
    TaskState,
)

from nova.paths import get_project_docs, get_project_logs, get_project_src
from nova.prompt import compose_system_prompt
from nova.state import all_tasks_done, save_state, transition_phase, transition_task

console = Console()
MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# File tree builder
# ---------------------------------------------------------------------------

def build_file_tree(root: Path, prefix: str = "") -> str:
    """Build a text representation of the directory tree for Coder context."""
    if not root.exists():
        return "(empty — no files yet)"

    SKIP = {".git", "node_modules", "dist", ".next", "__pycache__", ".venv", ".cache", ".DS_Store"}
    entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    entries = [e for e in entries if e.name not in SKIP]

    if not entries:
        return "(empty — no files yet)"

    lines: list[str] = []
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if is_last else "│   "
            subtree = build_file_tree(entry, prefix + extension)
            if subtree and not subtree.startswith("(empty"):
                lines.append(subtree)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def apply_file_operations(operations: list[FileOperation], project_src: Path) -> list[str]:
    """Apply file create/edit/delete operations to disk. Returns affected paths."""
    affected: list[str] = []

    for op in operations:
        target = project_src / op.path

        if op.action in ("create", "edit"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op.content)
            affected.append(op.path)
            label = "created" if op.action == "create" else "updated"
            console.print(f"  [green]{label}:[/green] {op.path}")

        elif op.action == "delete":
            if target.exists():
                target.unlink()
                affected.append(op.path)
                console.print(f"  [red]deleted:[/red] {op.path}")
            else:
                console.print(f"  [yellow]skip delete (not found):[/yellow] {op.path}")

    return affected


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def execute_commands(commands: list[str], working_dir: Path) -> list[CommandResult]:
    """Execute shell commands in the project directory."""
    results: list[CommandResult] = []

    for cmd in commands:
        console.print(f"  [cyan]$[/cyan] {cmd}")
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                timeout=300,
            )
            result = CommandResult(
                command=cmd,
                exit_code=proc.returncode,
                stdout=proc.stdout[-2000:] if proc.stdout else "",
                stderr=proc.stderr[-2000:] if proc.stderr else "",
            )
            results.append(result)

            if proc.returncode == 0:
                console.print(f"    [green]✓[/green] exit 0")
            else:
                console.print(f"    [red]✗[/red] exit {proc.returncode}")
                if proc.stderr:
                    console.print(f"    [dim]{proc.stderr[:500]}[/dim]")

        except subprocess.TimeoutExpired:
            results.append(CommandResult(
                command=cmd,
                exit_code=-1,
                stderr="Command timed out after 300 seconds",
            ))
            console.print(f"    [red]✗ timeout[/red]")

    return results


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------

def save_run_log(log: RunLog, project_name: str) -> Path:
    """Write a structured run log to logs/runs/."""
    logs_dir = get_project_logs(project_name) / "runs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{log.task_id}_{log.role.value}_{log.attempt}.json"
    path = logs_dir / filename
    path.write_text(log.model_dump_json(indent=2))
    return path


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

def _load_artifact(project_name: str, subdir: str, filename: str) -> str:
    path = get_project_docs(project_name) / subdir / filename
    return path.read_text() if path.exists() else ""


# ---------------------------------------------------------------------------
# Diff builder (for Reviewer context)
# ---------------------------------------------------------------------------

def build_file_diff(operations: list[FileOperation]) -> str:
    """Build a readable diff summary from the Coder's file operations."""
    if not operations:
        return "(no file changes)"

    parts: list[str] = []
    for op in operations:
        if op.action == "delete":
            parts.append(f"--- DELETED: {op.path} ---")
        elif op.action in ("create", "edit"):
            label = "NEW FILE" if op.action == "create" else "MODIFIED"
            header = f"--- {label}: {op.path} ---"
            content = op.content
            if len(content) > 3000:
                content = content[:3000] + f"\n... (truncated, {len(op.content)} chars total)"
            parts.append(f"{header}\n{content}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Dependent file scanner
# ---------------------------------------------------------------------------

SCAN_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".py", ".go", ".rs"}


def scan_dependents(project_src: Path) -> str:
    """Scan source files and build a dependency map with actual import lines.

    Returns a text block showing each shared file, who imports from it,
    and the exact import statement each consumer uses. This prevents the
    Coder from renaming exports without updating all importers.
    """
    if not project_src.exists():
        return ""

    skip = {"node_modules", "dist", ".next", "__pycache__", ".venv", ".cache"}
    source_files: list[Path] = []
    for p in project_src.rglob("*"):
        if p.is_file() and p.suffix in SCAN_EXTENSIONS:
            if not any(part in skip for part in p.parts):
                source_files.append(p)

    if not source_files:
        return ""

    import_map: dict[str, list[tuple[str, str]]] = {}

    for sf in source_files:
        try:
            content = sf.read_text(errors="replace")
        except OSError:
            continue

        rel = str(sf.relative_to(project_src))
        for line in content.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for other in source_files:
                other_rel = str(other.relative_to(project_src))
                stem = other.stem
                if stem in stripped and other_rel != rel:
                    import_map.setdefault(other_rel, [])
                    entry = (rel, stripped)
                    if entry not in import_map[other_rel]:
                        import_map[other_rel].append(entry)

    if not import_map:
        return ""

    lines = [
        "## Import Dependency Map",
        "",
        "CRITICAL: If you modify any file listed below, you MUST preserve "
        "the exact export names that importers use. If you rename an export, "
        "you MUST update ALL files that import it in the same set of file_operations.",
        "",
    ]
    for target, importers in sorted(import_map.items()):
        lines.append(f"### {target}")
        for importer_path, import_line in sorted(importers):
            lines.append(f"  - `{importer_path}`: `{import_line}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Existing file contents for Coder context
# ---------------------------------------------------------------------------

MAX_FILE_CONTENT_CHARS = 80_000  # total budget for all file contents

def read_existing_files(project_src: Path) -> str:
    """Read all source files and return their contents for the Coder.

    Gives the Coder full visibility into existing code so edits are informed.
    Files are sorted by path and truncated to fit within the token budget.
    """
    if not project_src.exists():
        return ""

    skip = {"node_modules", "dist", ".next", "__pycache__", ".venv", ".cache", ".git"}
    code_exts = {
        ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".py", ".go", ".rs",
        ".html", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".md",
    }

    files: list[tuple[str, str]] = []
    total_chars = 0

    source_paths = sorted(
        (p for p in project_src.rglob("*")
         if p.is_file() and p.suffix in code_exts
         and not any(part in skip for part in p.parts)),
        key=lambda p: str(p.relative_to(project_src)),
    )

    for p in source_paths:
        try:
            content = p.read_text(errors="replace")
        except OSError:
            continue

        rel = str(p.relative_to(project_src))

        if total_chars + len(content) > MAX_FILE_CONTENT_CHARS:
            remaining = MAX_FILE_CONTENT_CHARS - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n... (truncated)"
                files.append((rel, content))
            break

        files.append((rel, content))
        total_chars += len(content)

    if not files:
        return ""

    parts = [
        "## Existing File Contents",
        "",
        "Below are the current contents of all project files. When editing a file, "
        "you MUST use this as your starting point — do NOT rewrite from scratch "
        "unless the task explicitly requires it.",
        "",
    ]
    for rel_path, content in files:
        parts.append(f"### {rel_path}\n```\n{content}\n```\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------

def _deps_satisfied(task: Task, state: ProjectState) -> bool:
    """Check if all of a task's dependencies are DONE or ARCHIVED."""
    if not task.dependencies:
        return True
    done_ids = {
        t.id for t in state.tasks
        if t.state in (TaskState.DONE, TaskState.ARCHIVED)
    }
    return all(dep in done_ids for dep in task.dependencies)


def get_next_runnable_task(state: ProjectState) -> Task | None:
    """Get the next READY task whose dependencies are all satisfied."""
    candidates = [
        t for t in state.tasks
        if t.state == TaskState.READY and _deps_satisfied(t, state)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda t: t.order)[0]


# ---------------------------------------------------------------------------
# Single task execution
# ---------------------------------------------------------------------------

def _run_coder(
    task: Task,
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
    prior_feedback: str = "",
) -> CoderOutput | None:
    """Call the Coder agent. Returns CoderOutput on success, None if blocked."""
    project_name = state.project_name
    project_src = get_project_src(project_name)
    spec = _load_artifact(project_name, "spec", f"{state.version}.md")
    plan = _load_artifact(project_name, "plans", f"{state.version}.md")
    file_tree = build_file_tree(project_src)
    dep_map = scan_dependents(project_src)
    existing_files = read_existing_files(project_src)

    extra_parts = [s for s in (dep_map, existing_files) if s]
    extra = "\n\n".join(extra_parts)

    console.print(f"\n[bold yellow]⚡ Coder[/bold yellow] [dim](attempt {task.attempt})[/dim]")

    coder_config = models.roles["coder"]
    system_prompt = compose_system_prompt(
        role=AgentRole.CODER,
        preferences=preferences,
        task=task,
        spec_content=spec,
        plan_content=plan,
        file_tree=file_tree,
        prior_feedback=prior_feedback,
        extra_context=extra,
    )

    start = time.time()
    with console.status("[yellow]Coder is thinking...[/yellow]", spinner="dots"):
        coder_output, usage = call_agent_single_shot(
            role=AgentRole.CODER,
            system_prompt=system_prompt,
            model_config=coder_config,
        )
    duration_ms = int((time.time() - start) * 1000)

    coder_log = RunLog(
        role=AgentRole.CODER,
        task_id=task.id,
        attempt=task.attempt,
        status=coder_output.status,
        summary=coder_output.summary,
        next_action=coder_output.next_action,
        files_touched=coder_output.files_touched,
        token_usage={
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        },
        duration_ms=duration_ms,
        model_used=usage.get("model", ""),
    )
    save_run_log(coder_log, project_name)

    console.print(f"  [dim]{coder_output.summary}[/dim]")
    console.print(
        f"  [dim]Tokens: {usage.get('input_tokens', '?')} in / "
        f"{usage.get('output_tokens', '?')} out | {duration_ms}ms[/dim]"
    )

    if coder_output.status == AgentStatus.BLOCKED:
        console.print(f"\n  [red]Coder blocked:[/red] {coder_output.summary}")
        return None

    # Apply file operations
    if isinstance(coder_output, CoderOutput) and coder_output.file_operations:
        console.print(f"\n  [bold]Applying {len(coder_output.file_operations)} file operations:[/bold]")
        apply_file_operations(coder_output.file_operations, project_src)

    # Execute commands
    if isinstance(coder_output, CoderOutput) and coder_output.commands:
        console.print(f"\n  [bold]Running {len(coder_output.commands)} commands:[/bold]")
        cmd_results = execute_commands(coder_output.commands, project_src)
        coder_log.commands = cmd_results
        save_run_log(coder_log, project_name)

    return coder_output if isinstance(coder_output, CoderOutput) else None


def _detect_build_commands(project_src: Path) -> list[str]:
    """Detect the project's build commands from package.json or other config."""
    pkg_json = project_src / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})
            cmds: list[str] = []
            if "build" in scripts:
                cmds.append("npm run build")
            if "lint" in scripts:
                cmds.append("npm run lint")
            return cmds if cmds else ["npm run build"]
        except (json.JSONDecodeError, KeyError):
            return ["npm run build"]

    if (project_src / "Cargo.toml").exists():
        return ["cargo build"]
    if (project_src / "setup.py").exists() or (project_src / "pyproject.toml").exists():
        return ["python -m py_compile"]
    if (project_src / "go.mod").exists():
        return ["go build ./..."]

    return []


def _format_command_results(results: list[CommandResult]) -> str:
    """Format command results into a readable block for QA context."""
    if not results:
        return "(no commands were run)"

    parts: list[str] = []
    for r in results:
        header = f"$ {r.command}  →  exit {r.exit_code}"
        lines = [header]
        if r.stdout:
            lines.append(f"stdout:\n{r.stdout[-1500:]}")
        if r.stderr:
            lines.append(f"stderr:\n{r.stderr[-1500:]}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _run_qa(
    task: Task,
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
    coder_output: CoderOutput,
    build_results: list[CommandResult],
) -> QAOutput:
    """Call the QA agent with build results. Returns QAOutput."""
    project_name = state.project_name
    spec = _load_artifact(project_name, "spec", f"{state.version}.md")
    diff = build_file_diff(coder_output.file_operations)

    console.print("\n[bold magenta]🧪 QA[/bold magenta]")

    build_context = _format_command_results(build_results)
    extra_context = f"## Build / Validation Results\n\n{build_context}"

    qa_config = models.roles["qa"]
    system_prompt = compose_system_prompt(
        role=AgentRole.QA,
        preferences=preferences,
        task=task,
        spec_content=spec,
        diff=diff,
        extra_context=extra_context,
    )

    start = time.time()
    with console.status("[magenta]QA is verifying...[/magenta]", spinner="dots"):
        qa_output, usage = call_agent_single_shot(
            role=AgentRole.QA,
            system_prompt=system_prompt,
            model_config=qa_config,
        )
    duration_ms = int((time.time() - start) * 1000)

    qa_log = RunLog(
        role=AgentRole.QA,
        task_id=task.id,
        attempt=task.attempt,
        status=qa_output.status,
        summary=qa_output.summary,
        next_action=qa_output.next_action,
        files_touched=qa_output.files_touched,
        token_usage={
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        },
        duration_ms=duration_ms,
        model_used=usage.get("model", ""),
        commands=build_results,
    )
    save_run_log(qa_log, project_name)

    console.print(f"  [dim]{qa_output.summary}[/dim]")
    console.print(
        f"  [dim]Tokens: {usage.get('input_tokens', '?')} in / "
        f"{usage.get('output_tokens', '?')} out | {duration_ms}ms[/dim]"
    )

    if isinstance(qa_output, QAOutput):
        if qa_output.verdict == "pass":
            console.print(f"  [green]Verdict: PASS[/green]")
        elif qa_output.verdict == "fail":
            console.print(f"  [red]Verdict: FAIL[/red]")
            for v in qa_output.violations:
                console.print(f"    [red]•[/red] {v}")
            if qa_output.notes:
                console.print(f"    [dim]{qa_output.notes}[/dim]")
        elif qa_output.verdict == "blocked":
            console.print(f"  [yellow]Verdict: BLOCKED[/yellow] — {qa_output.notes}")

    return qa_output


def _build_qa_feedback(qa_output: QAOutput, build_results: list[CommandResult]) -> str:
    """Format QA failures into feedback for the Coder's next attempt."""
    parts = [f"QA verdict: FAIL — {qa_output.summary}"]

    if qa_output.violations:
        parts.append("\nViolations:")
        for v in qa_output.violations:
            parts.append(f"  - {v}")

    if qa_output.notes:
        parts.append(f"\nNotes: {qa_output.notes}")

    failed_cmds = [r for r in build_results if r.exit_code != 0]
    if failed_cmds:
        parts.append("\nFailed commands:")
        for r in failed_cmds:
            parts.append(f"  $ {r.command} → exit {r.exit_code}")
            if r.stderr:
                parts.append(f"    stderr: {r.stderr[:800]}")

    parts.append(
        "\nIMPORTANT: Build errors often surface one at a time. If you fix one "
        "import/reference error, the same pattern may be broken in other files. "
        "Check ALL files that import from or depend on any file you modified, "
        "and fix every instance of the same class of error in a single pass."
    )

    return "\n".join(parts)


def _build_escalation_context(
    task: Task,
    state: ProjectState,
    run_logs: list[RunLog],
) -> str:
    """Build a comprehensive context block for the Planner to resolve an escalation."""
    parts = [
        f"## Escalation: Task {task.id}",
        f"\n**Title:** {task.title}",
        f"**Description:** {task.description}",
        f"**Attempts used:** {task.attempt}",
        f"**Blocked reason:** {task.blocked_reason or 'Max attempts reached'}",
    ]

    if task.acceptance_criteria:
        criteria = "\n".join(f"  - {c}" for c in task.acceptance_criteria)
        parts.append(f"\n**Acceptance Criteria:**\n{criteria}")

    if run_logs:
        parts.append("\n## Attempt History\n")
        for log in run_logs:
            parts.append(
                f"### {log.role.value.title()} (attempt {log.attempt})\n"
                f"- **Status:** {log.status.value}\n"
                f"- **Summary:** {log.summary}"
            )
            if log.commands:
                for cmd in log.commands:
                    if cmd.exit_code != 0:
                        parts.append(f"- **Failed command:** `{cmd.command}` → exit {cmd.exit_code}")
                        if cmd.stderr:
                            parts.append(f"  ```\n  {cmd.stderr[:500]}\n  ```")
            parts.append("")

    return "\n".join(parts)


def _load_run_logs(project_name: str, task_id: str) -> list[RunLog]:
    """Load all run logs for a given task."""
    logs_dir = get_project_logs(project_name) / "runs"
    if not logs_dir.exists():
        return []

    logs: list[RunLog] = []
    for path in sorted(logs_dir.glob(f"{task_id}_*.json")):
        try:
            data = json.loads(path.read_text())
            logs.append(RunLog.model_validate(data))
        except (json.JSONDecodeError, Exception):
            continue
    return logs


def _run_escalation(
    task: Task,
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
) -> PlannerOutput | None:
    """Escalate a blocked task to the Planner (Opus). Returns PlannerOutput or None on failure."""
    project_name = state.project_name
    run_logs = _load_run_logs(project_name, task.id)
    escalation_context = _build_escalation_context(task, state, run_logs)
    spec = _load_artifact(project_name, "spec", f"{state.version}.md")

    console.print("\n[bold red]🚨 Escalating to Planner[/bold red]")

    planner_config = models.roles["planner"]
    system_prompt = compose_system_prompt(
        role=AgentRole.PLANNER,
        preferences=preferences,
        task=task,
        spec_content=spec,
        extra_context=escalation_context,
    )

    start = time.time()
    with console.status("[red]Planner is analyzing the escalation...[/red]", spinner="dots"):
        planner_output, usage = call_agent_single_shot(
            role=AgentRole.PLANNER,
            system_prompt=system_prompt,
            model_config=planner_config,
            user_message="Resolve this escalation. Analyze all attempts and provide a resolution.",
        )
    duration_ms = int((time.time() - start) * 1000)

    planner_log = RunLog(
        role=AgentRole.PLANNER,
        task_id=task.id,
        attempt=task.attempt,
        status=planner_output.status,
        summary=planner_output.summary,
        next_action=planner_output.next_action,
        files_touched=[],
        token_usage={
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        },
        duration_ms=duration_ms,
        model_used=usage.get("model", ""),
    )
    save_run_log(planner_log, project_name)

    console.print(f"  [dim]{planner_output.summary}[/dim]")
    console.print(
        f"  [dim]Tokens: {usage.get('input_tokens', '?')} in / "
        f"{usage.get('output_tokens', '?')} out | {duration_ms}ms[/dim]"
    )

    if isinstance(planner_output, PlannerOutput):
        if planner_output.resolution == "retry":
            console.print(f"  [green]Resolution: RETRY with new guidance[/green]")
            if planner_output.guidance:
                console.print(f"  [dim]Guidance: {planner_output.guidance[:200]}[/dim]")
        elif planner_output.resolution == "human_needed":
            console.print(f"  [yellow]Resolution: HUMAN NEEDED[/yellow]")
            if planner_output.guidance:
                console.print(f"  [yellow]{planner_output.guidance}[/yellow]")
        else:
            console.print(f"  [yellow]Resolution: {planner_output.resolution or 'unknown'}[/yellow]")

        return planner_output

    return None


def _create_escalation(
    task: Task,
    state: ProjectState,
    from_role: AgentRole,
    reason: str,
) -> Escalation:
    """Create and persist an Escalation record."""
    esc_id = f"esc-{task.id}-{len(state.escalations) + 1}"
    escalation = Escalation(
        id=esc_id,
        task_id=task.id,
        from_role=from_role,
        to_role=AgentRole.PLANNER,
        reason=reason,
    )
    state.escalations.append(escalation)
    task.escalation_id = esc_id
    save_state(state)
    return escalation


def _resolve_escalation(
    escalation: Escalation,
    resolution: str,
    state: ProjectState,
) -> None:
    """Mark an escalation as resolved."""
    escalation.resolved = True
    escalation.resolution = resolution
    escalation.resolved_at = datetime.now(timezone.utc).isoformat()
    save_state(state)


MAX_ESCALATIONS = 2


def _run_coder_qa_loop(
    task: Task,
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
    prior_feedback: str = "",
    label: str = "",
) -> bool:
    """Run the Coder → Lint/Build → QA loop up to MAX_ATTEMPTS times.

    Returns True if QA passes. On failure, task is set to BLOCKED.
    """
    project_src = get_project_src(state.project_name)

    for attempt in range(MAX_ATTEMPTS):
        coder_output = _run_coder(task, state, models, preferences, prior_feedback)

        if coder_output is None:
            transition_task(task, TaskState.BLOCKED)
            task.blocked_reason = f"Coder could not complete the task{label}"
            save_state(state)
            return False

        # --- LINT / BUILD / QA ---
        transition_task(task, TaskState.IN_QA)
        save_state(state)

        build_cmds = _detect_build_commands(project_src)
        build_results: list[CommandResult] = []
        if build_cmds:
            console.print(f"\n  [bold]Running lint & build ({len(build_cmds)} commands):[/bold]")
            build_results = execute_commands(build_cmds, project_src)

        qa_output = _run_qa(task, state, models, preferences, coder_output, build_results)

        if isinstance(qa_output, QAOutput) and qa_output.verdict == "pass":
            transition_task(task, TaskState.DONE)
            save_state(state)
            suffix = f" (after escalation)" if label else ""
            console.print(f"\n  [bold green]✓ Task {task.id} complete{suffix}[/bold green]")
            return True

        if isinstance(qa_output, QAOutput) and qa_output.verdict == "blocked":
            transition_task(task, TaskState.BLOCKED)
            task.blocked_reason = f"QA blocked: {qa_output.notes}"
            save_state(state)
            return False

        if attempt < MAX_ATTEMPTS - 1:
            prior_feedback = _build_qa_feedback(qa_output, build_results)
            console.print(f"\n  [yellow]Retrying Coder with QA feedback (attempt {attempt + 2}/{MAX_ATTEMPTS})...[/yellow]")
            transition_task(task, TaskState.IN_PROGRESS)
            save_state(state)
        else:
            transition_task(task, TaskState.BLOCKED)
            task.blocked_reason = f"Failed QA after {MAX_ATTEMPTS} attempts{label}"
            save_state(state)
            return False

    return False


def run_task(
    task: Task,
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
) -> bool:
    """Run a single task through the Coder → Lint/Build → QA pipeline. Returns True on success."""
    project_src = get_project_src(state.project_name)
    project_src.mkdir(parents=True, exist_ok=True)

    transition_task(task, TaskState.IN_PROGRESS)
    save_state(state)

    console.print()
    console.print(Panel(
        f"[bold]{task.id}:[/bold] {task.title}\n"
        f"[dim]Attempt {task.attempt} | "
        f"Dependencies: {', '.join(task.dependencies) or 'none'}[/dim]",
        title="[bold cyan]Task[/bold cyan]",
        border_style="cyan",
    ))

    # --- PRIMARY LOOP: Coder → QA ---
    if _run_coder_qa_loop(task, state, models, preferences):
        return True

    # --- ESCALATION ---
    if task.state != TaskState.BLOCKED:
        return False

    console.print(f"\n  [red]Max attempts ({MAX_ATTEMPTS}) exhausted. Escalating to Planner...[/red]")

    escalation = _create_escalation(
        task, state,
        from_role=AgentRole.CODER,
        reason=task.blocked_reason or "Max attempts reached",
    )

    planner_output = _run_escalation(task, state, models, preferences)

    if planner_output is None:
        console.print(f"\n  [red]Planner escalation failed. Task remains blocked.[/red]")
        return False

    if isinstance(planner_output, PlannerOutput) and planner_output.resolution == "retry":
        _resolve_escalation(escalation, planner_output.summary, state)

        console.print(f"\n  [green]Planner provided new guidance. Resetting for retry...[/green]")
        transition_task(task, TaskState.READY)
        task.attempt = 0
        task.blocked_reason = None
        save_state(state)

        transition_task(task, TaskState.IN_PROGRESS)
        save_state(state)

        planner_feedback = (
            f"Planner escalation guidance (from Opus):\n\n"
            f"{planner_output.guidance}\n\n"
            f"Decisions made: {', '.join(planner_output.decisions) if planner_output.decisions else 'none'}\n\n"
            f"IMPORTANT: Follow this guidance precisely. Previous attempts failed — "
            f"this is your corrected approach from the project architect."
        )

        return _run_coder_qa_loop(
            task, state, models, preferences,
            prior_feedback=planner_feedback,
            label=" (after escalation)",
        )

    elif isinstance(planner_output, PlannerOutput) and planner_output.resolution == "human_needed":
        _resolve_escalation(escalation, f"Human needed: {planner_output.summary}", state)
        task.blocked_reason = f"Planner: human intervention needed — {planner_output.guidance}"
        save_state(state)
        console.print(f"\n  [yellow]Task requires human intervention. See blocked_reason for details.[/yellow]")
        return False

    else:
        console.print(f"\n  [red]Planner returned unclear resolution. Task remains blocked.[/red]")
        return False


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
) -> None:
    """Run all READY tasks in dependency order with human confirmation between tasks."""
    total = len(state.tasks)
    done_count = sum(1 for t in state.tasks if t.state in (TaskState.DONE, TaskState.ARCHIVED))

    console.print(Panel(
        f"Project: [bold]{state.project_name}[/bold] ({state.version})\n"
        f"Tasks: {done_count}/{total} complete",
        title="[bold]nova run[/bold]",
        border_style="green",
    ))

    while True:
        task = get_next_runnable_task(state)
        if task is None:
            if all_tasks_done(state):
                console.print("\n[bold green]All tasks complete![/bold green]")
                transition_phase(state, ProjectPhase.COMPLETE)
                save_state(state)
                console.print("\n[bold]Running Distiller...[/bold]")
                run_distiller(state, models, preferences)
            else:
                blocked = [t for t in state.tasks if t.state == TaskState.BLOCKED]
                remaining = [t for t in state.tasks if t.state == TaskState.READY]
                if blocked:
                    console.print(f"\n[yellow]{len(blocked)} task(s) blocked. Resolve before continuing.[/yellow]")
                    for t in blocked:
                        console.print(f"  [yellow]• {t.id}: {t.blocked_reason}[/yellow]")
                elif remaining:
                    console.print(f"\n[yellow]{len(remaining)} task(s) READY but dependencies not met.[/yellow]")
                else:
                    console.print("\n[yellow]No runnable tasks found.[/yellow]")
            break

        success = run_task(task, state, models, preferences)

        if not success:
            console.print("\n[yellow]Task failed. Fix the issue and re-run.[/yellow]")
            break

        done_count = sum(1 for t in state.tasks if t.state in (TaskState.DONE, TaskState.ARCHIVED))
        remaining = total - done_count

        if remaining == 0:
            continue

        console.print()
        console.print(f"[dim]{done_count}/{total} tasks done, {remaining} remaining[/dim]")

        try:
            answer = console.input("\n[bold]Continue to next task? [Y/n/q]: [/bold]").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Pipeline paused. Re-run to continue.[/yellow]")
            break

        if answer in ("n", "q", "quit", "exit"):
            console.print("[yellow]Pipeline paused. Re-run to continue.[/yellow]")
            break


# ---------------------------------------------------------------------------
# Distiller
# ---------------------------------------------------------------------------

def _load_all_run_logs(project_name: str) -> list[RunLog]:
    """Load every run log for a project."""
    logs_dir = get_project_logs(project_name) / "runs"
    if not logs_dir.exists():
        return []

    logs: list[RunLog] = []
    for path in sorted(logs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            logs.append(RunLog.model_validate(data))
        except (json.JSONDecodeError, Exception):
            continue
    return logs


def _load_existing_lessons() -> list[str]:
    """Load existing lessons from the knowledge base to avoid duplicates."""
    from nova.paths import KNOWLEDGE_DIR
    lessons_dir = KNOWLEDGE_DIR / "lessons"
    if not lessons_dir.exists():
        return []

    existing: list[str] = []
    for f in sorted(lessons_dir.glob("*.md")):
        existing.append(f.read_text().strip())
    return existing


def _build_distiller_context(
    state: ProjectState,
    run_logs: list[RunLog],
    existing_lessons: list[str],
) -> str:
    """Build the full context block for the Distiller."""
    parts: list[str] = []

    # Task summary
    parts.append("## Task Summary\n")
    total = len(state.tasks)
    done = sum(1 for t in state.tasks if t.state in (TaskState.DONE, TaskState.ARCHIVED))
    blocked = sum(1 for t in state.tasks if t.state == TaskState.BLOCKED)
    parts.append(f"- Total tasks: {total}")
    parts.append(f"- Completed: {done}")
    parts.append(f"- Blocked: {blocked}")

    total_attempts = sum(t.attempt for t in state.tasks)
    parts.append(f"- Total retry attempts across all tasks: {total_attempts}")
    parts.append("")

    for t in state.tasks:
        status_icon = "✓" if t.state == TaskState.DONE else "✗" if t.state == TaskState.BLOCKED else "?"
        parts.append(f"- [{status_icon}] {t.id}: {t.title} (attempts: {t.attempt}, state: {t.state.value})")
        if t.blocked_reason:
            parts.append(f"  Blocked: {t.blocked_reason}")

    # Escalation summary
    if state.escalations:
        parts.append("\n## Escalations\n")
        for esc in state.escalations:
            resolved = "resolved" if esc.resolved else "unresolved"
            parts.append(f"- {esc.id}: {esc.reason} [{resolved}]")
            if esc.resolution:
                parts.append(f"  Resolution: {esc.resolution}")

    # Run log summary (aggregate, not every line)
    if run_logs:
        parts.append("\n## Run Log Summary\n")
        total_input = sum(l.token_usage.get("input", 0) for l in run_logs)
        total_output = sum(l.token_usage.get("output", 0) for l in run_logs)
        total_duration = sum(l.duration_ms for l in run_logs)
        parts.append(f"- Total API calls: {len(run_logs)}")
        parts.append(f"- Total tokens: ~{total_input:,} input, ~{total_output:,} output")
        parts.append(f"- Total duration: {total_duration / 1000:.1f}s")

        role_counts: dict[str, int] = {}
        for l in run_logs:
            role_counts[l.role.value] = role_counts.get(l.role.value, 0) + 1
        parts.append(f"- Calls by role: {', '.join(f'{r}: {c}' for r, c in sorted(role_counts.items()))}")

        failed_logs = [l for l in run_logs if l.status != AgentStatus.COMPLETE]
        if failed_logs:
            parts.append(f"\n### Failed/Blocked Invocations ({len(failed_logs)}):\n")
            for l in failed_logs:
                parts.append(f"- {l.task_id} / {l.role.value} (attempt {l.attempt}): {l.summary[:150]}")

    # Existing lessons
    if existing_lessons:
        parts.append("\n## Existing Lessons (do NOT duplicate)\n")
        for i, lesson in enumerate(existing_lessons, 1):
            parts.append(f"{i}. {lesson[:200]}")

    return "\n".join(parts)


def run_distiller(
    state: ProjectState,
    models: ModelsConfig,
    preferences: dict[str, Any],
) -> DistillerOutput | None:
    """Run the Distiller to produce a retrospective and extract lessons."""
    from nova.paths import KNOWLEDGE_DIR

    project_name = state.project_name

    console.print(Panel(
        f"Project: [bold]{project_name}[/bold] ({state.version})\n"
        f"Running version retrospective...",
        title="[bold]nova distill[/bold]",
        border_style="magenta",
    ))

    run_logs = _load_all_run_logs(project_name)
    existing_lessons = _load_existing_lessons()
    spec = _load_artifact(project_name, "spec", f"{state.version}.md")
    plan = _load_artifact(project_name, "plans", f"{state.version}.md")

    distiller_context = _build_distiller_context(state, run_logs, existing_lessons)

    distiller_config = models.roles["distiller"]
    system_prompt = compose_system_prompt(
        role=AgentRole.DISTILLER,
        preferences=preferences,
        spec_content=spec,
        plan_content=plan,
        extra_context=distiller_context,
    )

    console.print("\n[bold magenta]📝 Distiller[/bold magenta]")

    start = time.time()
    with console.status("[magenta]Distiller is analyzing the version...[/magenta]", spinner="dots"):
        output, usage = call_agent_single_shot(
            role=AgentRole.DISTILLER,
            system_prompt=system_prompt,
            model_config=distiller_config,
            user_message="Produce the version retrospective and extract lessons.",
        )
    duration_ms = int((time.time() - start) * 1000)

    console.print(f"  [dim]{output.summary}[/dim]")
    console.print(
        f"  [dim]Tokens: {usage.get('input_tokens', '?')} in / "
        f"{usage.get('output_tokens', '?')} out | {duration_ms}ms[/dim]"
    )

    if not isinstance(output, DistillerOutput):
        console.print("[red]Distiller returned unexpected output.[/red]")
        return None

    # Save retrospective
    retro_dir = get_project_docs(project_name) / "retros"
    retro_dir.mkdir(parents=True, exist_ok=True)
    retro_path = retro_dir / f"{state.version}-retro.md"
    retro_path.write_text(output.retro_content)
    console.print(f"\n  [green]Retrospective saved:[/green] {retro_path.relative_to(Path.cwd())}")

    # Save lessons
    if output.proposed_lessons:
        lessons_dir = KNOWLEDGE_DIR / "lessons"
        lessons_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"\n  [bold]Proposed lessons ({len(output.proposed_lessons)}):[/bold]")
        for i, lesson in enumerate(output.proposed_lessons):
            console.print(f"    {i + 1}. {lesson}")

            lesson_file = lessons_dir / f"{project_name}-{state.version}-{i + 1}.md"
            lesson_file.write_text(lesson)
            console.print(f"       [green]Saved:[/green] {lesson_file.relative_to(Path.cwd())}")
    else:
        console.print("\n  [dim]No new lessons proposed.[/dim]")

    # Save run log
    distiller_log = RunLog(
        role=AgentRole.DISTILLER,
        task_id=f"{state.version}-retro",
        attempt=0,
        status=output.status,
        summary=output.summary,
        next_action=output.next_action,
        files_touched=[],
        token_usage={
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        },
        duration_ms=duration_ms,
        model_used=usage.get("model", ""),
    )
    save_run_log(distiller_log, project_name)

    console.print(f"\n  [bold green]✓ Distiller complete[/bold green]")
    return output
