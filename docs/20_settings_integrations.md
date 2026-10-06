# Настройки и интеграции

## Пользовательская граница

В обычном Core появился административный раздел `Настройки → Интеграции`. SQLAdmin не является
рабочим способом подключения провайдеров. Первый экран поддерживает AQSI: состояние подключения,
включение/выключение, сохранение или замену API key, безопасную проверку подключения и выбор
магазина из shop IDs, которые уже умеет получать текущий adapter.

Устройства AQSI пока не обнаруживаются: существующий adapter не имеет подтверждённого read API
для их списка. Статические device IDs не переносятся в новый UI как «обнаруженные» значения.

## Модель

`Integration` хранит provider, имя, enabled state и JSON-конфигурацию только для несекретных
provider-параметров. Provider не уникален: несколько подключений AQSI допустимы на уровне модели.
Полный role-assignment UI пока не создан. Capability registry отделяет роли провайдера и сейчас
утверждает для AQSI реализованные `payment`, `fiscalization` и `catalog_projection`.
Payment/fiscalization доступны только в существующем изолированном spike и не объявляются
готовым Sales/POS.

В Core пока нет Store/Location/TradePoint. Sprint 7.13a не вводит пустую multi-tenant модель.
Будущая Store-модель должна получить отдельные назначения Integration по роли. До этого
единственная AQSI Integration обслуживает ручной и автоматический publication workflow; несколько
AQSI Integration делают legacy workflow неоднозначным и он безопасно останавливается.

## Секреты

`IntegrationCredential` хранит API key только в `secret_ciphertext` вместе с алгоритмом и версией
master key. Используется Fernet из `cryptography`: AES-128-CBC для шифрования и HMAC-SHA256 для
аутентификации согласно стандартному формату Fernet. Самописной криптографии нет.

При обычном Docker deployment master key автоматически создаётся один раз в shared volume
`core_secrets` (`/var/lib/core/secrets/master_encryption_key`) и затем одинаково загружается API и
worker. Explicit `MASTER_ENCRYPTION_KEY` остаётся приоритетным override для external secret
manager, но не может молча заменить другой persisted key. Master key не хранится в базе.
Plaintext API key существует только во время initial save/replacement и server-side provider call.
Read API возвращает только даты сохранения/ротации; кнопки «Показать ключ» нет.

Администратор может отдельно включить автоматическую проекцию каталога. По умолчанию она
выключена. Worker регистрирует один повторяемый sweep раз в пять минут; он выбирает только активные
Product/Variant, применяет существующую Ready for Sale проверку и ставит в очередь только новую
или изменившуюся canonical AQSI projection. Кнопка `Синхронизировать сейчас` запускает тот же
идемпотентный sweep немедленно и служит явным повтором после исправления ошибки.

Этот механизм обнаруживает локальные изменения Core по payload hash, но пока не выполняет
периодический remote drift read. Архивирование/удаление также не вызывает неподтверждённую
destructive AQSI operation.

Database backup должен сопровождаться matching backup master-key file. Повреждённый файл,
невалидный environment override или ключ, не способный расшифровать существующие credentials,
останавливает startup без генерации замены. Automatic key rotation отсутствует до появления
coordinated re-encryption workflow.

Текущая Identity поддерживает только флаги administrator/superuser, поэтому весь Settings API
ограничен этим минимальным безопасным уровнем. Отдельные `integration.view`,
`integration.manage`, `integration.credentials.manage` появятся вместе с granular permissions.

## AQSI runtime и миграция

Порядок разрешения конфигурации:

1. если существует Settings-backed AQSI Integration, используется только она;
2. если Integration выключена, legacy env не включает её обратно;
3. если Integration отсутствует, временно используются `CORE_AQSI_*`;
4. UI показывает предупреждение об устаревшей конфигурации и действие переноса.

Connection test расшифровывает ключ только на сервере и выполняет безопасный `GET /v2/Shops/list`.
Ответ и log record содержат только операторское сообщение и sanitized error code, без request
headers, provider payload или API key.

## Источник истины

Core является authoritative source для Catalog, Pricing и обычных Sales/POS workflows. Каталоги
касс и других провайдеров — односторонние проекции Core Catalog, прежде всего для
degraded/fallback operation. Редактирование имени или цены в AQSI никогда автоматически не
обновляет Core.

Будущий импорт фактов внешней продажи может идти provider → Core как отдельный workflow, но это не
двусторонняя синхронизация каталога. Автоматическая проекция Core → AQSI не означает
reconciliation, Sales/POS, refunds или fallback-sale import.
