# Nova Hub — Implementation Plan

**Status:** V1 Complete, V2 Planned
**Last Updated:** Feb 21, 2026

---

## Locked-In Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **CLI library: Typer** (built on Click) | Clean type-hint-based API, auto-generated help text, battle-tested foundation |
| 2 | **Python 3.12+** with **venv** (mandatory) | Latest language features, isolated dependencies |
| 3 | **Open questions resolved iteratively** | Decide as we build each component, document decisions inline |
| 4 | **Run from repo (MVP)** | No PyPI/pip install for now. Just entry point from venv. Distribution later |
| 5 | **Foundation-first build order** | Build infrastructure before chat loop |
| 6 | **Rich for terminal UX** | Formatted output, streaming rendering, progress indicators |
| 7 | **Anthropic Python SDK** | Official SDK handles streaming, retries, types, token counting |
| 8 | **Local files for knowledge/docs (V1)** | No external DB. JSON/YAML/Markdown files, version-controlled with git |
| 9 | **CLI command name: `nova`** | All commands prefixed with `nova` |
| 10 | **Python package name: `nova`** | Package directory is `nova/` inside the `nova-hub/` repo |
| 11 | **Reviewer removed from pipeline** | QA handles both validation and compliance checking. Reviewer was redundant (same-tier model reviewing its peer). |
| 12 | **Code directory: `code/`** | Project code lives in `projects/{name}/code/` to avoid `src/src/` nesting |
| 13 | **Parallel task execution** | Independent tasks run concurrently via ThreadPoolExecutor, thread-safe state |

---

## V1 Build Order — COMPLETE

### Phase 1: Foundation — Skeleton + Config + Scaffolding ✅

- [x] Set up `pyproject.toml` with entry point, dependencies, metadata
- [x] Create venv, install dependencies (anthropic, typer, pydantic, pyyaml, rich, python-dotenv)
- [x] Create `nova/` package structure (`__init__.py`, `cli.py`, `config.py`)
- [x] Create repo directory layout (`agents/`, `config/`, `knowledge/`)
- [x] Implement config loaders (models.json, framework_preferences.yaml)
- [x] Implement `nova new <project>` — scaffolds full project directory, inits git repo

### Phase 2: Data Models + State Machine ✅

- [x] Define Pydantic models: Task, RunLog, AgentOutput variants, Escalation, ProjectState
- [x] Implement task state machine (NEW → READY → IN_PROGRESS → IN_QA → DONE / BLOCKED / ARCHIVED)
- [x] Implement project-level state (BRAINSTORM → SPEC_DRAFT → ... → COMPLETE)
- [x] State persistence (thread-safe read/write state.json)
- [x] Preference merge logic (deep merge, `must_*` guardrails, structured preferences)

### Phase 3: Agent Invocation Layer ✅

- [x] Prompt composition engine — template + preferences + knowledge + task + context
- [x] Anthropic API integration — streaming (chat) + single-shot (pipeline agents)
- [x] Response parsing — JSON extraction from code fences and raw text
- [x] Token budget management per role (context window limits)
- [x] Error handling — retries, malformed JSON recovery, billing error detection

### Phase 4: Interactive Sessions ✅

- [x] Chat loop engine — Rich terminal I/O with streaming responses
- [x] Session persistence — save/load conversation history as JSON
- [x] Phase transition detection — keyword interception ("approved", "lock it in", "lgtm")
- [x] Document locking — approved specs/plans become immutable
- [x] `nova brainstorm`, `nova spec`, `nova plan`, `nova tasks` — all working

### Phase 5: Pipeline Execution ✅

- [x] Pipeline runner — orchestrates Coder → Lint/Build → QA per task
- [x] Coder agent — structured JSON output (file ops + commands), runner applies to disk
- [x] QA agent — validates build results + acceptance criteria + spec compliance
- [x] Run logging — structured log per agent invocation to `logs/runs/`
- [x] Git auto-commit per task with task ID in message
- [x] Human confirmation between batches
- [x] `nova run` and `nova task` commands
- [x] Parallel execution for independent tasks (ThreadPoolExecutor)
- [x] Coder receives existing file contents + import dependency map

### Phase 6: Escalation + Completion ✅

- [x] Escalation handler — detect blocked, create escalation object
- [x] Escalation routing — Coder → Planner (Opus) with full failure context
- [x] Planner returns retry (with guidance) or human_needed
- [x] Max 3 attempts, max 2 escalations
- [x] `nova distill` — Distiller produces retro + extracts lessons
- [x] Knowledge management — lessons saved and loaded into future prompts
- [x] Auto-distill when `nova run` completes all tasks

### Phase 7: Utility Commands + Polish (Partial) ⚠️

- [x] `nova status <project>` — Rich table with phase, tasks, attempts, blocked reasons
- [x] Better API error messages (billing detection, clear guidance)
- [ ] `nova log <project> <task-id>` — show run logs
- [ ] `nova tasks list <project>` — list tasks with status
- [ ] `nova spec show <project>` — display spec
- [ ] `nova plan show <project>` — display plan
- [ ] Tests (none written yet)

---

## V2 Build Order — PLANNED

### Phase 2.1: Automation

- [ ] `--auto` flag on `nova run` — skip confirmation prompts
- [ ] `nova auto <project> "description"` — one command, full flow, auto-approve
- [ ] Non-interactive spec/plan/tasks generation for auto mode

### Phase 2.2: Testing

- [ ] pytest setup
- [ ] State machine tests (valid + invalid transitions)
- [ ] Preference merge tests
- [ ] Prompt composition tests
- [ ] Response parsing tests (JSON extraction, malformed input)
- [ ] Config loading tests

### Phase 2.3: Remaining Polish

- [ ] `nova log`, `nova tasks list`, `nova spec show`, `nova plan show`
- [ ] Static analysis integration (ESLint, ruff, clippy auto-detection)
- [ ] Dynamic pipeline config loading from `full.json`

### Phase 2.4: Integrations (Future)

- [ ] GitHub integration — branches, PRs, commit linking
- [ ] Multi-provider support — OpenAI, Gemini
- [ ] Smarter context management — task-relevant files only
- [ ] PyPI distribution

---

## Dependencies (V1)

```
anthropic       — Anthropic API SDK
typer           — CLI framework
pydantic        — Data models, validation, serialization
pyyaml          — YAML config parsing
rich            — Terminal formatting, streaming output, tables
python-dotenv   — .env file loading
```

---

## What's NOT in V1

- ~~`--auto` flag~~ → V2
- ~~Tests~~ → V2
- PyPI distribution
- Multi-provider support
- Server mode / webhooks
- GitHub integration
- Autonomous browsing / screenshot testing
- Multi-project orchestration
