---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the bundled `references/web-interface-guidelines.md` relative to this loaded skill directory.
2. Read the requested source files with Astrion's file tools; infer the target from current context before asking for it.
3. Apply the relevant rules, checking actual component behavior and project constraints.
4. Output findings in the terse `file:line` format

## Guidelines Source

The complete rule snapshot is bundled, so reviews do not require a fresh network fetch. Its source is `vercel-labs/web-interface-guidelines`, pinned in the built-in library's UPSTREAM_LOCK.json. When updating rules, retrieve the repository's `command.md` through GitHub blob/CDN/API or available webpage tools and record the exact version. Do not invent a `WebFetch` tool or assume the raw GitHub domain is reachable.

## Usage

When a user provides a file or pattern argument:
1. Read the bundled rules.
2. Read the specified files and relevant component context.
3. Apply rules appropriate to the actual stack; distinguish confirmed defects from suggestions and note untested runtime behavior.
4. Report actionable `file:line` findings with impact and a concrete correction. Browser checks use available Playwright tools only when workspace policy permits; otherwise provide user acceptance steps.

If no files specified, ask the user which files to review.
