# Deployment and Bootstrap Goals

## Цель

Развёртывание Core должно быть понятным для собственного магазина, подписной поставки и франшизы.

Целевое время от подготовленного сервера до первого входа — не более десяти минут.

## Целевой сценарий

```text
1. Получить версию Core.
2. Скопировать и заполнить файл окружения.
3. Запустить Docker Compose.
4. Дождаться готовности PostgreSQL, Redis, API, worker и Angie.
5. Автоматически применить или явно выполнить миграции одной командой.
6. Запустить bootstrap и создать первого администратора.
7. Открыть health check и войти в систему.
```

## Требования

- Один документированный основной способ развёртывания.
- Angie является единственным reverse proxy проекта.
- Секреты не хранятся в Git.
- Bootstrap безопасно сообщает о незаполненной конфигурации и устаревшей схеме БД.
- Повторный запуск не должен создавать дубликаты или повреждать данные.
- Health checks покрывают обязательные сервисы.
- Обновление версии включает резервную копию, миграции и проверку работоспособности.
- Для подписки или франшизы допускается автоматизированная конфигурация экземпляра, но бизнес-логика остаётся общей.

## Master key для интеграций

Обычная self-hosted установка не требует ручной генерации ключа. API и worker монтируют один
persistent Docker volume `core_secrets`; канонический путь внутри контейнеров:

```text
/var/lib/core/secrets/master_encryption_key
```

Порядок разрешения:

1. explicit `MASTER_ENCRYPTION_KEY` из environment/secret manager;
2. существующий persistent key file;
3. атомарная генерация нового Fernet key при первом запуске.

Первый процесс получает exclusive `flock` на соседнем lock-файле, генерирует ключ через
`cryptography.fernet.Fernet.generate_key()`, записывает temporary file, выполняет `fsync`,
`chmod 0600` и атомарный `os.replace`. Второй одновременно запущенный процесс ждёт lock и читает
уже опубликованный файл. Ключ не печатается в логах.

Malformed environment value или повреждённый key file останавливают startup. Core не создаёт
замену. Если explicit environment key отличается от существующего persistent file либо не может
расшифровать сохранённые `IntegrationCredential`, startup также прекращается. Автоматического
выбора, rotation или re-encryption нет.

### Backup и recovery

Минимальный recoverable backup состоит из двух частей:

```text
PostgreSQL backup + matching master_encryption_key backup
```

`core_secrets` — отдельный volume и не входит автоматически в database backup. Не выполняйте
`docker compose down -v`, пока key backup не проверен. Пример отдельного защищённого backup:

```bash
mkdir -p backups
chmod 700 backups
docker compose run --rm --no-deps -v "$PWD/backups:/backup" api \
  sh -c 'umask 077; cp /var/lib/core/secrets/master_encryption_key /backup/master_encryption_key'
```

При recovery сначала восстановите key file с mode `0600` в `core_secrets`, затем базу данных и
только после этого запускайте API/worker. Потеря matching key делает provider credentials
нерасшифровываемыми; их придётся ввести заново.

`MASTER_ENCRYPTION_KEY` остаётся поддержанным production override для внешнего secret manager.
Автоматическая rotation намеренно отсутствует; безопасная rotation с coordinated re-encryption —
будущая отдельная операция.

## Bootstrap MVP

Bootstrap должен как минимум:

- проверить обязательную конфигурацию;
- проверить доступность базы данных;
- применить или подсказать применение миграций;
- создать первого администратора;
- не выдавать постоянные superuser-права;
- вывести адрес приложения и следующие действия.

## Definition of Done

Deployment считается понятным, если новый оператор может развернуть Core только по документации, без чтения исходного кода и ручных запросов к PostgreSQL.
