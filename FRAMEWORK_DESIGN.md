# Nova Hub — V1 Design Document

**Status:** V1 Complete
**Last Updated:** Feb 21, 2026

---

## 1. Purpose

A deterministic multi-agent development runtime that orchestrates specialized AI roles to plan, build, and ship software projects.

Core principles:

- Versioned intent (specs, plans, tasks are immutable once approved)
- Controlled execution (pipeline-driven, not ad hoc)
- Structured escalation (agents stop and route, never guess)
- Persistent memory (run logs, retros, lessons)
- Self-improvement (cross-project knowledge synthesis)

This is a runtime system, not a prompt collection.

---

## 2. Architecture Overview

### Language & Stack

- **Framework runner:** Python 3.12+ (better LLM SDK support, simpler CLI tooling, great at file/JSON/YAML manipulation)
- **Projects:** Any language. The framework doesn't care what language a project uses. Agents generate code in whatever the project requires.

### Directory Structure

```
nova-hub/                     ← git repo
├── nova/                     ← Python package (the runner)
│   ├── __init__.py
│   ├── cli.py               ← Typer CLI entry point
│   ├── agent.py             ← Anthropic API client (streaming + single-shot)
│   ├── config.py            ← Config loaders, preference merge
│   ├── models.py            ← Pydantic data models
│   ├── paths.py             ← Path resolution
│   ├── prompt.py            ← Prompt composition engine
│   ├── runner.py            ← Pipeline execution (Coder → QA, escalation, distiller)
│   ├── session.py           ← Interactive chat loop engine
│   ├── state.py             ← State machine + persistence
│   └── transitions.py       ← Phase transition handlers
├── agents/
│   ├── planner.md
│   ├── coder.md
│   ├── qa.md
│   ├── reviewer.md          ← kept for reference, not used in pipeline
│   └── distiller.md
├── config/
│   ├── pipelines/
│   │   └── full.json
│   ├── models.json
│   └── framework_preferences.yaml
├── knowledge/
│   ├── lessons/              ← extracted by Distiller
│   ├── escalation-patterns.md
│   └── failed-patterns.md
├── projects/                 ← each project is its own git repo
│   └── <project>/
│       ├── docs/
│       │   ├── brainstorm/
│       │   ├── spec/
│       │   ├── plans/
│       │   ├── tasks/
│       │   ├── decisions/
│       │   └── retros/
│       ├── logs/
│       │   ├── runs/
│       │   └── sessions/
│       ├── code/             ← Coder writes files here
│       ├── preferences.yaml
│       └── state.json
├── FRAMEWORK_DESIGN.md
├── IMPLEMENTATION_PLAN.md
├── STATUS_DONE.md
└── STATUS_TODO.md
```

### Model Routing

| Role | Model | Reasoning |
|------|-------|-----------|
| Planner | Claude Opus 4.6 | High-stakes reasoning, architecture, spec writing, escalation resolution. Low volume. |
| Coder | Claude Sonnet 4.6 | High volume, follows instructions precisely. Cost-efficient. |
| QA | Claude Sonnet 4.6 | Validates build results, acceptance criteria, spec compliance. |
| Distiller | Claude Opus 4.6 | Synthesis requires strong reasoning. Runs once per version. |

Configured in `config/models.json`. Model swaps are a one-line config change.

V1 uses a single provider (Anthropic) with two model tiers.

---

## 3. Core Lifecycle

Every project version follows this lifecycle:

```
Brainstorm → Spec → Plan → Tasks → Execute → Retro → Lessons
```

| Layer | Answers | Prevents |
|-------|---------|----------|
| Spec | What are we building? | Scope creep |
| Plan | How will we build it? | Architectural thrash |
| Tasks | What are the atomic work units? | Execution ambiguity |
| Run Logs | What happened during execution? | Invisible failures |
| Retro | How did the version go? | Repeated mistakes |
| Lessons | What portable rules did we learn? | Cross-project waste |
| Escalation Patterns | When should agents stop? | Infinite loops, token burn |
| Failed Patterns | What shouldn't we repeat? | Self-sabotage |
| Preferences | What are the personal constraints? | Generic AI output |

---

## 4. CLI Interface

### Project Creation & Brainstorm

```bash
nova new <project-name>       # scaffolds project, inits git
nova brainstorm <project>     # interactive session with Planner (loose mode)
```

### Spec, Plan, Tasks

```bash
nova spec <project>           # Planner drafts spec from brainstorm, feedback loop until approved
nova plan <project>           # Planner drafts plan from spec, feedback loop until approved
nova tasks <project>          # Planner generates structured task JSON, approve when ready
```

### Execution

```bash
nova run <project>            # run full pipeline — all tasks, parallel when possible
nova task <project> <id>      # run a single task by ID
```

Pipeline per task: **Coder → Lint/Build → QA**

- Independent tasks run in parallel (ThreadPoolExecutor)
- Pauses between batches for human confirmation
- Each agent invocation writes a run log
- Git auto-commit per task: `[nova] v1-001: Task title`
- Auto-distill runs when all tasks complete

### Status & Retrospective

```bash
nova status <project>         # dashboard: phase, tasks, attempts, blocked reasons
nova distill <project>        # manually run Distiller (also runs automatically after nova run)
```

### Escalation Flow

When a task fails after 3 Coder→QA attempts:
1. Escalates to Planner (Opus) with full failure context
2. Planner returns `retry` (with guidance) or `human_needed`
3. On retry: task resets, Coder gets Planner's guidance as feedback
4. Max 2 escalations before permanent block

---

## 5. Agents

### Planner (Opus)
- Two modes: **brainstorm** (loose, exploratory) and **strict** (spec/plan/tasks)
- Brainstorm mode: asks questions, proposes ideas, challenges assumptions
- Strict mode: writes formal specs, plans, task lists
- Also handles: escalation resolution (analyzes failures, provides corrective guidance)

### Coder (Sonnet)
- Implements tasks via structured JSON: file operations (create/edit/delete) + commands
- Receives: task, spec, plan, file tree, existing file contents, import dependency map, prior feedback
- Escalates instead of guessing when blocked
- Runner applies file operations to `projects/{name}/code/`

### QA (Sonnet)
- Single quality gate: validates build/lint results, acceptance criteria, spec compliance, preference compliance
- Receives: task, spec, code diff, build/lint command results
- Returns: pass/fail/blocked with violations and evidence
- Build is mandatory (gated by `must_run_build_before_done` preference)

### Distiller (Opus)
- Runs once per version after all tasks complete
- Receives: all run logs, spec, plan, task summaries, escalation history, existing lessons
- Produces: retrospective document + proposed lessons
- Lessons saved to `knowledge/lessons/` and loaded into future agent prompts

---

## 6. Key Decisions

1. **Python for the framework.** Projects can be any language.
2. **Anthropic only in V1.** Opus for Planner/Distiller, Sonnet for Coder/QA.
3. **CLI-driven in V1.** Human initiates every phase.
4. **Interactive chat sessions** for brainstorm/spec/plan. Conversation history persisted.
5. **Structured output for Coder.** Returns JSON with file operations; runner applies to disk.
6. **Git auto-commit per task.** Commit with task ID in message after QA passes.
7. **Human confirmation between batches** in V1. `--auto` flag planned for V2.
8. **Preference merge: deep merge, project overrides framework.** `must_*` rules are hard guardrails. All preferences have `value`, `description`, `agent_instruction`.
9. **Reviewer removed from pipeline.** QA absorbed its responsibilities. Build/lint validation + acceptance criteria + spec compliance in one step.
10. **Parallel task execution.** Independent tasks (no shared dependencies) run concurrently.
11. **Code directory is `code/` not `src/`.** Prevents `src/src/` nesting when Coder creates framework-standard `src/` directories.
12. **Coder sees existing files.** Full file contents provided in context to prevent blind rewrites.

---

## 7. Escalation Protocol

Escalation is a first-class event:

- Task fails QA after MAX_ATTEMPTS (3) → escalate to Planner
- Planner (Opus) receives full failure context: task details, all run logs, build errors
- Planner returns `retry` with guidance, or `human_needed`
- On retry: attempt counter resets, Coder gets Planner's guidance
- Max MAX_ESCALATIONS (2) before permanent block
- Escalation records stored in `state.escalations`

---

## 8. Knowledge Layer

### Lessons
- Extracted by Distiller after retros
- Saved to `knowledge/lessons/`
- Loaded into agent prompts for all future invocations
- Must be short, evidence-based, portable, behavior-changing
- Max 1-2 per version

### Escalation Patterns
- When to stop retrying, when to escalate
- Role-specific thresholds
- Prevents infinite loops and token burn

### Failed Patterns
- Global anti-patterns
- Injected selectively per task

---

## 9. Future Vision (Not V1)

- **V2:** `--auto` flag, `nova auto` one-command flow, tests, remaining utility commands
- **V3:** GitHub integration (PRs per task, branches per version)
- **V4:** Multi-provider support (OpenAI, Gemini alongside Anthropic)
- **V5:** Server mode, configurable autonomy tiers, circuit breakers
