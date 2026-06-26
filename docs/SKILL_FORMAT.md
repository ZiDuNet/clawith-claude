# Clawith Skill Format Spec

> Authoritative reference for how skills are stored, parsed, and loaded into agent workspaces.
> Read this before writing the SKILL → Clawith converter or any new skill.

## 1. Storage location

Skills live inside an agent's workspace at:

```
<agent_id>/skills/<skill-name>/
```

`<agent_id>` is a UUID; the full path is resolved by `app.services.storage.normalize_storage_key()`.
Backends: local filesystem (`STORAGE_BACKEND=local`, root = `STORAGE_LOCAL_ROOT`) or S3-compatible (MinIO).

## 2. Two physical formats (both supported)

The skill loader in `app/services/agent_context.py:_load_skills_index()` accepts **either**:

### Format A — Folder-style (recommended)

```
skills/
└── <skill-name>/
    ├── SKILL.md           # required (skill.md also accepted)
    ├── scripts/           # optional, Python stdlib
    ├── references/        # optional, knowledge base markdown
    ├── examples/          # optional, sample inputs/outputs
    └── assets/            # optional, templates, images, data files
```

### Format B — Flat-file

```
skills/
└── <skill-name>.md        # single markdown file, no subdirs
```

**Rule of thumb:** if the skill has any auxiliary file (scripts, references, examples), use Format A.
Format B is for tiny reference cards (1-page reminders).

## 3. YAML frontmatter (required)

`SKILL.md` / `<skill-name>.md` must start with YAML frontmatter between `---` fences.
Only `name` and `description` are parsed by Clawith (see `_parse_skill_frontmatter` in `agent_context.py:29-71`).

```markdown
---
name: "human-readable-skill-name"
description: "One-sentence description. When the user asks X, read this skill. Triggers: keyword1, keyword2."
---

# Skill body starts here
```

### Field rules

| Field | Required | Constraints |
|---|---|---|
| `name` | yes | string, ≤ 80 chars, no leading `---`/`name:` collision |
| `description` | yes | string, ≤ 200 chars (gets put in the index table); comma-separated trigger keywords help the model decide when to load |

### Extra fields are preserved but ignored

`license`, `metadata`, `category`, `version`, `author`, `updated`, etc. are kept verbatim in the file
(Clawith doesn't strip them) but **not parsed**. Safe to include for human readers and external tooling.

## 4. Loading semantics — progressive disclosure

Skills are **not** inlined into the system prompt. Instead:

1. On every LLM turn, `agent_context._load_skills_index()` scans `<agent_id>/skills/` and builds a markdown table:

   ```
   | Skill | Description | File |
   |-------|-------------|------|
   | Docker Development | Docker and container development skill… | skills/docker-development/SKILL.md |
   | Code Reviewer | Review pull requests against team standards… | skills/code-reviewer/SKILL.md |
   ```

2. The table is appended to the system prompt along with usage rules:

   > ⚠️ SKILL USAGE RULES:
   > 1. When a user request matches a skill, FIRST call `read_file` with the File path above to load the full instructions.
   > 2. Follow the loaded instructions to complete the task.
   > 3. Do NOT guess what the skill contains — always read it first.
   > 4. Folder-based skills may contain auxiliary files (scripts/, references/, examples/). Use `list_files` on the skill folder to discover them.

3. The agent calls `read_file` (file tool, see `agent_tools.py`) on the SKILL.md path when relevant.

**Implication for converters:** the `description` field is the single most important string — it
determines whether the agent loads the skill at all. Keep it tight, trigger-keyword-rich, ≤ 200 chars.

## 5. Auxiliary file conventions

### `scripts/`

- Python **stdlib only** (per Clawith's `execute_code` sandbox — no `pip install` at runtime).
- Each script should be CLI-runnable: `python scripts/foo.py --help` exits 0.
- Document the CLI in SKILL.md's "Workflow" section.
- Scripts are discovered by the agent via `list_files skills/<skill-name>/`.

### `references/`

- Markdown files, each focused on a sub-topic.
- Cited by SKILL.md; the agent reads them on demand.
- Naming: `kebab-case.md`. Example: `references/seo-fundamentals.md`.

### `examples/`

- Sample inputs + expected outputs.
- Naming: `<use-case>.md` or `<use-case>.json`.

### `assets/`

- Static templates, schemas, small datasets.
- Anything binary should be small (< 1 MB) or referenced via external URL.

## 6. Conversion rules (claude-skills → Clawith)

Source layout (per `claude-skills`):

```
<domain>/
└── <skill-name>/
    └── skills/
        └── <skill-name>/
            ├── SKILL.md
            ├── scripts/*.py
            ├── references/*.md
            └── assets/*
```

Target layout (Clawith workspace):

```
<agent-uuid>/
└── skills/
    └── <skill-name>/
        ├── SKILL.md
        ├── scripts/*.py
        ├── references/*.md
        └── assets/*
```

**Mapping:**

| Source | Target | Action |
|---|---|---|
| `<domain>/<skill>/skills/<skill>/SKILL.md` | `<output>/<skill>/SKILL.md` | copy verbatim |
| `<domain>/<skill>/skills/<skill>/scripts/` | `<output>/<skill>/scripts/` | copy recursively |
| `<domain>/<skill>/skills/<skill>/references/` | `<output>/<skill>/references/` | copy recursively |
| `<domain>/<skill>/skills/<skill>/assets/` | `<output>/<skill>/assets/` (or examples/) | copy recursively; rename `assets/` → `examples/` if it contains worked examples |
| frontmatter `name` | `name` | keep as-is |
| frontmatter `description` | `description` | **truncate to 200 chars** (Clawith parses it; keep trigger keywords) |
| frontmatter `license: MIT` | keep as comment block after frontmatter | Clawith doesn't strip it but agents should see it |
| `/cs:*` slash commands (in SKILL.md body) | keep as markdown section | Clawith has no slash system, but agent reads and may mimic |
| `cs-*` agent references | keep as markdown section | informational only |
| `references/Matt-Pocock-*` | keep | these are valuable context |
| `tests/`, `documentation/`, `megaprompts/` | **drop** | these are repo-internal |

**Duplicate names across domains:** two skills can share a folder name across `<domain>` directories.
Use `<domain>-<skill>` as the Clawith folder name when this happens
(e.g., `engineering/handoff/` → `handoff`, `productivity/handoff/` → `productivity-handoff`).
Clawith's index table deduplicates by `name` (frontmatter field), so rename in the frontmatter if needed.

## 7. Where Clawith's existing skills live

```
backend/agent_template/                       ← default template (ships with every new agent)
├── soul.md
├── HEARTBEAT.md
├── memory/
├── skills/
│   └── mcp-installer/SKILL.md                ← canonical example of a Clawith folder-style skill
├── workspace/
└── state.json

backend/agent_templates/                      ← 22 domain-specific agent templates
├── chief-of-staff/   (soul.md + bootstrap.md)
├── code-reviewer/
├── seo-specialist/
├── growth-hacker/
├── devops-automator/
└── ... (22 total)
```

The 22 templates in `agent_templates/` are the **starting point** for the virtual company.
Several already overlap with the proposed 56-Agent blueprint (chief-of-staff, code-reviewer,
seo-specialist, growth-hacker, content-creator, frontend-developer, backend-architect, devops-automator).

## 8. Validation checklist

Before declaring a skill "ready":

- [ ] Frontmatter has `name` and `description` (≤ 200 chars)
- [ ] Description contains trigger keywords users actually say
- [ ] At least one `## When to Use` or `## When This Skill Activates` section
- [ ] At least one `## Workflow` section with concrete steps
- [ ] All `scripts/*.py` pass `python <script>.py --help` (exit 0)
- [ ] All `references/*.md` linked from SKILL.md body
- [ ] No references to `/cs:*` slash commands that the agent can't actually invoke
- [ ] No placeholder TODOs left in

## 9. Anti-patterns

- ❌ Hardcoded credentials or tenant IDs
- ❌ LLM calls inside `scripts/*.py` (defeats the sandbox model)
- ❌ `pip install` requirements in scripts (use stdlib only)
- ❌ Giant SKILL.md (> 2000 lines) — split into `references/` instead
- ❌ Vague description ("Helps with things") — be specific about triggers
- ❌ Frontmatter `description` > 200 chars (gets cut by the index table parser)