# Nova Hub — Implementation Plan

**Status:** Locked In
**Last Updated:** Feb 19, 2026

---

## Locked-In Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **CLI library: Typer** (built on Click) | Clean type-hint-based API, auto-generated help text, battle-tested foundation |
| 2 | **Python 3.12+** with **venv** (mandatory) | Latest language features, isolated dependencies |
| 3 | **Open questions resolved iteratively** | Decide as we build each component, document decisions inline |
| 4 | **Tests per phase** | Write tests as each phase is completed, not after the whole build |
| 5 | **Run from repo (MVP)** | No PyPI/pip install for now. Just `python -m nova` or entry point from venv. Distribution figured out later |
| 6 | **Foundation-first build order** | Build infrastructure (config, state, models) before chat loop. Chat loop depends on solid plumbing — prompt composition, session persistence, state transitions all need to exist first. De-risks the hard part by giving it a stable foundation to build on |
| 7 | **Rich for terminal UX** | Formatted output, streaming rendering, progress indicators, the `[nova]` prefixed output style |
| 8 | **Anthropic Python SDK** | Official SDK handles streaming, retries, types, token counting. Far less error-prone than raw HTTP. One dependency, massive reduction in boilerplate |
| 9 | **Local files for knowledge/docs (V1)** | No Supabase or external DB. Local JSON/YAML/Markdown files, version-controlled with git. Simpler, faster, works offline, easier to debug. External storage is a V2+ concern if multi-user or cloud scenarios arise |
| 10 | **CLI command name: `nova`** | All commands prefixed with `nova` (e.g., `nova new`, `nova run`, `nova status`) |
| 11 | **Python package name: `nova`** | Package directory is `nova/` inside the `nova-hub/` repo |

---

## Refined Build Order

### Phase 1: Foundation — Skeleton + Config + Scaffolding

**Goal:** Runnable CLI that can create projects and load config.

- [ ] Set up `pyproject.toml` with entry point, dependencies, metadata
- [ ] Create venv, install dependencies (anthropic, typer, pydantic, pyyaml, rich)
- [ ] Create `nova/` package structure (`__init__.py`, `cli.py`, `config.py`)
- [ ] Create repo directory layout (`agents/`, `config/`, `knowledge/`, `docs/`)
- [ ] Implement config loaders (models.json, framework_preferences.yaml, pipelines)
- [ ] Implement `nova new <project>` — scaffolds full project directory, inits git repo
- [ ] **Tests:** Config loading, project scaffolding, directory structure validation

### Phase 2: Data Models + State Machine

**Goal:** All core data structures defined. State transitions enforced.

- [ ] Define Pydantic models: Task, RunLog, AgentOutput, Escalation, ProjectState
- [ ] Implement task state machine (NEW → READY → IN_PROGRESS → IN_REVIEW → IN_QA → DONE / BLOCKED / ARCHIVED)
- [ ] Implement project-level state (BRAINSTORM → SPEC_DRAFT → SPEC_APPROVED → PLAN_DRAFT → PLAN_APPROVED → TASKS_GENERATED → EXECUTING → COMPLETE)
- [ ] State persistence (read/write task and project state to disk)
- [ ] Preference merge logic (deep merge, project overrides framework, `must_*` conflict detection)
- [ ] **Tests:** State transitions (valid + invalid), preference merging, model serialization

### Phase 3: Agent Invocation Layer

**Goal:** Can call Anthropic API, compose prompts, parse responses.

- [ ] Agent base class — common interface for all roles
- [ ] Prompt composition engine — assemble system prompt from template + preferences + knowledge + context
- [ ] Anthropic API integration — streaming (for chat) + single-shot (for pipeline agents)
- [ ] Response parsing — extract structured output, validate against Pydantic models
- [ ] Token budget management per role
- [ ] Error handling — API failures, malformed responses, retries
- [ ] **Tests:** Prompt composition, response parsing, error handling (mock API calls)

### Phase 4: Interactive Sessions (The Hard Part)

**Goal:** Full brainstorm → spec → plan workflow working end-to-end.

- [ ] Chat loop engine — terminal input/output with Rich, streaming responses
- [ ] Session persistence — save/load conversation history as JSON
- [ ] Session resumption — reload context on `nova brainstorm <project>`
- [ ] Phase transition detection — keyword interception ("approved", "ready for spec")
- [ ] Document locking — approved specs/plans become immutable
- [ ] `nova brainstorm <project>` — Planner in loose mode
- [ ] `nova spec create <project> --version v1` — Planner in strict mode, feedback loop
- [ ] `nova plan create <project> --version v1` — same pattern as spec
- [ ] `nova tasks generate <project> --version v1` — structured task JSON output
- [ ] **Tests:** Session save/load, phase transitions, document locking

### Phase 5: Pipeline Execution

**Goal:** Coder → Reviewer → QA pipeline runs against tasks.

- [ ] Pipeline runner — reads pipeline config, orchestrates agent sequence per task
- [ ] Coder agent — structured JSON output (file ops + commands), runner applies to disk
- [ ] Reviewer agent — receives task + criteria + diff, returns pass/fail
- [ ] QA agent — executes validation commands, captures output, interprets results
- [ ] Run logging — structured log per agent invocation to `logs/runs/`
- [ ] Git integration — auto-commit per task with task ID in message
- [ ] Human confirmation loop — pause between tasks, show summary
- [ ] `nova run <project> --version v1` — run full pipeline
- [ ] `nova task run <project> <task-id>` — run single task
- [ ] **Tests:** Pipeline sequencing, file operation application, run log writing

### Phase 6: Escalation + Completion

**Goal:** Blocked tasks route correctly. Versions can be closed out.

- [ ] Escalation handler — detect blocked status, create escalation object
- [ ] Escalation routing — Coder→Planner, Planner→Human, QA/Reviewer→Planner
- [ ] BLOCKED → READY transition (only Planner or Human)
- [ ] `nova retro <project> --version v1` — Distiller agent produces retro + lessons
- [ ] Knowledge management — store lessons, update knowledge index
- [ ] **Tests:** Escalation routing, state transitions for blocked tasks

### Phase 7: Utility Commands + Polish

**Goal:** Full CLI surface area. Production-quality UX.

- [ ] `nova status <project>` — show task states, current phase
- [ ] `nova log <project> <task-id>` — show run logs
- [ ] `nova tasks list <project> --version v1` — list tasks with status
- [ ] `nova spec show <project> --version v1` — display spec
- [ ] `nova plan show <project> --version v1` — display plan
- [ ] Terminal output formatting — Rich-based `[nova]` prefixed output with role sections
- [ ] Error handling pass — graceful failures, state preservation on crash
- [ ] **Tests:** CLI output formatting, command argument validation

---

## Dependencies (V1)

```
anthropic       — Anthropic API SDK (streaming, tool use, types)
typer           — CLI framework (built on Click)
pydantic        — Data models, validation, serialization
pyyaml          — YAML config parsing
rich            — Terminal formatting, streaming output, tables
pytest          — Testing
```

---

## What We're NOT Building in V1

- PyPI distribution / `pip install nova-hub`
- Supabase or any external database
- Multi-provider support (OpenAI, etc.)
- Server mode / webhooks
- GitHub integration (PRs, issues)
- `--auto` flag (skip human confirmation)
- Autonomous browsing / screenshot testing for QA
- Multi-project orchestration
