# Core

Core — внутренняя система управления каталогом товаров, складом, арендой и публикацией товаров проекта 2010shop.

Core развивается как операционная платформа для небольших магазинов и сервисов аренды. Ядро предоставляет API-first бизнес-процессы; интерфейс является заменяемым клиентом.

## Документация

- [Описание продукта](docs/00_product.md)
- [Принципы Core](docs/01_principles.md)
- [Пользовательские пути](docs/02_user_journeys.md)
- [Язык предметной области](docs/domain.md)
- [Архитектура](docs/04_architecture.md)
- [Deployment и bootstrap](docs/11_deployment_bootstrap.md)
- [Структура продуктовой документации](docs/12_product_documentation.md)
- [Публикация товаров в AQSI](docs/17_aqsi_publication.md)

## Основные возможности

- Каталог товаров
- Остатки
- Приемка
- Фото товаров
- Генерация SKU
- Генерация штрихкодов
- Работа с AQSI
- Работа с Tilda
- Аренда оборудования

## Стек

- Python 3.13
- uv
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- SQLAdmin
- Redis
- RQ
- Docker Compose

## Статус

Проект находится в стадии активной разработки.

## AQSI product publication

AQSI is disabled by default. Product publication requires a generated external
API key in the private `.env` runtime file and `CORE_AQSI_ENABLED=true`. Docker
Compose loads committed defaults from `.env.example` and then applies optional
local overrides from the git-ignored `.env`. Never put a real AQSI key in
`.env.example` or commit it.

The first installation uses AQSI VAT code `6` (`НДС не облагается`) and creates
the deterministic `Товары Core` goods category. If the AQSI account has one
active shop, Core selects it automatically. For multiple shops, configure
`CORE_AQSI_SHOP_ID` explicitly.

Core publishes through a background worker. The API records the requesting
user and returns HTTP 202; the worker creates or updates the good, binds its
retail price to the selected shop and verifies the resulting AQSI projection.

The real API key is not required for unit and integration tests. A controlled
live smoke test should use one dedicated test Variant after migrations and
runtime configuration are complete.

## Administrative CLI

Administrative commands should normally run inside the Docker API container. It
contains the application environment and has access to PostgreSQL.

### Create an administrator

```bash
docker compose exec api uv run python -m core identity create-admin
```

This interactive command asks for an email, full name, password, and password
confirmation. It creates an active administrator account, but does not grant
emergency superuser access. Passwords must contain at least 8 characters and
must not be empty or whitespace-only.

### Enable emergency superuser access

```bash
docker compose exec api uv run python -m core identity enable-superuser \
  <email> \
  --reason "<reason>"
```

Emergency superuser access is available only through the local CLI. Enabling it
requires a non-empty reason and writes a privilege audit event. It must not be
used for normal daily work.

### Disable emergency superuser access

```bash
docker compose exec api uv run python -m core identity disable-superuser \
  <email> \
  --reason "<reason>"
```

This disables emergency privileges and writes a privilege audit event. A reason
is optional in code, but always providing one is recommended.

### Help commands

```bash
docker compose exec api uv run python -m core identity --help

docker compose exec api uv run python -m core identity enable-superuser --help
```

### Local non-Docker usage

```bash
uv run python -m core identity <command>
```

Local execution requires a valid `DATABASE_URL` and a reachable PostgreSQL
instance.

Never run identity CLI commands inside the `postgres`, `worker`, or
`angie` containers: use the `api` container. Do not expose these CLI
commands through a public HTTP endpoint.
