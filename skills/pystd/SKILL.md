---
name: pystd
description: Apply Python project standards (Astral stack, Justfile, CI). Use when setting up or reviewing a Python project for modern best practices.
argument-hint: "[check|apply|fix]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Python Project Standards

You are setting up or reviewing a Python project to follow modern best practices. Apply these standards:

## Technology Stack (Astral Stack)

| Tool | Purpose | Command |
|------|---------|---------|
| **uv** | Package management, virtual environments, Python version management | `uv sync`, `uv run`, `uv build` |
| **ruff** | Linting and formatting (replaces Flake8, Black, isort, pyupgrade) | `uv run ruff check`, `uv run ruff format` |
| **ty** | Type checking (fast alternative to mypy/pyright) | `uv run ty check` |
| **just** | Task runner (replaces Makefile) | `just fc`, `just test` |

## Required Files

### pyproject.toml

```toml
[project]
name = "project-name"
version = "0.1.0"
description = "Short project description"
readme = "README.md"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["uv_build>=0.8.17,<0.9.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "pytest>=9.0.0",
    "pytest-cov>=6.0.0",
    "genbadge[coverage]>=1.1.3",
    "ruff>=0.14.0",
    "ty>=0.0.8",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E501"]

[tool.ruff.lint.isort]
known-first-party = ["package_name"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.ty.environment]
python-version = "3.12"

# NOTE: ty has limited pyproject.toml configuration options.
# Only these top-level keys are valid: environment, src, rules, terminal, analysis, overrides
# To exclude files, use --exclude on the command line (see Justfile).
# [[tool.ty.overrides]] accepts: include, exclude, rules (NOT "ignore")

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
```

### Justfile

```just
set shell := ["bash", "-cu"]

default:
    @just --list

fmt:
    uv run ruff format .

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check . --fix

type:
    uv run ty check .
    # To exclude files with expected warnings (e.g., optional package imports):
    # uv run ty check . --exclude "src/mypackage/optional_module.py"

test:
    uv run pytest

# Run tests with coverage report
test-cov:
    uv run pytest --cov=src --cov-report=term-missing

# FIX + CHECK: Run before every commit
fc: fmt lint-fix lint type test

ci: lint format-check type test

install:
    uv sync --dev
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
        python-version: ["3.12", "3.13", "3.14"]

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Set up Python ${{ matrix.python-version }}
        run: uv python install ${{ matrix.python-version }}

      - name: Install dependencies
        run: uv sync --dev

      - name: Install just
        uses: taiki-e/install-action@v2
        with:
          tool: just

      - name: Lint and type check
        run: |
          just lint
          just format-check
          just type

      - name: Run tests with coverage
        run: uv run pytest --cov=src --cov-report=xml --cov-report=term-missing

      - name: Generate coverage badge
        if: matrix.python-version == '3.14' && github.ref == 'refs/heads/main'
        run: |
          mkdir -p assets
          uv run genbadge coverage -i coverage.xml -o assets/coverage.svg

      - name: Commit coverage badge
        if: matrix.python-version == '3.14' && github.ref == 'refs/heads/main'
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          git add assets/coverage.svg
          git diff --staged --quiet || git commit -m "chore: update coverage badge [skip ci]"
          git push
```

### README Badges

Add these badges at the top of your README.md (replace OWNER/REPO with your GitHub path):

```markdown
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
![coverage](assets/coverage.svg)
```

### Initial Coverage Badge

Generate an initial badge locally before the first push so the README doesn't show a broken image:

```bash
mkdir -p assets
uv run pytest --cov=src --cov-report=xml
uv run genbadge coverage -i coverage.xml -o assets/coverage.svg
```

Add `coverage.xml` and `htmlcov/` to `.gitignore`. The `assets/coverage.svg` file **is** committed — CI auto-updates it on pushes to main.

## The `fc` Command

**Always run `just fc` before committing.** It runs: Format -> Lint-fix -> Lint -> Type check -> Test

## Coverage Policy

- **CI generates the badge**: On pushes to main, CI runs coverage, generates an SVG badge via `genbadge`, and commits it to `assets/coverage.svg`
- **Local `just test` runs fast** without coverage overhead
- **`just test-cov`** runs coverage locally with a terminal report (for development use)
- The `[skip ci]` commit message suffix prevents infinite CI loops when the badge is updated
- No external services (Codecov, Coveralls, etc.) — the badge is a self-contained SVG in the repo

## Agent Guidelines

1. **Always run `just fc` before committing** - non-negotiable
2. **Do not add backward-compatibility shims** - clean up old code fully
3. **Use type hints** - all new code should be fully typed
4. **Write tests** - new features need tests; bug fixes need regression tests
5. **Follow existing patterns** - match the project's established conventions

## Common Issues & Troubleshooting

### ty type checker limitations

ty (v0.0.8+) is fast but has limited configuration compared to mypy/pyright:

- **Excluding files**: Use `--exclude` on command line, NOT pyproject.toml
  ```bash
  uv run ty check . --exclude "src/bot/*.py" --exclude "src/optional.py"
  ```
- **Ignoring rules**: Use `--ignore <RULE>` on command line
  ```bash
  uv run ty check . --ignore possibly-missing-attribute
  ```
- **pyproject.toml valid keys**: Only `environment`, `src`, `rules`, `terminal`, `analysis`, `overrides`
- **overrides valid keys**: `include`, `exclude`, `rules` (NOT "ignore")

### uv sync failures with incompatible packages

If `uv sync --dev` fails due to package incompatibility (e.g., torch/basicsr on Python 3.13):

1. Use `[project.optional-dependencies]` for problematic packages
2. In Justfile, install dev tools directly:
   ```just
   install:
       uv pip install -e . && uv pip install ruff ty pytest pytest-cov
   ```
3. Or use `.venv/bin/ruff` directly instead of `uv run ruff`

### Telegram/async library type warnings

Libraries like `python-telegram-bot` have optional attributes that ty flags as `possibly-missing-attribute`. Add these files to ty exclusions rather than suppressing project-wide.

## Your Task

$ARGUMENTS

If no specific task was given, analyze this project and:
1. Check if the required files exist (pyproject.toml, Justfile, .github/workflows/ci.yml)
2. Report what's missing or needs updating
3. Offer to create/update the missing pieces
