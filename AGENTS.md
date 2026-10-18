# qortoo — Shared Conventions

This file is the canonical source for conventions shared across every `qortoo-*` repository (Rust, Go, or otherwise). Each repo keeps its own root `AGENTS.md` — the only Claude/Codex-adjacent file allowed outside this repo — with these sections copied in verbatim, plus repo-specific content appended. No repo other than this one should have a `CLAUDE.md` or its own `.claude/settings.json`; see [Principle: this is the only repo with Claude/Codex tooling](README.md#principle-this-is-the-only-repo-with-claudecodex-tooling) in the README. When this file changes, copy the updated section into each consuming repo — there is no automatic sync (tools like Codex don't follow imports into subdirectories).

The `qortoo-shared` Claude Code plugin (`plugins/qortoo-shared/`) in this repo provides the actual commit/PR/doc-scaffolding automation described below.

## Critical guidelines

- All code comments and shared, tracked documentation must be written in **English**. Personal plan documents under `qortoo-harness/.local/plans/` are the exception: they must use the language of the current user session.
- Favor SOLID principles, especially Single Responsibility (SRP) and Open/Closed (OCP), where practical. Check for these during review.
- Structure important concepts under `./docs/` so they're easy to follow from Markdown and diagrams grounded in the actual code. Use the `/qortoo-shared:doc-new` command to scaffold a new concept document.
- All individual plan documents, regardless of the repository they concern, belong in the qortoo-harness repository's gitignored `.local/plans/` directory. Task lists and working notes likewise belong under qortoo-harness's `.local/` — never commit them or propose committing them. Claude Code's project-scoped agent memory (`.claude/agent-memory/`) is likewise personal and gitignored, not shared team knowledge.
- When work is carried out against a plan document in `.local/plans/`, update that document — status, checklist items, progress log — as part of the same work, not only when separately asked. The plan should reflect what was actually done (including corrections, reverted commits, and scope decisions) closely enough that reading it alone tells the true current state.

## Commit & Pull Request Guidelines

- Commit messages follow an emoji + conventional format: `🧬feat(scope): summary`, `🪲fix(scope): summary`, `📜docs(scope): summary`, `💿CI/CD(scope): summary`. See `/qortoo-shared:commit` for the full emoji/type table and scope guidance.
- Keep scopes specific to the affected module or package.
- PRs should include a concise summary, testing notes (commands run), and links to issues if applicable. Target branch is always `main`; PRs are squash-merged, so the PR title/body becomes the final commit message (`/qortoo-shared:pr` automates this).
- If behavior changes, update docs and add/adjust tests.

## Documentation Guidelines

- Qortoo has not been released yet, so `docs/` should describe only the current intended behavior.
- Do not add development-history or migration notes for intermediate, unreleased changes (for example, that a field or label previously existed and was removed) unless the user explicitly asks for that history.
- Add compatibility or migration guidance only for behavior that has appeared in a released version.
- Concept docs describe the system as it is now and the rule that holds it in place — write principles, not history. Leave out what was tried before, what was removed, or what was "learned the hard way"; git history already carries that.

## Personal plan lifecycle

- Keep active plan documents in the gitignored `.local/plans/` directory.
- Before closing a plan, confirm that its checklist and external delivery state match reality. Keep it active if any acceptance condition remains, even when implementation is otherwise complete.
- Mark a finished plan as complete, record its final outcome, and move it to the gitignored `.local/legacy/` directory. Completed plans are retained as personal history rather than deleted.
- Do not commit files from either `.local/plans/` or `.local/legacy/`.
