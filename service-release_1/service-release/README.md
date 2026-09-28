# Аналитические сервисы 0.5.0 — комплект передачи

Два HTTP-сервиса для внешнего бэка: deterministic и VLM → LLM. Платформа проверяемой поставки — Linux/amd64. PostgreSQL 16.15-alpine включён в архив образов; для запуска контейнеров загрузка образов из registry не нужна. Обычный модельный анализ требует сети до удалённого OpenAI-compatible gateway и вашего ключа. UI, CV-детектор и адаптер внешнего бэка не входят в комплект.

## Быстрый запуск

Нужны Docker с Compose v2 и свободные порты. Все команды выполняются в каталоге этого комплекта.

```text
docker load -i images/analytics-images.tar
```

Скопируйте `.env.example` в `.env`. Задайте четыре непустых независимых секрета: `POSTGRES_PASSWORD`, `ANALYTICS_DETERMINISTIC_DB_PASSWORD`, `ANALYTICS_VLM_DB_PASSWORD`, `ANALYTICS_SERVICE_TOKEN`. Для обычных модельных запросов добавьте `LITELLM_API_KEY` и проверьте `LITELLM_GATEWAY_URL`/`ANALYTICS_MODEL`. Реальные секреты в комплект не входят. Не передавайте заполненный `.env` другим командам.

```text
docker compose config --quiet
docker compose up -d
docker compose ps -a
docker compose logs bootstrap
```

Bootstrap выполняет добавочные миграции 005 и 006, проверенный импорт двух эталонных справочников и подготовку ограниченных LOGIN-пользователей. Он должен завершиться с кодом 0. Приложения запускаются после него. База хранится в именованном `pg_data`; SQLite в `references/` — только источник импорта, приложения его не читают.

Адреса по умолчанию: deterministic `http://127.0.0.1:18001`, VLM `http://127.0.0.1:18002`. Порт БД — `127.0.0.1:15432`. Их можно изменить переменными `DETERMINISTIC_PORT`, `VLM_LLM_PORT`, `POSTGRES_PORT`. Публикация привязана к localhost; внешний бэк в той же Compose-сети использует `http://deterministic:8000` и `http://vlm_llm:8000`. Для подключения с другой машины настройте сетевую публикацию или свой reverse proxy и передачу Bearer-токена.

## Готовность и API

`GET /health/live` проверяет процесс. `GET /health/ready` проверяет конфигурацию и БД, не вызывает модель. Без gateway-ключа readiness VLM возвращает 503; его Docker healthcheck проверяет только live. План только из no_class и отсутствие плана могут обрабатываться без ключа и без платных вызовов. Обычный concrete/смешанный план требует ключа. Deterministic независим от моделей.

Все `/v1/*` и `/v2/*` требуют `Authorization: Bearer <ANALYTICS_SERVICE_TOKEN>`. Справочник: `/v1/catalog`, возможности: `/v1/capabilities`, анализ: `POST /v1/analyze/frame`, поиск: `/v1/analyses/by-request/{request_id}?site_id=...`. Полная семантика — [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md), схема — [contracts/frame-analysis-v1.schema.json](contracts/frame-analysis-v1.schema.json), экспорт — [catalog/catalog.json](catalog/catalog.json).

Принимаются concrete и no_class; summary/неизвестные ID отклоняются. no_class не выбирается по изображению. Незавершённая no_class-работа блокирует последовательный переход даже при подтверждении более позднего пункта. После завершения учитываются только наблюдения не раньше actual_completed_at, либо effective_at при отсутствии фактического времени. Сроки no_class проверяет deterministic по внешним подтверждениям; модельный сервис сроки/переход не оценивает.

Ресурсные маршруты `/v2/analyze/frame`, `/v2/capabilities`, `/v2/analyses/by-request/{request_id}?site_id=...` реализованы в обоих сервисах. Поля, ограничения, ошибки и таблица совместимости: [RESOURCE_INTEGRATION.md](RESOURCE_INTEGRATION.md). Deterministic сравнивает ресурсы, VLM возвращает not_evaluated. В v2 перенесены equipment_activity и work_progress.

## Запускаемые примеры

В `examples/` находятся исходная синтетическая иллюстрация и явно демонстрационные планы/CV/progress. Это проверка контракта, не реальная разметка стройки и не оценка распознавания. `examples/index.json` перечисляет ожидаемые ветки; `examples/responses/` содержит записанные результаты фактических HTTP-прогонов на тестовой БД, а не выдуманный модельный ответ.

В окружении клиента задайте `ANALYTICS_SERVICE_TOKEN` равным настроенному токену. Python 3.11+ нужен только для примера клиента, контейнеры содержат свой runtime. Из каталога комплекта:

```text
python examples/send_example.py --url http://127.0.0.1:18001 --case mixed-unconfirmed --repeat --lookup --conflict
python examples/send_example.py --url http://127.0.0.1:18001 --case mixed-completed
python examples/send_example.py --url http://127.0.0.1:18001 --case concrete
python examples/send_example.py --url http://127.0.0.1:18002 --case only-no-class --repeat --lookup
python examples/send_example.py --url http://127.0.0.1:18002 --case no-plan
python examples/send_example.py --url http://127.0.0.1:18001 --case invalid-summary
python examples/send_example.py --url http://127.0.0.1:18001 --case resource-cv --repeat --lookup --conflict
python examples/send_example.py --url http://127.0.0.1:18001 --case resource-partial
python examples/send_example.py --url http://127.0.0.1:18001 --case resource-manual
python examples/send_example.py --url http://127.0.0.1:18002 --case resource-cv
```

Concrete и mixed можно отправлять тому же клиенту на адрес VLM при наличии ключа; это реальные модельные вызовы. POST содержит ровно два файловых multipart-part: metadata и image. `Idempotency-Key` равен request_id. Повторяются точные байты: даже добавление пробела к JSON с прежним ID даёт 409. Для изменённых входов используйте новый request_id и новые версии изменённых снимков; прежние результаты не пересчитываются. Примеры рассчитаны на разные demo-site; старые результаты модельного вызова сохраняются и при смене настроек.

## Настройки и права

Рабочие пользователи `lct_deterministic_app` и `lct_vlm_app` имеют только членство в соответствующей NOLOGIN-роли. Runtime выполняет SET ROLE; каталог доступен для чтения, request/attempt разделены RLS. Администратор передаётся только БД и bootstrap. Приложения работают под UID 10001 с read-only файловой системой. Compose не передаёт им административный пароль.

Профили закреплены: `visual-profiles-708cb01523c740f39b8789aa`, каталог `catalog-cf435bba295909ab953facca`, матрица `construction-class-matrix-v2.0`. Профили остаются `ai_authored_unvalidated`. Смена набора требует проверенного импорта и согласованного каталога клиента.

`ANALYTICS_MAX_CONCURRENCY=1` — на процесс; `ANALYTICS_MODEL_TIMEOUT_SECONDS=240` — на gateway-вызов; `ANALYTICS_EXECUTION_TIMEOUT_SECONDS=1020` — возраст running-записи для обнаружения неопределённости, не жёсткий watchdog. Последний должен быть ≥4×таймаут модели+10. Адрес БД внутри Compose — postgres:5432. В ручном запуске допустим `ANALYTICS_DATABASE_URL` вместо POSTGRES_*; release Compose его не передаёт. Матрица и версия профилей явно закреплены в compose.yaml.

При таймауте/неопределённом модельном вызове повтор не запускает новый платный анализ; возвращается execution_uncertain. Административного reset API нет, нужна ручная сверка с gateway. Аудит и изображения хранятся без автоматической очистки: планируйте объём БД и резервные копии.

## Обновление и повтор bootstrap

Сохраните проектное имя Compose, данные volume и секреты. Сделайте резервную копию. Загрузите новые образы и замените комплект, сверив manifest/схему. Для повторной подготовки существующего volume:

```text
docker compose run --rm bootstrap
docker compose up -d --no-deps --force-recreate deterministic vlm_llm
```

Повтор миграции/импорта того же содержимого допускается; иное содержимое прежней версии отклоняется. Bootstrap может обновить пароли собственных LOGIN-пользователей, после чего приложения нужно пересоздать. Исходные construction.* и справочники не удаляются. Смена POSTGRES_PASSWORD в env не меняет пароль администратора существующего PostgreSQL-volume автоматически.

## Резервная копия и восстановление

Резервная копия содержит фотографии, запросы, результаты и попытки. Храните её как чувствительные данные. Команды ниже создают файл внутри контейнера и копируют его без двоичного перенаправления PowerShell:

```text
docker compose exec postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/analytics.dump'
docker compose cp postgres:/tmp/analytics.dump ./analytics.dump
```

Для восстановления запустите отдельный Compose project с новым volume и отдельными портами, выполните bootstrap (он создаёт необходимые роли), остановите приложения и восстановите дамп в его чистую БД:

```text
docker compose stop deterministic vlm_llm
docker compose cp ./analytics.dump postgres:/tmp/analytics.dump
docker compose exec postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/analytics.dump'
docker compose up -d deterministic vlm_llm
```

Эти команды относятся к выбранному проекту: для отдельного восстановления используйте его `-p` и env-файл последовательно во всех командах. Роли/пароли создаёт bootstrap нового окружения; pg_dump одной БД не сохраняет LOGIN-секреты. Проверьте ready, lookup известных запросов и изображения в БД. `docker compose down` сохраняет volume. Не выполняйте `down -v` на рабочей базе.

## Проверки поставки и оставшиеся зависимости

Фактические результаты контейнерных проверок записаны в `verification.json`; версии/образы/архитектура и SHA-256 — в `manifest.json` и `SHA256SUMS`. Проверка комплекта не подтверждает точность строительного анализа. Реальный gateway smoke без предоставленного ключа не выполняется; сквозное подключение внешнего адаптера требует фактических входов и совместного прогона с его командой.
