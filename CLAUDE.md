# CLAUDE.md

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

### Available Skills

- `/office-hours` — Brainstorm a new idea
- `/plan-ceo-review` — Review a plan (strategy)
- `/plan-eng-review` — Review a plan (architecture)
- `/plan-design-review` — Review a plan (design)
- `/design-consultation` — Create a design system
- `/review` — Code review before merge
- `/ship` — Create PR / deploy
- `/land-and-deploy` — Land and deploy changes
- `/canary` — Canary deployment
- `/benchmark` — Performance benchmarking
- `/browse` — Web browsing and testing
- `/qa` — QA testing
- `/qa-only` — QA testing (test-only mode)
- `/design-review` — Visual design audit
- `/setup-browser-cookies` — Set up browser cookies for testing
- `/setup-deploy` — Set up deployment config
- `/retro` — Weekly retrospective
- `/investigate` — Debug errors
- `/document-release` — Post-ship doc updates
- `/codex` — Adversarial code review / second opinion
- `/cso` — Chief Security Officer review
- `/autoplan` — Auto-generate implementation plan
- `/careful` — Maximum safety mode
- `/freeze` — Scope edits to one module/directory
- `/guard` — Destructive warnings + edit restrictions
- `/unfreeze` — Remove edit restrictions
- `/gstack-upgrade` — Upgrade gstack to latest version

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.
