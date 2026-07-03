# CONVENTIONS

## Python

Version:

Python 3.13

Package manager:

uv

---

## Formatting

- Ruff
- Black
- isort

---

## Typing

Type hints are mandatory.

Avoid Any whenever possible.

---

## Naming

Classes:

PascalCase

Variables:

snake_case

Constants:

UPPER_CASE

---

## Database

PostgreSQL

SQLAlchemy 2

Alembic

UUID primary keys.

---

## API

FastAPI

REST API

JSON only.

---

## Commits

Good:

Add Variant service

Generate SKU

Create Price entity

Bad:

fix

update

changes

misc

---

## Code style

Functions should be short.

Services should do one thing.

Repositories should only access database.

Routes should only receive and return data.

No business logic inside routes.

## Documentation and typing

All public functions and service methods must have readable docstrings.

Docstrings should explain what the function does in business terms, not just repeat the function name.

Type hints are mandatory for:

- function arguments;
- function return values;
- important variables when the type is not obvious.

Avoid untyped functions.

Avoid `Any` unless there is a clear reason.