---
name: bootstrap-app
description: Bootstrap a FastAPI + Tailwind mobile-first web app with dark mode. Use when the user wants to create a new web application project from scratch.
argument-hint: "[project-name]"
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Bootstrap FastAPI + Tailwind App

Bootstrap a new project with the following stack:
- **Backend**: FastAPI with async SQLite (SQLAlchemy)
- **Frontend**: Jinja2 templates with Tailwind CSS (CDN)
- **Styling**: Dark mode with system preference detection
- **Tooling**: Astral stack (uv, ruff, ty, just)

## IMPORTANT: Complete All Steps

This skill should complete ALL steps in a single execution. Do NOT stop after any individual step - continue through to the end, including installing dependencies and verifying the server starts.

## Step 1: Read Python Standards

First, read the Python project standards from `~/.claude/skills/pystd/SKILL.md` to get the current templates for:
- pyproject.toml structure
- Justfile recipes
- GitHub Actions CI workflow
- Ruff and ty configuration

Use those templates as the base, then apply the FastAPI-specific modifications below.

## Step 2: Create Project Structure

```
{project}/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── api/
│   │   ├── __init__.py
│   │   └── views.py         # HTML view routes
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py        # Pydantic settings
│   │   └── database.py      # Async SQLAlchemy setup
│   ├── models/
│   │   └── __init__.py      # SQLAlchemy models
│   ├── services/
│   │   └── __init__.py      # Business logic services
│   └── templates/
│       └── layouts/
│           └── base.html    # Base template with dark mode
├── static/
│   ├── css/
│   └── js/
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   └── __init__.py
│   └── integration/
│       └── __init__.py
├── .env.example
└── README.md
```

## Step 3: Modify pyproject.toml for FastAPI

Start with the pystd template, then apply these changes:

**Build system**: Use hatchling (NOT uv_build) since app is in `app/` not `src/`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

**Dependencies**: Add FastAPI stack:
```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.20.0",
    "python-multipart>=0.0.12",
    "jinja2>=3.1.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
]
```

**Dev dependencies**: Add pytest-asyncio:
```toml
[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.27.0",
    "ruff>=0.14.0",
    "ty>=0.0.8",
]
```

**Ruff**: Add B008 to ignores (for FastAPI Depends):
```toml
ignore = ["E501", "B008"]
```

**isort**: Set known-first-party to "app":
```toml
[tool.ruff.lint.isort]
known-first-party = ["app"]
```

**pytest**: Add asyncio_mode:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --tb=short"
```

## Step 4: Extend Justfile

Add these recipes to the pystd Justfile template:

```just
# Start development server
dev:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start production server
serve:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Step 5: Create Core Files

### app/core/config.py

```python
"""Application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///./app.db"

    # Environment
    environment: str = "development"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
```

### app/core/database.py

```python
"""Database connection and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize the database, creating all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

### app/main.py

```python
"""Main FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.views import router as views_router
from app.core.config import get_settings
from app.core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    await init_db()
    yield


settings = get_settings()

app = FastAPI(
    title="My App",
    description="A mobile-friendly web app",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(views_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
    )
```

### app/api/views.py

```python
"""Web views for HTML pages."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["views"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page."""
    return templates.TemplateResponse(
        request,
        "home.html",
        {},
    )
```

## Step 6: Create Dark Mode Base Template

### app/templates/layouts/base.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
    <title>{% block title %}My App{% endblock %}</title>
    <!-- Tailwind CSS via CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        primary: {
                            50: '#f0f9ff',
                            100: '#e0f2fe',
                            200: '#bae6fd',
                            300: '#7dd3fc',
                            400: '#38bdf8',
                            500: '#0ea5e9',
                            600: '#0284c7',
                            700: '#0369a1',
                            800: '#075985',
                            900: '#0c4a6e',
                        }
                    }
                }
            }
        }
    </script>
    <script>
        // Dark mode initialization - runs before page renders to prevent flash
        (function() {
            function getThemePreference() {
                const stored = localStorage.getItem('theme');
                if (stored === 'dark' || stored === 'light') {
                    return stored;
                }
                return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            }

            function applyTheme(theme) {
                if (theme === 'dark') {
                    document.documentElement.classList.add('dark');
                } else {
                    document.documentElement.classList.remove('dark');
                }
            }

            applyTheme(getThemePreference());

            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
                if (!localStorage.getItem('theme')) {
                    applyTheme(e.matches ? 'dark' : 'light');
                }
            });
        })();
    </script>
    <style>
        /* Smooth theme transitions */
        html.transitioning,
        html.transitioning *,
        html.transitioning *::before,
        html.transitioning *::after {
            transition: background-color 0.3s ease, border-color 0.3s ease, color 0.3s ease !important;
        }
    </style>
</head>
<body class="bg-gray-50 dark:bg-gray-900 min-h-screen transition-colors">
    <!-- Header -->
    <header class="bg-primary-600 dark:bg-gray-800 text-white sticky top-0 z-50 shadow-md">
        <div class="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
            <a href="/" class="text-xl font-bold">{% block header_title %}My App{% endblock %}</a>
            <nav class="flex items-center gap-4">
                {% block nav_links %}{% endblock %}
                <!-- Dark mode toggle -->
                <button id="theme-toggle" type="button"
                        class="p-2 rounded-lg hover:bg-primary-700 dark:hover:bg-gray-700 transition"
                        title="Toggle dark mode"
                        aria-label="Toggle dark mode">
                    <!-- Sun icon (shown in dark mode) -->
                    <svg id="theme-icon-light" class="hidden dark:block w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clip-rule="evenodd"/>
                    </svg>
                    <!-- Moon icon (shown in light mode) -->
                    <svg id="theme-icon-dark" class="block dark:hidden w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z"/>
                    </svg>
                </button>
            </nav>
        </div>
    </header>

    <!-- Main content -->
    <main class="max-w-4xl mx-auto px-4 py-6">
        {% block content %}{% endblock %}
    </main>

    <!-- Footer -->
    <footer class="bg-gray-100 dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 mt-auto py-4 text-center text-gray-600 dark:text-gray-400 text-sm">
        <div class="max-w-4xl mx-auto px-4">
            {% block footer %}My App{% endblock %}
        </div>
    </footer>

    <script>
        // Theme toggle functionality
        document.getElementById('theme-toggle').addEventListener('click', function() {
            document.documentElement.classList.add('transitioning');
            const isDark = document.documentElement.classList.contains('dark');
            const newTheme = isDark ? 'light' : 'dark';

            if (newTheme === 'dark') {
                document.documentElement.classList.add('dark');
            } else {
                document.documentElement.classList.remove('dark');
            }

            localStorage.setItem('theme', newTheme);

            setTimeout(function() {
                document.documentElement.classList.remove('transitioning');
            }, 300);
        });
    </script>

    {% block scripts %}{% endblock %}
</body>
</html>
```

### app/templates/home.html

```html
{% extends "layouts/base.html" %}

{% block title %}Home - My App{% endblock %}

{% block content %}
<div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Welcome</h1>

    <div class="bg-white dark:bg-gray-800 rounded-lg shadow p-6 border border-gray-200 dark:border-gray-700">
        <p class="text-gray-600 dark:text-gray-300">
            Your app is ready! Edit this template to get started.
        </p>
    </div>
</div>
{% endblock %}
```

## Step 7: Create .env.example

```
DATABASE_URL=sqlite+aiosqlite:///./app.db
ENVIRONMENT=development
```

## Step 8: Create Empty __init__.py Files

Create empty `__init__.py` files in:
- `app/`
- `app/api/`
- `app/core/`
- `app/models/`
- `app/services/`
- `tests/`
- `tests/unit/`
- `tests/integration/`

## Step 9: Create README.md

```markdown
# {Project Name}

A mobile-friendly web app built with FastAPI and Tailwind CSS.

## Development

\`\`\`bash
# Install dependencies
uv sync --dev

# Start development server
just dev

# Run tests
just test

# Format and lint
just fc
\`\`\`
```

## Step 10: Install and Verify

After creating all files:

```bash
uv sync --dev
timeout 5 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 || true
```

The app should start successfully. Report success to the user.

## Your Task

$ARGUMENTS

If no specific arguments provided, complete ALL of these steps in order:
1. Read ~/.claude/skills/pystd/SKILL.md for Python tooling templates
2. Create the directory structure (mkdir -p for all directories)
3. Create pyproject.toml (pystd base + FastAPI modifications + hatchling)
4. Create Justfile (pystd base + dev/serve commands)
5. Create .github/workflows/ci.yml (from pystd template)
6. Create all core Python files (config.py, database.py, main.py, views.py)
7. Create the dark mode base template and home template
8. Create .env.example
9. Create all empty __init__.py files
10. Create README.md
11. Run `uv sync --dev` to install dependencies
12. Verify the server starts with a timeout test

Use the current directory name as the project name. Adjust the app title in main.py and base.html to match.
