---
name: tsstd
description: Apply TypeScript project standards (Biome, strict tsc, Vitest, Justfile, CI). Use when setting up or reviewing a TypeScript project for modern best practices.
argument-hint: "[check|apply|fix]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# TypeScript Project Standards

You are setting up or reviewing a TypeScript project to follow modern best practices. Apply these standards:

## Technology Stack

| Tool | Purpose | Command |
|------|---------|---------|
| **Biome** | Linting, formatting, import organizing (replaces ESLint + Prettier) | `npx biome check`, `npx biome format` |
| **tsc** | Type checking (strictest settings) | `npx tsc --noEmit` |
| **Vitest** | Testing (fast, TypeScript-native, V8 coverage) | `npx vitest run` |
| **just** | Task runner (replaces npm scripts sprawl) | `just fc`, `just test` |

## Required Files

### tsconfig.json (Maximum Strictness)

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noFallthroughCasesInSwitch": true,
    "noPropertyAccessFromIndexSignature": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,

    "target": "ESNext",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "verbatimModuleSyntax": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "forceConsistentCasingInImports": true,

    "outDir": "dist",
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "skipLibCheck": true,

    "jsx": "react-jsx"
  },
  "include": ["src"],
  "exclude": ["node_modules", "dist", "coverage"]
}
```

**Key strict options explained:**
- `noUncheckedIndexedAccess` — Array/object index access returns `T | undefined`, forcing null checks
- `exactOptionalPropertyTypes` — Distinguishes `{ x?: string }` (missing) from `{ x: undefined }` (present but undefined)
- `noPropertyAccessFromIndexSignature` — Forces bracket notation for index signatures, making dynamic access explicit
- `verbatimModuleSyntax` — Requires explicit `import type` for type-only imports, prevents runtime import side effects

Remove `"jsx": "react-jsx"` if not using React.

### biome.json (Biome v2, Aggressive Rules)

```json
{
  "$schema": "https://biomejs.dev/schemas/2.4.4/schema.json",
  "formatter": {
    "enabled": true,
    "indentStyle": "space",
    "indentWidth": 2,
    "lineWidth": 100
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true,
      "complexity": {
        "noExcessiveCognitiveComplexity": "warn",
        "noUselessTypeConstraint": "error"
      },
      "correctness": {
        "noUnusedImports": "error",
        "noUnusedVariables": "error",
        "noUnusedFunctionParameters": "warn",
        "useExhaustiveDependencies": "warn",
        "useHookAtTopLevel": "error"
      },
      "performance": {
        "noAccumulatingSpread": "warn",
        "noBarrelFile": "warn",
        "noReExportAll": "warn"
      },
      "style": {
        "noNonNullAssertion": "warn",
        "useForOf": "warn",
        "useTemplate": "error"
      },
      "suspicious": {
        "noExplicitAny": "error",
        "noConfusingVoidType": "error"
      }
    }
  },
  "assist": {
    "actions": {
      "source": {
        "organizeImports": "on"
      }
    }
  },
  "javascript": {
    "formatter": {
      "quoteStyle": "single",
      "trailingCommas": "all",
      "semicolons": "always"
    }
  }
}
```

**Aggressive rules explained:**
- `noExplicitAny: "error"` — Bans `any` type; use `unknown` and narrow instead
- `noUnusedImports: "error"` — Auto-removable by `biome check --write`
- `noBarrelFile: "warn"` — Discourages `index.ts` re-export barrels that hurt tree-shaking
- `noAccumulatingSpread: "warn"` — Flags O(n²) spread-in-loop patterns
- `noNonNullAssertion: "warn"` — Discourages `!` postfix; prefer proper null checks

### vitest.config.ts

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/**/*.d.ts'],
    },
  },
});
```

### Justfile

```just
set shell := ["bash", "-cu"]

default:
    @just --list

# Format code
fmt:
    npx biome format --write .

# Check formatting (no changes)
format-check:
    npx biome format .

# Lint code (no changes)
lint:
    npx biome lint .

# Fix lint + format + organize imports
lint-fix:
    npx biome check --write .

# Type check
type:
    npx tsc --noEmit

# Run tests
test:
    npx vitest run

# Run tests with coverage
test-cov:
    npx vitest run --coverage

# Full check (lint + format + imports, no fixes)
check:
    npx biome check .

# FIX + CHECK: Run before every commit
fc: lint-fix check type test

# CI pipeline (check-only, no fixes)
ci: check type test

# Install dependencies
install:
    npm install
```

### GitHub Actions CI (.github/workflows/ci.yml)

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:

permissions:
  contents: write

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        node-version: ["20", "22"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js ${{ matrix.node-version }}
        uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node-version }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Install just
        uses: taiki-e/install-action@v2
        with:
          tool: just

      - name: Lint, format, and type check
        run: |
          just check
          just type

      - name: Run tests with coverage
        run: npx vitest run --coverage

      - name: Generate coverage badge
        if: matrix.node-version == '22' && github.ref == 'refs/heads/main'
        run: node scripts/coverage-badge.mjs

      - name: Commit coverage badge
        if: matrix.node-version == '22' && github.ref == 'refs/heads/main'
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          git add assets/coverage.svg
          git diff --staged --quiet || git commit -m "chore: update coverage badge [skip ci]"
          git push
```

### Coverage Badge Script (scripts/coverage-badge.mjs)

Zero-dependency Node.js script that generates an SVG badge from Vitest coverage output:

```js
#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

const summary = JSON.parse(readFileSync('coverage/coverage-summary.json', 'utf8'));
const pct = Math.round(summary.total.lines.pct);
const color = pct >= 90 ? '#4c1' : pct >= 75 ? '#dfb317' : '#e05d44';
const w = pct === 100 ? 116 : 108;
const tx = pct === 100 ? 88 : 84;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="20">
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="a"><rect width="${w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#a)">
    <rect width="61" height="20" fill="#555"/>
    <rect x="61" width="${w - 61}" height="20" fill="${color}"/>
    <rect width="${w}" height="20" fill="url(#b)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11">
    <text x="30.5" y="15" fill="#010101" fill-opacity=".3">coverage</text>
    <text x="30.5" y="14">coverage</text>
    <text x="${tx}" y="15" fill="#010101" fill-opacity=".3">${pct}%</text>
    <text x="${tx}" y="14">${pct}%</text>
  </g>
</svg>`;

mkdirSync('assets', { recursive: true });
writeFileSync('assets/coverage.svg', svg);
console.log(`Coverage badge: ${pct}%`);
```

### README Badges

Add at the top of README.md (replace OWNER/REPO):

```markdown
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
![coverage](assets/coverage.svg)
```

### Initial Coverage Badge

Generate locally before first push:

```bash
npx vitest run --coverage
node scripts/coverage-badge.mjs
```

Add `coverage/` to `.gitignore`. The `assets/coverage.svg` file **is** committed — CI auto-updates it on pushes to main.

## Dev Dependencies

Install with:

```bash
npm install -D @biomejs/biome typescript vitest @vitest/coverage-v8
```

```json
{
  "devDependencies": {
    "@biomejs/biome": "^2.4.0",
    "typescript": "^5.7.0",
    "vitest": "^4.0.0",
    "@vitest/coverage-v8": "^4.0.0"
  }
}
```

## The `fc` Command

**Always run `just fc` before committing.** It runs:

1. `lint-fix` — Auto-fix lint, formatting, and import ordering via Biome
2. `check` — Verify everything passes (catches unfixable issues)
3. `type` — TypeScript type checking with strictest settings
4. `test` — Run full test suite

## Coverage Policy

- **CI generates the badge**: On pushes to main, CI runs coverage, generates an SVG via `scripts/coverage-badge.mjs`, and commits it to `assets/coverage.svg`
- **Local `just test` runs fast** without coverage overhead
- **`just test-cov`** runs coverage locally with terminal report
- The `[skip ci]` suffix prevents infinite CI loops when the badge is updated
- No external services (Codecov, Coveralls) — the badge is a self-contained SVG in the repo

## Agent Guidelines

1. **Always run `just fc` before committing** — non-negotiable
2. **No `any` type** — use `unknown` and narrow with type guards
3. **No non-null assertions** — prefer optional chaining, nullish coalescing, or proper checks
4. **Use `import type`** — separate type imports from value imports (`verbatimModuleSyntax` enforces this)
5. **Write tests** — new features need tests; bug fixes need regression tests
6. **Follow existing patterns** — match the project's conventions
7. **Do not add backward-compatibility shims** — clean up old code fully

## Project Type Adjustments

### Node.js (no bundler)
```jsonc
// tsconfig.json changes:
"module": "Node16",
"moduleResolution": "Node16"
// Remove "jsx" line
```

### Library (published to npm)
```jsonc
// tsconfig.json — keep declaration/declarationMap enabled
// Ensure "outDir" is set and included in package.json "files"
```

### React / Vite
```jsonc
// tsconfig.json: keep as-is (bundler + react-jsx is correct)
// Add to vitest.config.ts if using jsdom:
// test: { environment: "jsdom" }
```

### Electron
```jsonc
// May need separate tsconfigs for main (Node) vs renderer (browser)
// Main: "module": "CommonJS", "moduleResolution": "node"
// Renderer: "module": "ESNext", "moduleResolution": "bundler"
```

### Node.js with native TypeScript execution (Node 22+)
```jsonc
// tsconfig.json: add for --strip-types compatibility:
"erasableSyntaxOnly": true
// Disables enums and namespaces (only erasable syntax allowed)
```

## Common Issues & Troubleshooting

### `verbatimModuleSyntax` import errors

If you see errors about imports that should be type-only:
```ts
// Wrong — will error with verbatimModuleSyntax
import { MyType } from './types';

// Correct
import type { MyType } from './types';

// Mixed (values and types from same module)
import { myFunction, type MyType } from './module';
```

### `exactOptionalPropertyTypes` confusion

This option is strict about `undefined` vs missing:
```ts
interface Config {
  debug?: boolean; // Can be missing, but NOT explicitly `undefined`
}

const a: Config = { debug: undefined }; // ERROR
const b: Config = {}; // OK
```

If too strict for your codebase, remove `exactOptionalPropertyTypes` from tsconfig.

### `noUncheckedIndexedAccess` returns `T | undefined`

Array/object index access now returns `T | undefined`:
```ts
const arr = [1, 2, 3];
const x = arr[0]; // number | undefined

// Fix: check before use
if (arr[0] !== undefined) {
  console.log(arr[0]); // number
}

// Or use non-null assertion (avoid if possible)
const y = arr[0]!; // number
```

### Biome v2 migration from v1

If upgrading from Biome v1, run the automated migration:
```bash
npx @biomejs/biome migrate --write
```

Key v1 → v2 changes:
- `organizeImports.enabled` → `assist.actions.source.organizeImports: "on"`
- `include`/`ignore` → `includes` (use `!` prefix for exclusions)
- `--fix` → `--write`
- Several rules renamed or removed (migration command handles this)

### Biome vs tsc unused variable overlap

Both Biome (`noUnusedVariables`) and tsc (`noUnusedLocals`/`noUnusedParameters`) catch unused variables. This is intentional — Biome can auto-fix (prefix with `_`), tsc catches at type-check time. If double-reporting is annoying, disable one:
```jsonc
// Option A: Disable in tsc, let Biome handle it
"noUnusedLocals": false,
"noUnusedParameters": false

// Option B: Disable in Biome, let tsc handle it
// In biome.json: "noUnusedVariables": "off"
```

### Package manager alternatives

The Justfile uses `npx` which works universally. For pnpm or bun projects:
- `npx` → `pnpm exec` or `bunx`
- `npm install` → `pnpm install` or `bun install`
- `npm ci` → `pnpm install --frozen-lockfile` or `bun install --frozen-lockfile`

Update the CI workflow `cache` field accordingly (`'pnpm'` or remove for bun).

## Your Task

$ARGUMENTS

If no specific task was given, analyze this project and:
1. Check if the required files exist (tsconfig.json, biome.json, vitest.config.ts, Justfile, .github/workflows/ci.yml)
2. Report what's missing or needs updating
3. Offer to create/update the missing pieces
