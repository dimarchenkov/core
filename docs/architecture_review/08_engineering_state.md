# Architecture Review v2 — Engineering State after Sprint 8

## Verification environment

Audit date: 2026-07-24. Baseline: `49acd72`, tag `v0.5.0`.
Python resolved by uv: CPython 3.13.12.

## Executed checks

| Check | Command | Result |
| --- | --- | --- |
| Ruff | `uv run ruff check .` | Passed: `All checks passed!` |
| pytest | `uv run pytest` | Passed: 218 collected, 218 passed in 67.33 s |
| Alembic heads | `uv --cache-dir /private/tmp/core-audit-uv-cache run alembic heads` | Passed: one head, `0016_create_activity_events` |
| Alembic history | `uv --cache-dir /private/tmp/core-audit-uv-cache run alembic history` | Passed: linear chain `0001` → `0016` |
| Docker Compose syntax | `docker compose config --quiet` | Passed |
| Clean PostgreSQL migration smoke test | `docker compose up -d postgres` | Not executed: Docker daemon was not running. First sandbox attempt could not access the socket; approved retry reported `Cannot connect to the Docker daemon`. |

The first parallel Alembic invocation failed because uv could not initialize its default cache
inside the sandbox. It was rerun with an isolated writable cache and passed. This is an audit
environment issue, not a repository result.

## Test and lint configuration

pytest and Ruff are declared in the dev dependency group (`pyproject.toml:26-30`). pytest uses
`tests/` and `src` import path (`pyproject.toml:61-63`). Ruff targets Python 3.13, 100-character
lines and enables annotations, bugbear, docstrings, pycodestyle, Pyflakes, import ordering and
pyupgrade rules (`pyproject.toml:37-59`).

The release documentation reports 218 automated tests, matching actual collection
(`docs/releases/v0.5.0.md:31`, pytest result above).

## Static typing and coverage

- mypy is not declared or configured in `pyproject.toml`;
- coverage/pytest-cov is not declared or configured;
- therefore no project-defined mypy or coverage check was available to run.

The absence of these checks is an engineering-state fact, not a failing test result.

## Makefile, pre-commit and CI

The following requested artifacts are absent at `v0.5.0`:

- `Makefile`;
- `.pre-commit-config.yaml`;
- `.github/workflows/`.

Consequently there is no repository-defined Make target, pre-commit pipeline or GitHub Actions
quality gate. The documented local finish gate is only:

```text
uv run ruff check .
uv run pytest
```

(`.codex.md:50-57`).

## Docker Compose

`docker-compose.yml` is syntactically valid. It defines:

- `api` and `worker` from the same application image;
- PostgreSQL 17 with a healthcheck;
- Redis 8 with a healthcheck;
- Angie as the only reverse proxy;
- persistent PostgreSQL data and mounted local media storage.

The API waits for healthy PostgreSQL and Redis (`docker-compose.yml:12-17`), while Angie only
depends on API container start, not API health (`docker-compose.yml:52-58`). This matches the
existing infrastructure backlog note rather than a new audit discovery.

Runtime startup and migration application could not be reconfirmed because the Docker daemon was
unavailable. The Compose configuration check itself passed.

## Alembic

There are 16 migration files and a single linear head. Sprint 8 migrations are:

- `0015_create_intake_sessions`;
- `0016_create_activity_events`.

Engineering risk: `migrations/env.py:8-16` imports every other persisted context but does not
import `core.activity.models`. `ActivityEvent` is declared on shared `Base.metadata`
(`src/core/activity/models.py:17-20`), and migration `0016` creates its table
(`migrations/versions/0016_create_activity_events.py:22-62`). Therefore Alembic's target metadata
does not include the Sprint 8 Activity table in an env.py-only migration/autogenerate process.
The hand-written history remains valid, but metadata-based drift/autogenerate is not complete.

## Documentation and README

Documentation is extensive: 38 Markdown files under `docs`, including product/domain documents,
two ADRs, five previous architecture-review documents and release notes.

Current documentation strengths:

- Sprint 8 release notes correspond to code and test count;
- transaction and workflow decisions are explicit;
- Intake, operational visibility and Ready for Sale have dedicated documents;
- README documents `/app`, AQSI configuration and identity CLI.

Drift found:

- `docs/04_architecture.md` still presents `/mobile/intake` as the first UI, while the actual and
  README entry point is `/app` (`src/core/web/routes.py:12-15`, `README.md:36-40`);
- README lists Tilda and Rental among “Основные возможности” although no implementation is
  registered (`README.md:17-25`, `src/core/main.py:36-50`);
- architecture examples still describe future modules that remain reserved packages, which is
  acceptable only when read as target architecture.

## Engineering summary

The executable Python state is healthy under the configured local checks: all lint and automated
tests pass, endpoint composition imports successfully through the test suite, Compose syntax is
valid and migration history is linear. The principal engineering control gaps are missing CI,
pre-commit, typing and coverage gates; the principal Sprint 8 migration defect is incomplete
Alembic model registration for ActivityEvent.

