# AGENTS.md — Verdict Archive

Rules for AI coding agents (Codex, Claude Code, Cursor) working in this repo.
`CLAUDE.md` imports this file; edit rules here only.

## Project shape

- **Pipeline (Python 3.12, `uv`)**: `scripts/` collects reports → transcribes → scores → backtests →
  writes static artifacts. There is no runtime database; the "DB" is versioned JSON/CSV artifacts.
- **Artifacts**: `src/data/*.json` (build-time inputs for pages), `public/api/v1/**` (public static REST API,
  described by `public/api/v1/openapi.json`, documented in `docs/API.md`).
- **Web (Next.js 16 App Router, React 19, TypeScript, Tailwind CSS v4, shadcn/ui new-york + Radix, Lucide)**:
  statically generated; the only route handler is `src/app/api/v1/signals/latest/route.ts`.

## Agent tooling setup (once per clone)

- shadcn Skill lives in `.agents/skills/shadcn` (pinned by `skills-lock.json`; Codex and Cursor read it directly).
  Claude Code reads `.claude/skills/`, which is per-machine and git-ignored:
  `npx skills add shadcn/ui --skill shadcn --agent claude-code -y`.
- shadcn MCP: `.mcp.json` (Claude Code, enabled in `.claude/settings.json`) and `.cursor/mcp.json` are committed.
  Codex keeps MCP config globally: `npx shadcn@latest mcp init --client codex`.

## Data / schema / API contract rules

1. Python writers are the source of truth. Every artifact the web reads carries `schema_version`.
   Changing a field means: bump `schema_version`, update the TS reader (`src/lib/*`, zod schema),
   `openapi.json`, `docs/API.md`, and the tests in the same change.
2. Validate external/generated JSON at the boundary with zod (`src/lib/report-model.ts` pattern).
   Do not `as`-cast raw JSON imports into domain types.
3. Invalid generated data must fail the build or the pipeline — never publish a cached error payload.
4. Public API paths under `/api/v1/` are frozen; breaking changes go to `/api/v2/`.
5. Never hand-edit generated artifacts or `tests/golden/*`. Regenerate via the scripts; golden updates
   require `UPDATE_GOLDEN=1` and an explicit reason in the commit message.
6. No look-ahead: any signal/score must only use data available at its `as_of` date.

## Frontend UI engineering rules

### UI component policy

Use shadcn/ui as the primary UI component system. Before implementing any UI feature:

1. Inspect existing components under `src/components/ui`.
2. Search the official shadcn registry with the shadcn Skill (`.agents/skills/shadcn`) or the shadcn MCP
   server (`.mcp.json`, `.cursor/mcp.json`); CLI fallback: `npx shadcn@latest search <query>`.
3. Read the component docs and examples (`npx shadcn@latest docs <component>`, https://ui.shadcn.com/docs).
4. Reuse an existing component, or install an official one: `npx shadcn@latest add <component>`.
   Use `--dry-run` / `--diff` first when the target file already exists.
   After every `add`, check `git diff package.json` and the new files' imports: on Windows the CLI has
   rewritten `@/lib/utils` to a bare `"cn"` import and installed the unrelated npm package `cn`.
   Imports must be `import { cn } from "@/lib/utils"`; uninstall any package you did not intend.
5. Compose app-level components (in `src/components/<feature>/`) from shadcn primitives.
6. Write a custom primitive only when no existing component satisfies the requirement, and say why.

### Design requirements

- Use Tailwind utilities and the tokens in `src/app/globals.css`. Preserve the existing theme, spacing,
  typography (Pretendard sans, Noto Serif display, tabular mono numerals) and radius.
- Korean market color convention: **up = red, down = blue**. Use `text-up` / `text-down` /
  `--finance-*` tokens; never `text-green-*` / `text-red-*` for P&L.
- Canvas charts cannot read CSS variables — resolve them with `cssHsl()` from `src/lib/chart-colors.ts`.
- Prefer compact, information-dense layouts. Every financial number uses `tabular-nums`.
- Data grids: shadcn `Table` primitives for markup. For sorting/filtering/pagination beyond a simple
  sort, propose TanStack Table (not installed yet — needs approval as a new dependency).
- Semantic HTML and Radix primitives; keyboard navigation and visible focus (`focus-visible:ring-ring`).
- Every data view handles loading, empty, error, and disabled states.
- Lucide icons only, `size-4` / `size-5`.
- Large JSON belongs in Server Components; pass only the slice a client component needs.

### Prohibited

- Recreating shadcn components from scratch (tables, dialogs, command palette, tabs, tooltips, selects).
- Adding another UI component library without approval.
- Hardcoded hex/HSL colors when a semantic token exists.
- Overwriting customized files in `src/components/ui` without reviewing `npx shadcn@latest add --diff`.
- Inventing component APIs or props — check the docs or the installed source.

## Verification (before claiming a task is done)

```bash
npx tsc --noEmit          # types
npm run lint              # eslint
node --test tests/report-model.test.cjs
uv run pytest             # pipeline, accounting oracles, golden gate, publication safety
npm run build             # production build (also validates generated API inputs)
```

For UI tasks also check a narrow (375px) viewport and keyboard-only navigation, then summarize which
components were reused, installed, and custom-built.
