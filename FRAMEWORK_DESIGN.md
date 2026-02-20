# Agent Framework — V1 Design Document

**Status:** In Progress
**Last Updated:** Feb 18, 2026

---

## 1. Purpose

A deterministic multi-agent development runtime that orchestrates specialized AI roles to plan, build, review, and ship software projects.

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

- **Framework runner:** Python (better LLM SDK support, simpler CLI tooling, great at file/JSON/YAML manipulation)
- **Projects:** Any language. The framework doesn't care what language a project uses. Agents generate code in whatever the project requires.

### Directory Structure

```
agent-framework/              ← git repo (public)
├── af/                       ← Python package (the runner)
├── agents/
│   ├── planner.md
│   ├── coder.md
│   ├── reviewer.md
│   ├── qa.md
│   └── distiller.md
├── config/
│   ├── pipelines/
│   │   └── full.json
│   ├── models.json
│   └── framework_preferences.yaml
├── knowledge/
│   ├── lessons/
│   ├── escalation-patterns.md
│   ├── failed-patterns.md
│   └── knowledge_index.json
├── docs/
│   ├── state_machine.md
│   ├── workflow.md
│   └── contracts/
├── projects/                 ← .gitignored at framework level
│   └── <project>/            ← each project is its own git repo
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
│       ├── preferences.yaml
│       └── src/
└── FRAMEWORK_DESIGN.md       ← this file
```

Projects are git-ignored at the framework level and initialized as independent repositories.

### Model Routing (V1)

| Role | Model | Reasoning |
|------|-------|-----------|
| Planner | Opus 4.6 | High-stakes reasoning, architecture, spec writing. Low volume. |
| Coder | Sonnet 4 | High volume, follows instructions precisely. Cost-efficient. |
| Reviewer | Sonnet 4 | Reads code against spec. Careful, not creative. |
| QA | Sonnet 4 | Validates behavior, runs test logic. Mostly mechanical. |
| Distiller | Opus 4.6 | Synthesis requires strong reasoning. Runs once per version. Worth it. |

Configured in `config/models.json`. When Codex 5.3 API becomes available, swap it into the Coder role — one config change.

V1 uses a single provider (Anthropic) with two model tiers. Multi-provider support (OpenAI, etc.) deferred to later versions.

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
| Decisions (ADRs) | Why did we choose X? | Lost reasoning history |

---

## 4. CLI Interface (Decided)

> **Note:** `name` is a placeholder. Final CLI name TBD. Candidates: `forge`, `arc`, `helm`, `loop`, `op`, `crew`.

### Phase 1: Project Creation & Brainstorm

```bash
name new <project-name>
```

- Creates full project directory structure under `projects/<project-name>/`
- Drops into an interactive brainstorm session with Planner (loose mode)
- Brainstorm is conversational — free-form back and forth
- Produces `docs/brainstorm/v1-notes.md` (running log of key points)
- Brainstorm is tied to a version (one brainstorm per version)
- Either human or Planner can initiate transition to spec creation

Resume an interrupted session:

```bash
name brainstorm <project> [--version v1]
```

### Phase 2: Spec Creation

Transitions from brainstorm, or invoked directly:

```bash
name spec create <project> --version v1
```

- Planner switches to strict spec-writing mode
- Drafts `docs/spec/v1.md` based on brainstorm conversation
- Feedback loop: human reads, gives feedback, Planner revises
- Human says "approved" → spec is locked and immutable
- Orchestrator prompts: "Spec v1 approved. Ready to create a plan?"

### Phase 3: Plan Creation

```bash
name plan create <project> --version v1
```

- Same feedback loop as spec
- Planner drafts `docs/plans/v1.md` based on the locked spec
- Back and forth until human approves
- On approval: plan is locked, orchestrator prompts for task generation

### Phase 4: Task Generation

```bash
name tasks generate <project> --version v1
```

- Planner reads approved spec + plan, produces `docs/tasks/v1.tasks.json`
- Human reviews structured task list
- Can give feedback ("task 3 is too big, split it", "reorder 5 and 6")
- On approval: tasks are locked (only status, blocked_reason, archival metadata mutable)

### Phase 5: Execution

Run the full pipeline (processes all READY tasks sequentially):

```bash
name run <project> --version v1
```

Run a specific task:

```bash
name task run <project> <task-id>
```

Run a specific role against a task (for debugging/retrying):

```bash
name task run <project> <task-id> --role coder
```

Pipeline per task: **Coder → Reviewer → QA**

- Pauses between tasks for human confirmation (V1)
- Future: `--auto` flag to skip confirmations
- Each agent invocation writes a run log
- Commit per task with task ID in commit message

Example terminal output:

```
[name] Project: af-website | Version: v1
[name] Loading pipeline: full
[name] Next task: v1-001 (Create project scaffolding)
[name] ── Coder ──────────────────────────────
[name] Model: sonnet-4 | Budget: 8000 tokens
[name] Status: complete
[name] Files: package.json, tsconfig.json, src/app/layout.tsx (+3)
[name] Commit: a1b2c3d "v1-001: create project scaffolding"
[name] ── Reviewer ───────────────────────────
[name] Model: sonnet-4
[name] Status: passed
[name] Notes: "Structure matches plan. No preference violations."
[name] ── QA ─────────────────────────────────
[name] Model: sonnet-4
[name] Status: passed
[name] Notes: "Build succeeds. No runtime errors."
[name] ── Task v1-001: DONE ──────────────────
[name] Run log: logs/runs/v1-001-attempt-1.json
[name]
[name] Next task: v1-002 (Implement hero section)
[name] Continue? [Y/n]
```

### Phase 6: Escalation Handling

When an agent returns `blocked` or hits budget thresholds:

- Task state → BLOCKED with `blocked_reason` + `escalation_id`
- Routing: Coder → Planner, Planner → Human, QA/Reviewer → Planner
- Only Planner or Human can move a task back to READY

### Phase 7: Version Completion

```bash
name retro <project> --version v1
```

- Distiller runs after all tasks DONE
- Produces `docs/retros/v1-retro.md`
- Proposes lessons for `knowledge/lessons/`

### Utility Commands

```bash
name status <project>                     # show task states, current phase
name log <project> <task-id>              # show run logs for a task
name tasks list <project> --version v1    # list all tasks with status
name spec show <project> --version v1     # display the spec
name plan show <project> --version v1     # display the plan
```

---

## 5. Agents (Decided)

### Planner
- Two modes: **brainstorm** (loose, exploratory) and **spec/plan** (strict, structured)
- Brainstorm mode: asks questions, proposes ideas, challenges assumptions
- Spec mode: writes formal specs with acceptance criteria
- Also handles: plan authoring, task generation, escalation resolution, ADR creation

### Coder
- Implements tasks by producing structured file operations
- Escalates instead of guessing when blocked
- Output: file creates/edits + commands to run

### Reviewer
- Checks code against spec, acceptance criteria, and preferences
- Receives: task + acceptance criteria + git diff + preferences + spec refs
- Returns: pass/fail + notes

### QA
- Validates functionality by running build/test commands
- Receives: task + acceptance criteria + test commands + diff
- Returns: pass/fail + notes

### Distiller
- Runs once per version after all tasks complete
- Receives: version artifacts + aggregated run logs + decisions
- Produces: retro document + proposed lessons
- Max 1-2 lessons per version (quality over quantity)

All agents return a common structured output (schema TBD — see open question #5).

---

## 6. Key Decisions Made

1. **Python for the framework.** Projects can be any language.
2. **Anthropic only in V1.** Opus for Planner/Distiller, Sonnet for Coder/Reviewer/QA.
3. **CLI-driven in V1.** Human initiates every phase. No server, no webhooks.
4. **Interactive chat sessions** for brainstorm/spec/plan phases. Conversation history persisted in `logs/sessions/`.
5. **Structured output for Coder** (Option A). Coder returns JSON with file operations; runner applies them to disk. Most deterministic, easiest to validate and rollback.
6. **Commit per task.** Git commit with task ID in message after successful Coder → Reviewer → QA pass.
7. **Human confirmation between tasks** in V1. `--auto` flag deferred.
8. **Brainstorm tied to version.** `docs/brainstorm/v1-notes.md`, `v2-notes.md`, etc.
9. **Preference merge: deep merge, project overrides framework.** Except for `must_*` rules which require human resolution on conflict.
10. **Version boundaries are human decisions** with orchestrator suggestions.

---

## 7. Escalation Protocol (Decided)

Escalation is a first-class event:

- Agent returns `status: "blocked"` or hits budget thresholds
- Orchestrator creates an escalation object, updates task to BLOCKED
- Routing defaults:
  - Coder → Planner
  - Planner → Human
  - QA/Reviewer → Planner
- Task gets `blocked_reason` + `escalation_id`
- Only Planner or Human can move it back to READY

---

## 8. Knowledge Layer (From Original Spec)

### Lessons
- Distilled after retros
- Created only for repeated friction, escalation clusters, token waste, architectural regrets
- Must be short, evidence-based, portable, behavior-changing
- Max 1-2 per version

### Escalation Patterns
- When to stop retrying, when to escalate
- Role-specific thresholds
- Prevents infinite loops and token burn

### Failed Patterns
- Global anti-patterns ("what repeatedly causes thrash")
- Injected selectively per task (only relevant entries)

---

## 9. Future Vision (Not V1)

Planting seeds, not building yet:

- **V2:** Full pipeline sequencing, multi-project, knowledge injection
- **V3:** GitHub integration (PRs per task, issue intake → draft specs, webhooks)
- **V4:** Server mode, monitoring integration (Vercel logs), auto-remediation with approval gates
- **V5:** Configurable autonomy tiers, circuit breakers, autonomous operation for routine issues

---

## 10. Open Questions — Remaining Items to Flesh Out

### OPEN QUESTION 1: CLI — Interactive Session Implementation

The brainstorm/spec/plan phases are the most complex part of V1. Remaining details:

- **Conversation persistence:** Proposed `logs/sessions/<project>-<phase>-<version>.json` as a JSON array of messages. Confirm format.
- **Streaming:** Use Anthropic streaming API so responses appear in real-time. Any concerns?
- **Phase transitions:** Triggered by keywords ("approved", "ready for spec") intercepted by the runner before sending to the model. Need to define the exact keyword set.
- **Session resumption:** On resume, feed conversation history back as context. What's the max context window budget for session history? Do we truncate or summarize old messages?
- **Error handling:** What happens if the API call fails mid-session? Auto-retry? Save state and exit?

### OPEN QUESTION 2: State Machine

Define the exact task state transitions:

- Which states exist? (Current list: NEW, READY, IN_PROGRESS, IN_REVIEW, IN_QA, BLOCKED, DONE, ARCHIVED)
- What triggers each transition?
- Who/what can trigger each transition?
- What validations gate each transition?
- Is there a project-level state machine too (BRAINSTORM → SPEC_DRAFT → SPEC_APPROVED → PLAN_DRAFT → PLAN_APPROVED → TASKS_GENERATED → EXECUTING → COMPLETE)?
- How do we handle version-level state vs. task-level state?
- State machine diagram needed.

### OPEN QUESTION 3: Agent Invocation Contract

How the runner composes and executes an agent call:

- **Prompt composition:** How exactly does the runner assemble the system prompt? Order of injection (template → preferences → knowledge → task → artifacts)?
- **Context budget:** How much context does each role get? Hard token limits per role?
- **API call mechanics:** Streaming vs. non-streaming per role? Timeout handling? Retry policy?
- **Response parsing:** How does the runner extract structured output from the model response? Tool use? JSON block in text? Structured output API?
- **Validation:** What happens if the response doesn't match the expected schema? Retry? Escalate?
- **Conversation vs. single-shot:** Brainstorm/spec/plan are multi-turn conversations. Coder/Reviewer/QA are single-shot (one request, one response). Confirm this distinction.

### OPEN QUESTION 4: Run Log Schema

Define the exact fields for `logs/runs/*.json`:

- Proposed fields: role, task_id, attempt, status, escalation_info, summary, next_action, files_touched, commands (with exit codes), token_usage, duration, git_commit, model_used, timestamp
- What NOT to store: full prompts, full model outputs, entire diffs, full stack traces
- Do we need a separate schema for session logs (brainstorm/spec/plan conversations) vs. execution logs (coder/reviewer/QA runs)?
- How are logs indexed? By task? By timestamp? Both?
- Log rotation / archival policy?

### OPEN QUESTION 5: Agent Output Schema (`agent_output.schema.json`)

Every agent returns a common structured format. Define it:

- Proposed common fields: status, summary, next_action, artifacts_created, files_touched, optional escalation
- **Coder-specific:** file operations array (create/edit/delete with paths and content), commands to run
- **Reviewer-specific:** pass/fail verdict, violation list, notes
- **QA-specific:** pass/fail verdict, test commands run + results, notes
- **Planner-specific:** artifact content (spec/plan/task list), decisions made
- **Distiller-specific:** retro content, proposed lessons
- How strictly do we enforce this? Reject non-conforming responses? Retry?
- JSON Schema or Pydantic model?

### OPEN QUESTION 6: Pipeline Config Format

Define `config/pipelines/full.json`:

- What does it contain? Ordered list of roles per task? Conditional logic (skip QA if no tests)?
- Can pipelines be customized per project?
- Example structure needed
- Are there other pipeline types besides "full"? (e.g., "quick" = Coder only, "review-only" = Reviewer + QA)

### OPEN QUESTION 7: Preference Schema

Define `framework_preferences.yaml` and project `preferences.yaml`:

- What categories of preferences exist? (coding style, architecture, testing, documentation, naming conventions, etc.)
- What's the format? Flat key-value? Nested YAML?
- Which preferences are `must_*` (hard guardrails) vs. `prefer_*` (soft defaults)?
- How are preferences injected into agent prompts? Verbatim? Summarized?
- Example preferences needed
- How does the merge work mechanically? Deep merge with project winning? What about list values (append or replace)?

---

## 11. V1 Build Scope Summary

Once all open questions are resolved, V1 includes:

1. **Project scaffolding** — `new` command creates directory structure
2. **Interactive sessions** — brainstorm, spec, plan phases with chat loop + persistence
3. **Task generation** — Planner produces structured task JSON
4. **Pipeline execution** — Coder (structured output) → Reviewer → QA
5. **Run logging** — every agent invocation logged
6. **State management** — task state transitions, spec/plan approval flags
7. **Git integration** — commit per task with task ID in message
8. **Escalation** — BLOCKED state + routing to Planner or Human
9. **Retro/Distiller** — post-version synthesis
10. **Config** — models.json, preferences, pipeline config

First project after V1 is working: a website explaining the framework (simple, self-contained, validates the system with a real use case).


Flags
Flag 1: The interactive chat sessions are the hardest part of V1.
The pipeline execution (Coder → Reviewer → QA) is actually straightforward — it's deterministic, one agent at a time, structured input/output. But the brainstorm/spec/plan phases are a conversational chat loop embedded in a CLI. That means:
You need to maintain conversation history within a session
You need to stream responses (nobody wants to wait 30 seconds staring at a blank terminal)
You need to detect phase transitions ("approved" → lock spec → prompt for plan)
You need to handle session resumption — if you exit mid-brainstorm and come back tomorrow, it needs to reload context
This is doable but it's the most complex part of V1 from an engineering standpoint. I'd suggest:
Use Anthropic's streaming API for responses
Persist conversation history as a simple JSON array in logs/sessions/<project>-<phase>-<version>.json
Phase transitions triggered by explicit keywords you type ("approved", "ready for spec", etc.) that the runner intercepts before sending to the model
On resume, feed the conversation history back as context with a "session resumed" system message
This keeps it simple while still feeling conversational.

Flag 2: How does the Coder agent actually write code?
This is the question we haven't touched. When Coder gets a task, what's the mechanism for turning that into files on disk? Options:
Option A: Structured output. Coder returns a JSON response containing file operations:
{  "files": [    {"action": "create", "path": "src/app/page.tsx", "content": "..."},    {"action": "edit", "path": "src/app/layout.tsx", "content": "..."}  ],  "commands": ["npm install framer-motion"]}
The runner applies these to the filesystem. This is the most controlled approach — the runner validates everything before writing.
Option B: Tool use. Coder uses Claude's tool-use API with tools like write_file, edit_file, run_command. The runner implements these tools and executes them as the model calls them. This is more flexible but less predictable.
Option C: Diff-based. Coder returns unified diffs that the runner applies. More precise for edits, but models are inconsistent at generating clean diffs.
My recommendation for V1: Option A (structured output). It's the most deterministic, easiest to validate, easiest to log, and easiest to roll back. The runner is fully in control of what hits the filesystem. Option B is better long-term but adds complexity.
Flag 3: How does QA actually validate?
QA needs to do more than "read the code." For a web app, it needs to:
Run the build (npm run build) and check for errors
Possibly run the dev server and check for crashes
Run tests if they exist
Check that acceptance criteria are met
So QA needs the ability to execute shell commands and interpret the results. The runner needs to:
Give QA a sandboxed command execution capability
Capture stdout/stderr and exit codes
Feed those back to QA for interpretation
QA then returns a structured verdict (pass/fail + notes)
For V1, this can be simple: QA gets a list of validation commands from the task (or infers them: "run npm run build, check exit code"), the runner executes them, and QA interprets the output. Not autonomous browsing or screenshot testing — just command-line validation.


So the V1 build scope as I see it:
Project scaffolding — new command creates directory structure
Interactive sessions — brainstorm, spec, plan phases with chat loop + persistence
Task generation — Planner produces structured task JSON
Pipeline execution — Coder (structured output) → Reviewer (read + assess) → QA (commands + assess)
Run logging — every agent invocation logged
State management — task state transitions, spec/plan approval flags
Git integration — commit per task with task ID in message
Escalation — BLOCKED state + routing to Planner or Human
Retro/Distiller — post-version synthesis
Config — models.json, preferences, pipeline config
