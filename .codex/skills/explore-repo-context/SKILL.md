---
name: explore-repo-context
description: Build fresh repo understanding before implementation, debugging, reviews, or planning. Use when Codex starts work in this project or needs current context about purpose, constraints, file layout, data sources, tests, architecture, or recent local changes before making decisions.
---

# Explore Repo Context

## Overview

Use this skill to deliberately refresh repository context before acting. Prefer current files and local instructions over memory, summaries, or assumptions.

## Context Workflow

1. Read local agent instructions first.
   - Read `AGENTS.md` if present.
   - Follow any referenced files such as `CLAUDE.md`.
   - Treat these instructions as project policy unless they conflict with higher-priority system or developer instructions.

2. Load project-specific context.
   - For this repository, read `references/markets-recon-context.md` after local agent instructions.
   - Then read the files named there before making implementation decisions.

3. Inventory the repo quickly.
   - Run `pwd`.
   - Run `rg --files` or `find` to identify source, docs, data, tests, scripts, config, and hidden project metadata.
   - Use `ls -la` for the root and any directory that appears central.
   - If the repo is a git worktree, run `git status --short` before editing; if it is not, note that briefly.

4. Identify the active technical shape.
   - Read package/build config files before assuming a stack.
   - Locate entry points, test commands, scripts, fixtures, sample data, and generated outputs.
   - Search for TODOs, open questions, schema names, and user-facing terms relevant to the request.

5. Respect authoritative sources.
   - Prefer canonical docs, schemas, fixtures, and locked data files over older notes.
   - When sources conflict, name the conflict and choose the authority identified by repo instructions.
   - Do not invent labels, schemas, commands, or workflows when the repo provides them.

6. Summarize before substantial work.
   - State the repo purpose in one or two sentences.
   - Name the files that are authoritative for the current task.
   - Call out constraints, open questions, and likely validation commands.
   - Keep the summary short, then proceed with the user’s task.

## Search Patterns

Use targeted searches once the first-pass inventory is done:

```bash
rg -n "TODO|FIXME|open question|Open Questions|Definition Of Done|source of truth|canonical|Locked|schema|confidence|uncertain"
rg -n "test|pytest|vitest|jest|mocha|ruff|mypy|lint|build" -g '!*csv'
```

Adjust patterns to the task. For large data files, inspect headers and small samples instead of reading entire files into context.

## Decision Rules

- Read before editing when touching unfamiliar code or data.
- Re-run context discovery when the user asks for "latest", "current", "existing", or "how this repo works".
- Do not rely on previous-session memory if the same fact can be checked cheaply.
- Keep exploration proportional: broad enough to avoid wrong assumptions, narrow enough to preserve momentum.
- Prefer `rg` and direct file reads. Avoid network access unless the task explicitly requires current external information.
