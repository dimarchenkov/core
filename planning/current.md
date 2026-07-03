# Current Sprint

## Sprint Goal

Создать инфраструктуру проекта Core.

---

## Tasks

### Environment

- [x] Инициализировать проект через uv
- [x] Создать pyproject.toml
- [x] Создать src-layout

### Backend

- [x] Настроить FastAPI
- [x] Настроить конфигурацию
- [x] Настроить логирование

### Database

- [x] PostgreSQL
- [x] SQLAlchemy
- [x] Alembic

### Background jobs

- [x] Redis
- [x] RQ Worker

### Admin

- [x] SQLAdmin

SQLAdmin mounted and opens successfully. It has no registered model views yet because business models are intentionally out of scope for the infrastructure skeleton.

### Infrastructure

- [x] Docker Compose
- [x] Angie

### Validation

- [x] docker compose up
- [x] Swagger доступен
- [x] SQLAdmin открывается

---

## Out of Scope

- Product
- Variant
- AQSI
- Rental
## Definition of Done

Infrastructure is complete if:

- docker compose up works
- FastAPI is available
- Swagger opens
- SQLAdmin opens
- PostgreSQL connects
- Alembic migration works
- Redis works
- RQ worker starts
