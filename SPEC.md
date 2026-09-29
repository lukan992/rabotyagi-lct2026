# SPEC.md

## 1. Обзор реализации

Репозиторий объединяет сайт «СтройКонтроль», внешний YOLO tracker и поставляемые сервисы аналитики 0.5.0. Основной backend — Python 3.12+, FastAPI, SQLAlchemy async, Alembic и ONNX Runtime; frontend — React, TypeScript и Vite. В Docker сайт использует PostgreSQL и MediaMTX; локальный backend по умолчанию использует SQLite. `analytics_service_05` заменяет только prompt/pipeline VLM-образа поставки. Spider читается backend через HTTP и не меняет локальный план или детекции.

## 2. Структура и взаимодействие

| Путь | Назначение |
|---|---|
| `Hakaton-main/backend/app/main.py`, `app/api/`, `app/services/` | API сайта, фоновые задачи, обработка видео, плана, Spider и аналитики |
| `Hakaton-main/backend/migrations/` | Схема базы и миграции Alembic |
| `Hakaton-main/frontend/src/` | Интерфейс, API-клиент и типы |
| `yolo_service/` | Отдельный сервис детекции/трекинга и outbox событий |
| `service-release_1/service-release/` | Поставленные образы deterministic и VLM/LLM, схемы v1/v2 и отдельная БД |
| `analytics_service_05/` | Overlay для передачи плана Spider в LLM-контекст |
| `start-all.sh` | Проверка комплекта и запуск analytics, сайта и одного профиля YOLO |

Поток: камера → MediaMTX → backend/YOLO tracker → снимки и детекции в БД сайта → сверка с локальным планом → предупреждения и интерфейс. Отдельно backend отправляет кадр и подготовленный локальный контекст в deterministic и VLM/LLM. При выбранном Spider backend добавляет только нормализованный ресурсный план и provenance по контракту v2.

## 3. Запуск и проверка

- Полный Docker-стенд: подготовить игнорируемые `.env` согласно `Hakaton-main/backend/.env.example`, `service-release_1/service-release/.env.example` и `yolo_service/README.md`; из корня на Linux выполнить `./start-all.sh --check`, затем `./start-all.sh --cpu` либо `--gpu`. Скрипт проверяет архив поставки, Compose и необходимые файлы до запуска.
- Backend локально: `cd Hakaton-main/backend && uv sync && uv run uvicorn app.main:app --port 8100 --reload`. Первый администратор: `uv run python -m app.manage create-admin --login admin --name "Имя"`. Alembic запускается при старте через `prepare_database`.
- Frontend локально: `cd Hakaton-main/frontend && npm ci && npm run dev` (по умолчанию порт 5180, API проксируется на 8100).
- Проверки: `cd Hakaton-main/backend && uv run pytest && uv run ruff check .`; `cd Hakaton-main/frontend && npm test && npm run build`. Тесты backend используют временный SQLite; PostgreSQL задаётся через `SK_TEST_DATABASE_URL` и extra `postgres`.

## 4. API и контракты

Полная машинная схема сайта доступна по `/openapi.json`; типы frontend генерируются в `frontend/src/api/openapi.d.ts` и сверяются при сборке. Все перечисленные ниже маршруты находятся под `/api`.

| Метод и путь | Вход → результат | Ошибки и доступ |
|---|---|---|
| `GET /sites/{site_id}/spider/connection` | Нет body → origin, признак токена и тип настройки без самого токена | Авторизованный участник объекта; недоступный объект скрывается проверкой `get_site` |
| `PUT /sites/{site_id}/spider/connection` | `{url, token?}` → состояние подключения; отсутствие token сохраняет прежний только при том же origin | Руководитель/администратор; 422 для неверного origin |
| `POST /sites/{site_id}/spider/import` | Без body → ID, статус и ID снимка успешного импорта | Руководитель/администратор; 503 без источника, 502 при сбое или неконсистентных данных |
| `GET /sites/{site_id}/spider` | Нет body → последний снимок, попытка, время успеха, stale и ограничения | Участник объекта; внешних запросов не делает |
| `POST /sites/{site_id}/spider/observations/{observation_id}/prepare` | `{snapshotId}` → метаданные сохранённого фото и проверенной цели | Руководитель/администратор; 404 для чужого снимка/наблюдения, 502 при ошибке источника |
| `GET /sites/{site_id}/spider/images/{asset_id}` | Нет body → байты изображения | Участник объекта; 404 без действительного импорта и привязки фото к объекту/подключению |

Другие группы API находятся в `app/api/`: вход и пользователи, объекты и план, камеры/видео, снимки и детекции, предупреждения и нарушения, фотоанализ, работы по камерам, отчёты и аудит. Их request/response и статусы определяются Pydantic-схемами в `app/schemas.py` и `/openapi.json`; поставленные сервисы аналитики описаны отдельно в `service-release_1/service-release/BACKEND_INTEGRATION.md` и `RESOURCE_INTEGRATION.md`.

## 5. Pipeline Spider и данные

1. `app/services/spider.py` выбирает явное подключение объекта или серверный fallback; origin ограничен `http(s)://host[:port]`. Токен расшифровывается только на сервере.
2. Импорт последовательно читает `/api/v1/stages`, `/equipment`, `/observations`, `/photo-equipment`, `/comparisons`; ограничивает ответ и число повторов. Проверяются версия, тип/источник данных, совпадение этапов и техники, ссылки наблюдений, согласованность разметки/сравнений и допустимые для v2 ресурсы. API источника не предоставляет атомарную версию пяти ответов.
3. SHA-256 исходных документов и origin образуют ID неизменяемого `SpiderSnapshot`. `SpiderImport` хранит попытку для объекта. Отпечаток активного подключения — HMAC от origin/токена с ключом, производным от `SK_SECRET_KEY`; он не раскрывает токен. Только импорты с текущим отпечатком используются в статусе, аналитике и доступе к фото.
4. Миграция `0013` добавляет отпечаток к импортам и объект/отпечаток к фото. Старые строки остаются в БД с `NULL` и не считаются данными активного подключения. Нужен новый успешный импорт; при включённом refresh он может выполниться автоматически. Исходные документы и исторические ответы не удаляются.
5. `prepare` ещё раз получает `/observations` и сравнивает SHA-256 с импортом до скачивания фото. Фото хранится по собственному SHA-256 в приватном `SK_DATA_DIR/spider/images`, запись принадлежит объекту и подключению. `/plan-at?timestamp=...` сверяется с ресурсным этапом и сохранённым сравнением. Повтор успешной подготовки не делает новый запрос.
6. `app/services/analytics/request.py` строит v2 только при явном выборе Spider и действительном импорте; этапы остаются `unmapped`, `resource_target=null`, `equipment_observation` относится только к локальному CV. Если импорт отсутствует, используется v1 с примечанием. `app/services/analytics/result.py` проверяет provenance ответа перед отображением.

## 6. БД и артефакты

Основные сущности сайта: пользователи/назначения объектов, объекты, зоны, камеры, календарные этапы, снимки, обнаруженная техника, предупреждения/действия, запросы и результаты аналитики. Для Spider используются `spider_connections` (origin и зашифрованный токен объекта), `spider_snapshots` (исходные тела и нормализованные ресурсы), `spider_imports` (статус и отпечаток подключения), `spider_observation_assets` (объект, подключение, хеш и приватный путь изображения, попытки выбора этапа). Схема — модели в `Hakaton-main/backend/app/models.py` и миграции 0011–0013.

Локальные кадры и фото хранятся под `SK_DATA_DIR`; фото Spider не публикуются через `/media`, а читаются только после проверки прав. БД аналитической поставки отделена от БД сайта; её compose использует именованный том PostgreSQL.

## 7. Переменные окружения

Значения ниже — defaults класса `Settings` (`Hakaton-main/backend/app/config.py`). Все обычные настройки backend принимают префикс `SK_`; три URL/токен аналитики и три настройки Spider также принимают алиасы без `SK_`. Секреты задаются в игнорируемых `.env`, реальные значения в документации не приводятся.

| Переменная | Использование; обязательность; пример; default |
|---|---|
| `SK_DATABASE_URL`, `SK_DATA_DIR` | БД и файлы; SQLite локально по умолчанию, Docker задаёт PostgreSQL; пример `postgresql+asyncpg://user:***@db:5432/db`; defaults `backend/data/stroykontrol.db`, `backend/data` |
| `SK_SECRET_KEY` | Подпись/шифрование и HMAC Spider; обязателен свой ключ вне демо; пример: случайная строка; dev default небезопасен для рабочего запуска |
| `SK_TIMEZONE`, `SK_CORS_ORIGINS`, `SK_TOKEN_TTL_HOURS` | Время, CORS, срок токена; необязательны; defaults `Europe/Moscow`, localhost:5180, `12` |
| `SK_DEMO_MODE`, `SK_SEED_ON_START`, `SK_DEMO_PASSWORD` | Стендовые режимы и пароль при наполнении; необязательны; defaults `false`, `false`, `demo` |
| `SK_KEYCLOAK_ISSUER`, `SK_KEYCLOAK_BACKCHANNEL_URL`, `SK_KEYCLOAK_CLIENT_ID`, `SK_KEYCLOAK_AUTO_PROVISION`, `SK_KEYCLOAK_ADMIN_CLIENT_ID`, `SK_KEYCLOAK_ADMIN_CLIENT_SECRET` | Необязательный OIDC и административный клиент; defaults `null`, `null`, `stroykontrol-web`, `true`, `stroykontrol-backend`, `null`; пример issuer `https://sso.example/realms/site` |
| `SK_VIDEO_ENABLED`, `SK_VIDEO_API_URL`, `SK_VIDEO_RTSP_URL`, `SK_VIDEO_WEBRTC_URL` | Видеошлюз; нужны при видео; defaults `true`, `http://127.0.0.1:9997`, `rtsp://127.0.0.1:8554`, `http://localhost:8889` |
| `SK_FRAME_INTERVAL_S`, `SK_CHECK_INTERVAL_S`, `SK_CAMERA_TIMEOUT_S`, `SK_ALLOW_LOOPBACK_CAMERAS`, `SK_MAX_FRAME_BYTES` | Интервалы/лимиты камер; необязательны; defaults `2`, `60`, `6`, `true`, `12582912` |
| `SK_KEEP_FRAMES_PER_CAMERA`, `SK_KEEP_USAGE_DAYS` | Хранение кадров и истории; необязательны; defaults `200`, `30` |
| `SK_ANALYSIS_PROVIDER`, `SK_ANALYSIS_API_URL`, `SK_ANALYSIS_API_KEY`, `SK_ANALYSIS_TIMEOUT_S`, `SK_INGEST_API_KEY` | Локальная модель/HTTP/push детекции; URL и ключ нужны только для соответствующего режима; defaults `auto`, `null`, `null`, `20`, `null` |
| `SK_DETECTOR_MODEL`, `SK_DETECTOR_CONFIDENCE`, `SK_DETECTOR_THREADS`, `SK_DETECTOR_ACCELERATE`, `SK_DETECTOR_CLASSES` | ONNX-модель и mapping; модель нужна для `local`; defaults `models/detector.onnx`, `0.35`, `0`, `true`, `{}` |
| `SK_REALTIME_FPS`, `SK_REALTIME_MAX_CAMERAS` | Живые рамки; необязательны; defaults `8`, `8` |
| `SK_TRACKER_URL`, `SK_TRACKER_API_KEY`, `SK_TRACKER_RTSP_URL`, `SK_TRACKER_VIDEO_DELAY_MS`, `SK_EQUIPMENT_OBSERVATION_TIMEOUT_SECONDS` | Внешний tracker; URL/ключ нужны при его включении; defaults `null`, `null`, `null`, `150`, `90` |
| `DETERMINISTIC_SERVICE_URL`, `VLM_LLM_SERVICE_URL`, `ANALYTICS_SERVICE_TOKEN`, `ANALYTICS_HTTP_TIMEOUT_SECONDS` | Аналитические сервисы; URL и общий токен нужны для внешних вызовов; defaults `null`, `null`, `null`, `1020`; пример URL `http://deterministic:8000` |
| `SK_ANALYTICS_INTERVAL_MIN`, `SK_ANALYTICS_VLM_INTERVAL_MIN`, `SK_ANALYTICS_HISTORY_DAYS`, `SK_ANALYTICS_OBSERVATION_MIN`, `SK_ANALYTICS_CLASSES` | Периоды, история и mapping аналитики; необязательны; defaults `20`, `60`, `7`, `20`, `{}` |
| `CAMERA_STAGE_MONITOR_URL`, `CAMERA_STAGE_MONITOR_TOKEN`, `CAMERA_STAGE_MONITOR_TIMEOUT_SECONDS`, `SK_CAMERA_STAGE_MONITOR_REFRESH_SECONDS`, `SK_CAMERA_STAGE_MONITOR_MAX_RETRIES` | Резервный Spider origin/токен, таймаут, refresh и повторы; URL/токен можно заменить настройкой объекта; defaults `null`, `null`, `15`, `0`, `1`; пример origin `https://spider.example` |

Frontend: `VITE_BACKEND_URL` меняет dev proxy, `VITE_API_URL` — адрес API при отдельном домене; оба необязательны. YOLO tracker требует общий `SK_TRACKER_API_KEY` и путь к модели `YOLO_MODEL_HOST_PATH` при запуске через Compose. Аналитическая поставка требует собственные пароли PostgreSQL и `ANALYTICS_SERVICE_TOKEN`; модельный VLM требует `LITELLM_API_KEY` и gateway-настройки. Остальные параметры этих отдельных компонентов приведены в их `.env.example` и README.

## 8. Ошибки, ограничения и вопросы

- Ошибка импорта Spider фиксируется отдельной попыткой и не подменяет последний успех того же подключения. После смены токена или origin старые импорты скрыты.
- API Spider не даёт ID объекта, атомарную ревизию пяти документов и хеш изображения. Поэтому принадлежность плана и неизменность фото по URL остаются неподтверждёнными; интерфейс сообщает об этом.
- Фото, полученное до миграции 0013, сохраняется в БД, но не относится к текущему проверенному подключению и требует новой подготовки.
- При невалидной первой группе VLM/LLM `analytics_service_05/construction_analytics/pipeline.py` делает один повтор с ошибками валидатора, предыдущим JSON-ответом (если он помещается в лимит контекста) и точными ID наблюдений, которым не хватает `evidence` с ролью `supports`. Если предыдущий ответ не помещается, сохраняются ошибки и ID. Валидатор остаётся строгим; второй невалидный ответ даёт `model_invalid_response` (502).
- Архивный кадр OBS03 с локальным демонстрационным планом и ресурсным планом Spider прошёл CV → VLM → LLM после исправления: первая группа не прошла `area_support`, повтор был принят. Это одна проверка, а не оценка точности CV или стабильности VLM на произвольных производственных кадрах.
