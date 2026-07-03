# PROMPTS

## Create new module

Create a new module following the project architecture.

Requirements:

- SQLAlchemy models
- Pydantic schemas
- Repository
- Service
- Routes
- SQLAdmin
- Tests

Follow the documentation inside `/docs`.

---

## Create entity

Create a new entity.

Requirements:

- SQLAlchemy model
- Pydantic schemas
- Repository
- Service
- CRUD routes
- SQLAdmin integration
- Tests

---

## Refactor module

Refactor the module without changing public API.

Improve readability.

Reduce duplication.

Keep architecture unchanged.

---

## Add integration

Create a new integration module.

Requirements:

- isolated module
- no business logic
- configuration from environment
- retry support
- logging
- tests

---

## Review code

Review the code.

Check:

- architecture
- readability
- typing
- SQLAlchemy best practices
- FastAPI best practices
- possible bugs
- unnecessary complexity

Suggest improvements only if they simplify the project.