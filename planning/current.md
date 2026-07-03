# Current Sprint

## Sprint Goal

Создать инфраструктуру проекта Core.

---

## Tasks

### Environment

- [ ] Инициализировать проект через uv
- [ ] Создать pyproject.toml
- [ ] Создать src-layout

### Backend

- [ ] Настроить FastAPI
- [ ] Настроить конфигурацию
- [ ] Настроить логирование

### Database

- [ ] PostgreSQL
- [ ] SQLAlchemy
- [ ] Alembic

### Background jobs

- [ ] Redis
- [ ] RQ Worker

### Admin

- [ ] SQLAdmin

### Infrastructure

- [ ] Docker Compose
- [ ] Angie

### Validation

- [ ] docker compose up
- [ ] Swagger доступен
- [ ] SQLAdmin открывается

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