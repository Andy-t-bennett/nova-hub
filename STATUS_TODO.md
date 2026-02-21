# Nova Hub — What Needs To Be Done

**Last Updated:** Feb 21, 2026

---

## Phase 2: Automation (Next Up)

### High Priority

1. **`--auto` flag on `nova run`**
   - Skip the "Continue to next batch?" prompt between tasks
   - Pipeline runs all tasks without human intervention
   - Estimated effort: Small (add a flag, skip the input prompt)

2. **`nova auto <project> "description"`**
   - One command: idea → working code
   - Runs the full flow: brainstorm → spec → plan → tasks → run → distill
   - Non-interactive: Planner generates spec/plan/tasks and auto-approves after one pass
   - Human can review artifacts afterward
   - This is the killer feature — turns 6 manual commands into 1
   - Estimated effort: Medium (need non-interactive versions of spec/plan/tasks generation)

3. **Tests**
   - Zero tests exist. The implementation plan called for tests per phase.
   - Priority areas:
     - State machine transitions (valid + invalid)
     - Preference merging (deep merge, must_* conflicts)
     - Prompt composition (template + context assembly)
     - Response parsing (JSON extraction, malformed input)
     - Config loading (models.json, preferences YAML)
   - Use pytest with mocked API calls (no real Anthropic calls in tests)
   - Estimated effort: Medium-Large

### Medium Priority

4. **Remaining Phase 7 utility commands**
   - `nova log <project> <task-id>` — show run logs for a task
   - `nova tasks list <project>` — list all tasks with status (similar to `nova status` table but just tasks)
   - `nova spec show <project>` — display the approved spec
   - `nova plan show <project>` — display the approved plan
   - Estimated effort: Small (read files and print with Rich formatting)

5. **Static analysis in QA**
   - Wire up real linters: ESLint for JS/TS, ruff for Python, cargo clippy for Rust
   - Auto-detect and run as part of the lint/build step
   - Feed lint output to QA for evaluation
   - Currently `_detect_build_commands` only looks for `npm run lint` in package.json scripts
   - Estimated effort: Small-Medium

6. **Pipeline config becomes dynamic**
   - Runner reads `config/pipelines/full.json` to determine which steps to run
   - Add a "lite" pipeline (Coder only, skip QA) for rapid prototyping
   - `nova run --pipeline lite` to select
   - Estimated effort: Medium

### Lower Priority

7. **GitHub integration**
   - Auto-create a branch per version (`nova/v1`)
   - Open a PR when all tasks complete
   - Link commits to task IDs
   - Estimated effort: Medium

8. **Multi-provider support**
   - Allow OpenAI, Gemini, etc. alongside Anthropic
   - `models.json` already has a `provider` field — just not wired up
   - Would enable using different models per role (e.g., OpenAI o3 for Coder, Claude Opus for Planner)
   - Estimated effort: Medium-Large

9. **Smarter context management**
   - Currently `read_existing_files` dumps all files up to 80K chars
   - Better: only include files relevant to the current task (based on task description, dependencies, file tree analysis)
   - Would reduce token usage significantly on larger projects
   - Estimated effort: Medium

10. **PyPI distribution**
    - `pip install nova-hub` instead of running from the repo
    - Would need to handle config/agents/knowledge paths differently (not relative to repo root)
    - Estimated effort: Medium

---

## Known Issues / Technical Debt

1. **No tests** — See item 3 above. This is the biggest risk for regressions.

2. **Console output interleaves during parallel execution** — When multiple tasks run in parallel, their Rich output (panels, spinners, status messages) can interleave. Functional but messy. Could be fixed with per-task output buffering or a live display layout.

3. **Pipeline config not dynamically loaded** — `config/pipelines/full.json` exists but the runner ignores it. Pipeline steps are hardcoded in `runner.py`.

4. **Session resumption is basic** — If you exit mid-brainstorm and come back, the conversation history reloads but there's no summary/compression of old messages. Long sessions could hit context limits.

5. **`ReviewerOutput` and `AgentRole.REVIEWER` still in `models.py`** — Kept for backwards compatibility with existing state files and run logs. Not used in the active pipeline. Can be removed once old project data is cleared.

6. **`TaskState.IN_REVIEW` still in state machine** — Same backwards compat reason. Transitions to/from IN_REVIEW are still defined but never triggered.

7. **Existing file contents budget is fixed** — `MAX_FILE_CONTENT_CHARS = 80_000` is a constant. For very large projects, this may not be enough or may waste tokens on irrelevant files.

---

## Decisions to Make

- **Automation level for `nova auto`**: Should it auto-approve everything, or should it pause for human review at spec/plan? Probably configurable (`--approve-all` vs default which pauses at spec).
- **Test strategy**: Unit tests only? Integration tests with mocked API? End-to-end tests with real API calls (expensive)?
- **Multi-version support**: V2 of a project — does it start from the V1 codebase? How does the Planner get context about what already exists?
