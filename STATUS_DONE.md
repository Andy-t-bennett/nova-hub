# Nova Hub — What's Been Done

**Last Updated:** Feb 21, 2026

---

## V1 Core (Phases 1-6): COMPLETE

### Phase 1: Foundation
- `pyproject.toml` with entry point, all dependencies
- venv with Python 3.12+
- `nova/` package: `__init__.py`, `cli.py`, `config.py`
- Repo layout: `agents/`, `config/`, `knowledge/`, `projects/`
- Config loaders: `models.json`, `framework_preferences.yaml`
- `nova new <project>` — scaffolds project directory, inits git repo

### Phase 2: Data Models + State Machine
- Pydantic models: `Task`, `RunLog`, `AgentOutput` (+ role-specific: `CoderOutput`, `QAOutput`, `PlannerOutput`, `DistillerOutput`, `ReviewerOutput`), `Escalation`, `ProjectState`, `FileOperation`, `CommandResult`
- Task state machine: `NEW → READY → IN_PROGRESS → IN_QA → DONE / BLOCKED / ARCHIVED`
- Project phase machine: `BRAINSTORM → SPEC_DRAFT → SPEC_APPROVED → PLAN_DRAFT → PLAN_APPROVED → TASKS_GENERATED → EXECUTING → COMPLETE`
- Thread-safe state persistence (`state.json` per project)
- Preference merge: deep merge, project overrides framework, `must_*` guardrails, structured format with `value`, `description`, `agent_instruction`

### Phase 3: Agent Invocation Layer
- Anthropic SDK integration (streaming + single-shot)
- Prompt composition engine: template + preferences + knowledge + task context + file tree + existing file contents + dependency map
- JSON response parsing with code fence extraction
- Retry logic (3 attempts with exponential backoff)
- Malformed JSON recovery (re-prompt for valid JSON)
- Billing/credit error detection with clear user messaging
- Token budget management per role with context window limits

### Phase 4: Interactive Sessions
- Chat loop engine with Rich streaming output
- Session persistence in `logs/sessions/`
- Phase transition detection: "approved", "lock it in", "lgtm", etc.
- Document locking: approved specs/plans are immutable
- `nova brainstorm <project>` — Planner in loose/exploratory mode
- `nova spec <project>` — Planner in strict spec-writing mode, auto-prompts from brainstorm notes
- `nova plan <project>` — Plan creation from approved spec
- `nova tasks <project>` — Task generation, JSON array with structured fields

### Phase 5: Pipeline Execution
- Pipeline runner: orchestrates Coder → Lint/Build → QA per task
- Coder agent: structured JSON output (file operations + commands), runner applies to disk
- QA agent: validates build results, acceptance criteria, spec compliance, preference compliance
- Lint/build detection: auto-detects `npm run build`, `npm run lint`, `cargo build`, etc.
- Run logging: structured log per agent invocation in `logs/runs/`
- Git auto-commit per task: `[nova] v1-001: Task title`
- Human confirmation between batches
- `nova run <project>` — run full pipeline
- `nova task <project> <task-id>` — run single task

### Phase 6: Escalation + Completion
- Escalation handler: detects blocked status, creates escalation record
- Escalation routing: Coder → Planner (Opus) for resolution
- Planner can `retry` (with guidance) or declare `human_needed`
- Max 3 attempts per task, max 2 escalations before permanent block
- `nova distill <project>` — Distiller produces retrospective + extracts lessons
- Knowledge management: lessons saved to `knowledge/lessons/`, loaded into future agent prompts
- Auto-distill: runs automatically when `nova run` completes all tasks

---

## Post-V1 Improvements (Phase 1.5)

These were built after the core V1 was working:

1. **Dropped LLM Reviewer** — Reviewer was redundant (same model reviewing its own peer's work). QA now handles acceptance criteria, spec compliance, and preference compliance alongside build validation. Pipeline is now Coder → Lint/Build → QA.

2. **Fixed project code directory** — Code lives in `projects/{name}/code/` instead of `projects/{name}/src/`. Prevents the `src/src/` nesting issue when Coder creates `src/` inside the project.

3. **Coder sees existing file contents** — `read_existing_files()` reads all project source files (up to 80K chars) and includes them in the Coder's context. Prevents blind rewrites of existing files.

4. **Import dependency map** — `scan_dependents()` scans source files for import relationships and provides the Coder with a map showing which files import from which, with exact import lines. Prevents the Coder from renaming exports without updating importers.

5. **`nova status <project>`** — Rich table showing project phase, approval states, all tasks with state/attempts/blocked reasons, summary counts.

6. **Parallel task execution** — Independent tasks (no shared dependencies) run concurrently via `ThreadPoolExecutor`. Thread-safe state saves and git commits. Falls back to sequential for single-task batches.

7. **Better API error handling** — Billing/credit errors show a clear message with a link to the Anthropic billing page. General API errors show more context.

8. **Pipeline config updated** — `config/pipelines/full.json` reflects the current pipeline (Coder → QA, escalation to Planner, auto-distill).

---

## Files That Matter

| File | Purpose |
|------|---------|
| `nova/cli.py` | CLI entry point — all `nova` commands |
| `nova/runner.py` | Pipeline execution — Coder → QA loop, escalation, distiller, parallel batches |
| `nova/agent.py` | Anthropic API — single-shot, streaming, response parsing, retries |
| `nova/models.py` | Pydantic data models — Task, RunLog, AgentOutput variants, Escalation, ProjectState |
| `nova/state.py` | State machine — task/phase transitions, persistence, thread-safe saves |
| `nova/prompt.py` | Prompt composition — templates + preferences + knowledge + context assembly |
| `nova/config.py` | Config loaders — models.json, preferences YAML, merge logic |
| `nova/paths.py` | Path resolution — framework root, project dirs, knowledge dir |
| `nova/session.py` | Interactive chat loop — streaming, phase transition detection, session save/load |
| `nova/transitions.py` | Phase transitions — handles spec/plan/task approval, artifact saving |
| `agents/*.md` | Agent templates — system prompts for each role |
| `config/models.json` | Model config — which Claude model each role uses |
| `config/framework_preferences.yaml` | Framework-level preferences with structured format |
| `config/pipelines/full.json` | Pipeline definition (documentation, not dynamically loaded yet) |

---

## Known Working End-to-End Flow

Successfully tested on multiple projects:

```bash
nova new myproject
nova brainstorm myproject    # interactive chat with Planner
nova spec myproject          # auto-drafts spec from brainstorm, approve when ready
nova plan myproject          # auto-drafts plan from spec, approve when ready
nova tasks myproject         # generates task list, approve when ready
nova run myproject           # executes all tasks: Coder → Build → QA, auto-distills at end
nova status myproject        # check progress anytime
```
