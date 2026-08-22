---
name: agents-md-writer
description: "Guide for creating AGENTS.md files — the standardized Markdown file that provides context and instructions to AI coding agents. Use this skill when the user wants to (1) Create a new AGENTS.md file for a project, (2) Improve or update an existing AGENTS.md, (3) Understand AGENTS.md best practices and conventions, (4) Migrate from legacy config files like .cursorrules or CLAUDE.md to AGENTS.md, or (5) Learn what AGENTS.md is and why it matters."
---

# AGENTS.md Writer

Create or improve AGENTS.md files — the open standard for guiding AI coding agents.

## What AGENTS.md Is

AGENTS.md complements README.md: README is for humans, AGENTS.md is for AI coding agents. Over 60k open-source projects use it. One file works across 20+ tools (Codex, Cursor, Windsurf, Devin, Aider, Gemini CLI, VS Code, etc.).

**Key principle:** Information that would help a new teammate onboard faster belongs in AGENTS.md.

## Workflow

### Step 1: Understand the Project

Before writing, discover the project context:

- Check for existing config files (`.cursorrules`, `CLAUDE.md`, `.aider.conf.yml` — these may contain content to migrate)
- Identify the tech stack (read `package.json`, `requirements.txt`, `Cargo.toml`, `go.mod`, etc.)
- Find build, test, and lint commands
- Note the project structure and any existing conventions

Ask the user about anything unclear: team conventions, preferred code style, security constraints.

### Step 2: Choose the Right Approach

Match the AGENTS.md to the project:

| Project Type | Approach |
|-------------|----------|
| Single repo (small/medium) | Single AGENTS.md at root |
| Monorepo | Root AGENTS.md + nested files per sub-package |
| Library/SDK | Lightweight, focus on build/test/API |

### Step 3: Generate the AGENTS.md

Use the knowledge gathered in Step 1 to fill in the appropriate template. Read [templates.md](references/templates.md) for template options:

- **Template 1**: General project (most common)
- **Template 2**: Monorepo root
- **Template 3**: Frontend project (React/Vue/Next.js)
- **Template 4**: Backend project (Node/Python/Go)
- **Template 5**: Lightweight (small projects/libraries)

The base template file is also available at `assets/AGENTS.md.template`.

### Step 4: Review & Refine

After generating, check:

- [ ] Every command listed actually works (test if possible)
- [ ] Code style rules match actual project conventions
- [ ] No duplicate information from README.md
- [ ] Sections are concise (prefer command examples over verbose explanations)
- [ ] Nested AGENTS.md files are created for monorepo sub-packages if needed

## Content Guidelines

### What to Include (Prioritized)

1. **Build and test commands** (always) — The most important section. Agents need exact commands.
2. **Code style conventions** — Naming, formatting, language features used/avoided
3. **Project structure overview** — Where things live, especially non-obvious locations
4. **Testing strategy** — Test locations, coverage expectations, how to run specific tests
5. **PR/commit conventions** — If the team follows specific rules

### What to Avoid

- Duplicating README.md content verbatim (complement, don't repeat)
- Overly verbose explanations (commands > paragraphs)
- Vague guidance ("write clean code" — be specific)
- Information that changes frequently (link to docs instead)

### Monorepo Rules

- Root AGENTS.md: global tooling, sub-package overview, cross-cutting conventions
- Sub-package AGENTS.md: package-specific commands, conventions, structure
- The closest AGENTS.md to a file wins

## Detailed Reference

For the full AGENTS.md specification, compatibility list, and migration guides, read [specification.md](references/specification.md).

For complete template examples across project types, read [templates.md](references/templates.md).
