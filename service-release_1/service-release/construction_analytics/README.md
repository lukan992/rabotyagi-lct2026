# Аналитические сервисы 0.5.0

Два независимо запускаемых HTTP-приложения принимают один и тот же `frame-analysis-input-v1` и возвращают `frame-analysis-result-v1`. Внешний бэк владеет площадками, камерами, планом и исходной историей. Форматы и семантика: [BACKEND_INTEGRATION.md](../BACKEND_INTEGRATION.md); машинная схема: [frame-analysis-v1.schema.json](../contracts/frame-analysis-v1.schema.json).

`deterministic` ранжирует пункты переданного плана по матрице v2, проверяет повторяющийся сигнал следующей работы и сроки по sourced progress. `vlm_llm` отправляет изображение реальному OpenAI-compatible gateway, затем передаёт полный валидный VLM-ответ, CV-объекты, план и профили текстовой модели. Группы требуют отдельных визуальных опор; CV-ссылки проверяются по ID и области. VLM не рассчитывает задержку и не подтверждает ход работ.

HTTP-приложения читают справочники из PostgreSQL. `tmp/`, SQLite и `outputs/` не нужны установленному сервису; исходные артефакты нужны только администратору для первоначального импорта. Архивная Docker-поставка 0.2.0: service-release/README.md (путь в исходном репозитории: `service-release/README.md`), исходный Compose — deploy/compose.yaml. Она не содержит нового расширения; пакет 0.5.0 устанавливается из текущих исходников или wheel. Корневой Compose сохранён для прежней локальной БД.

Релиз допускает concrete/no_class, отклоняет summary/неизвестные ID. no_class не выбирается визуально; неподтверждённое завершение блокирует последовательный переход, а после completed старые наблюдения исключаются по actual_completed_at либо effective_at. Полный план сохраняется в аудите. Только no_class/нет плана/нет привязки участка — VLM без gateway-вызова/ключа; readiness без ключа остаётся 503. Поля версии модели содержат настроенное имя даже при пропущенном вызове по прежней схеме v1.

CLI `bootstrap --matrix PATH --profiles PATH` дополнительно создаёт ограниченные LOGIN-учётки lct_deterministic_app/lct_vlm_app (пароли ANALYTICS_DETERMINISTIC_DB_PASSWORD/ANALYTICS_VLM_DB_PASSWORD), проверяет их доступ и допускает повтор. Администратор нужен только подготовке. `export-catalog --output PATH [--service deterministic|vlm_llm]` экспортирует фактический References.catalog(), используемый API.

## Ресурсный v2 (0.5.0)

Оба сервиса дополнительно принимают frame-analysis-input-v2. [RESOURCE_INTEGRATION.md](../RESOURCE_INTEGRATION.md) описывает вход, сравнение, происхождение, ограничения и ошибки. API v1 и необязательное расширение активности сохранены. Deterministic сравнивает видимую технику с ресурсным планом; VLM сохраняет контекст, но возвращает для ресурсов not_evaluated. Фактические объёмы и производительность не выводятся из количества машин.

Новый комплект Docker: service-release-0.5.0/README.md (путь в исходном репозитории: `service-release-0.5.0/README.md`). Он создаётся и проверяется отдельно от архивной поставки 0.2.0. Живая синхронизация Spider и внешний адаптер требуют отдельного внедрения. Readiness требует миграцию 006.

## Установка и подготовка БД

Нужны Python 3.11+ и PostgreSQL. Из корня проекта:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
docker compose up -d postgres
.venv/Scripts/python.exe -m construction_analytics.cli --env-file .env migrate
.venv/Scripts/python.exe -m construction_analytics.cli --env-file .env import-references --matrix outputs/ranking_matrix_v2_2026_09_21/matrix_v2.json --profiles outputs/visual_work_profiles_2026_09_25/visual_work_profiles.sqlite3
```

`.env` должен содержать локальные настройки БД; шаблон — [`.env.example`](../.env.example). Существующий `.env` не перезаписывать. Для новой БД Compose применяет SQL 001–005; миграцию 006 применяет CLI migrate/bootstrap; для существующего тома команда `migrate` применяет добавочные миграции 005 и 006. Существующие таблицы `construction.*`, планы и наблюдения она не изменяет. Импорт запускается явно, проверяет SQLite integrity/FK, соответствие ID, матрицы и её SHA-256, сохраняет все девять исходных таблиц и сверяет прочитанные из PostgreSQL данные до commit. Все 377 строк `profile_json` сохраняются побайтно как TEXT.

Импорт текущих источников создаёт:

- `matrix_version=construction-class-matrix-v2.0`, 377 строк, 56 классов, 2238 связей;
- `catalog_version=catalog-cf435bba295909ab953facca`;
- `profiles_version=visual-profiles-708cb01523c740f39b8789aa`.

Версии неизменяемы: другое содержимое той же версии отклоняется. Профили по-прежнему имеют статус `ai_authored_unvalidated`; перенос не подтверждает их предметную точность. Импорт проверяет именно текущие артефакты v2; смена поколения справочников требует пересмотра проверок импорта.

## Конфигурация

Сервер читает окружение процесса. `.env` загружается только при явном `--env-file`, существующие переменные процесса имеют приоритет.

| Переменная | Назначение / значение по умолчанию |
| --- | --- |
| `ANALYTICS_DATABASE_URL` | Необязательный libpq URI; при наличии заменяет POSTGRES-настройки. Может отличаться у двух процессов. |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `127.0.0.1`, `5432`. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `construction_stages`, `construction_app`, пароль без значения по умолчанию. |
| `ANALYTICS_SERVICE_TOKEN` | Обязательный непустой секрет Bearer для API; без него ready=503. |
| `LITELLM_API_KEY` | Секрет реального gateway для VLM → LLM; без него ready=503. |
| `LITELLM_GATEWAY_URL` | Базовый OpenAI-compatible адрес, по умолчанию `https://litellm.g-309.ru/v1`, без встроенного пароля. |
| `ANALYTICS_MODEL` | Одна модель для VLM и LLM: `qwen3.8-27b`. |
| `ANALYTICS_MAX_CONCURRENCY` | Одновременно выполняемые анализы на процесс: `1`; превышение даёт 429. |
| `ANALYTICS_MODEL_TIMEOUT_SECONDS` | Таймаут одного gateway-вызова: `240`. |
| `ANALYTICS_EXECUTION_TIMEOUT_SECONDS` | Порог давности running-записи: `1020`; должен быть ≥ 4 × model timeout + 10. Это порог обнаружения неопределённого выполнения, не обещание жёсткого завершения всей задачи. |
| `ANALYTICS_MATRIX_VERSION` | `construction-class-matrix-v2.0`. |
| `ANALYTICS_PROFILES_VERSION` | Не задана: последний импортированный набор; для поставки рекомендуется явно фиксировать версию. |

Runtime выполняет `SET ROLE lct_analytics_deterministic` или `SET ROLE lct_analytics_vlm`. Эти NOLOGIN-роли могут читать каталог и писать журнал, но не менять справочники или снимки исходного плана. RLS разделяет записи запросов/попыток по сервису. На `construction.*` права не выдаются. В локальном Compose пользователь администратора позволяет выполнить миграцию и SET ROLE; в поставке нужны отдельные LOGIN-пользователи с членством только в своей роли, например `GRANT lct_analytics_vlm TO <пользователь_сервиса>`. Импорт и миграции выполняет отдельный администратор. Runtime SQL ограничен statement timeout 15 с и lock timeout 5 с.

## Запуск

Настроить секреты в окружении или локальном `.env`. Два терминала:

```powershell
.venv/Scripts/python.exe -m construction_analytics.cli --env-file .env serve --service deterministic --port 8001
```

```powershell
.venv/Scripts/python.exe -m construction_analytics.cli --env-file .env serve --service vlm_llm --port 8002
```

По умолчанию оба слушают `127.0.0.1`; `--host` задаётся явно при сетевом размещении. Допустим запуск через Uvicorn-фабрики `construction_analytics.api:deterministic_app` и `construction_analytics.api:vlm_app` с `--factory`. При нескольких worker/process общий PostgreSQL-журнал по-прежнему запрещает повтор одного запроса, а лимит concurrency относится к каждому процессу отдельно.

| Маршрут | Результат |
| --- | --- |
| `GET /health/live` | Доступность процесса; без авторизации. |
| `GET /health/ready` | Конфигурация, доступность БД и импортированных справочников; без авторизации. Не делает платный вызов и не доказывает доступность gateway. |
| `GET /v1/catalog` | `$defs.catalog`, включая 40 канонических классов техники и все 377 строк работ. |
| `GET /v1/capabilities` | `$defs.capabilities`, версии и фактические лимиты. |
| `POST /v1/analyze/frame` | Multipart с файлами `metadata` (`metadata.json`, application/json) и `image`; заголовок Idempotency-Key равен request_id. |
| `GET /v1/analyses/by-request/{request_id}?site_id=...` | Сохранённый результат/ошибка; 404 при отсутствии, 429 пока выполняется, 409 при неопределённости. |

Все `/v1/*` требуют `Authorization: Bearer ...`. Metadata передаётся отдельным файловым multipart-part с filename, image — также файловым part. Поля сверх этих двух отклоняются. JSON должен быть UTF-8 без повторяющихся ключей и нечисловых NaN/Infinity. JSON Schema дополняется проверками времени, геометрии, источников, ID, декодирования изображения и его SHA-256.

Пример клиента для уже подготовленного реального `metadata.json`:

```python
import json, os
from pathlib import Path
import httpx

metadata = Path("metadata.json").read_bytes()
case = json.loads(metadata)
image = Path("frame.png").read_bytes()
headers = {"Authorization": "Bearer " + os.environ["ANALYTICS_SERVICE_TOKEN"],
           "Idempotency-Key": case["request_id"]}
parts = {"metadata": ("metadata.json", metadata, "application/json"),
         "image": ("frame.png", image, case["frame"]["media_type"])}
response = httpx.post("http://127.0.0.1:8001/v1/analyze/frame", headers=headers,
                      files=parts, timeout=1050)
# Сохранить HTTP status и JSON; обработать code/execution_state по контракту.
```

## Движение и возможный простой техники

Оба сервиса поддерживают расширение `equipment-activity-v1` в существующем `/v1/analyze/frame`. Проверить поддержку можно через `GET /v1/capabilities?include_extensions=true`: ответ дополнительно содержит `extensions`. Без `equipment_activity` в metadata результат сохраняет прежнюю форму. Оценка не требует плана работ; VLM-ветка использует признаки операции, только если её обычный анализ выполнился.

Добавьте в корень валидного metadata следующий объект. Это пример конфигурации: даты и `source_ref` необходимо заменить фактическим рабочим периодом и его источником, классы — политикой конкретной площадки.

```json
{
"equipment_activity": {
  "schema_version": "equipment-activity-options-v1",
  "camera_stable": true,
  "idle_candidate_classes": ["Excavator", "DumpTruck"],
  "idle_threshold_seconds": 14400,
  "max_observation_gap_seconds": 1800,
  "working_period": {
    "start_at": "2026-09-27T08:00:00+03:00",
    "end_at": "2026-09-27T20:00:00+03:00",
    "source_ref": "backend:shift/schedule-id"
  }
}
}
```

`schema_version`, `camera_stable` и `idle_candidate_classes` обязательны при включении. Используйте канонические коды из `/v1/catalog`. `working_period` необязателен/nullable: без него подозрение на простой не выдаётся. Если класс не включён в список, неподвижность также не означает `possible_idle`. `camera_stable=false` отключает вывод о движении/неподвижности по истории; прямые признаки текущей VLM-операции остаются допустимы. Стабильность ракурса подтверждает источник; автоматической компенсации движения камеры нет.

| Необязательный параметр | По умолчанию | Значение |
| --- | --- | --- |
| `idle_threshold_seconds` | 14400 | Длительность наблюдаемой неподвижности для подозрения. |
| `class_idle_threshold_seconds` | `{}` | Пороги отдельных классов, например `{"DumpTruck": 7200}`. |
| `max_observation_gap_seconds` | 1800 | Максимальный интервал соседних кадров в цепочке. |
| `min_observations` | 3 | Минимальное число неподвижных наблюдений, включая текущий. |
| `min_detection_confidence` | 0.5 | Нижняя граница confidence CV; null не считается надёжным. |
| `center_change_ratio` | 0.1 | Допуск смещения центра относительно среднего размера bbox. |
| `size_change_ratio` | 0.2 | Допуск относительного изменения ширины/высоты. |
| `association_min_iou` | 0.1 | Минимальный IoU для геометрической пары. |
| `association_max_center_shift_ratio` | 1.5 | Альтернативный предел смещения для пары. |
| `association_max_size_change_ratio` | 0.6 | Предел изменения размера для любой геометрической пары. |

Передавайте реальные bbox `[x1,y1,x2,y2]` и confidence в текущем `cv.detections` и `history.observations[].cv.detections`, времена кадров и `history.complete`. История должна охватывать требуемую длительность в рабочем периоде. Для четырёх часов с шагом 30 минут нужны как минимум девять кадров. Один старый кадр четырёхчасовой давности не создаёт достаточную цепочку. Изображения истории не отправляются этому расширению.

В detection добавлено необязательное nullable-поле `track_id`: постоянный ID физической машины в пределах камеры, полученный внешним трекером. Не заменяйте его ID отдельной детекции; `detection_id` сохраняет прежний смысл. Tracking ID должен быть уникальным в каждом кадре, одинаковым для машины на всей цепочке и не переиспользоваться между машинами. Если ID задан, пара требует одинаковый ID и класс в обоих кадрах. Без ID используется только взаимно однозначное геометрическое сопоставление. Перестановка detections не влияет на него, близкие одинаковые машины дают неоднозначность. Замена похожей машины на другую в том же месте может остаться незамеченной.

В ответе `equipment_activity.items` содержит запись для каждой текущей видимой машины:

| `status` | Интерпретация |
| --- | --- |
| `movement_indicated` | Между ближайшими сопоставленными кадрами изменились центр или размер bbox сверх допуска. |
| `stationary_observed` | Есть наблюдаемая неподвижная цепочка, но условий подозрения на простой недостаточно. |
| `possible_idle` | Неподвижная цепочка достигла порога в рабочем периоде для разрешённого класса при полной истории. |
| `operation_indicated` | Текущая VLM-группа указывает на операцию и прямо ссылается на эту машину; подозрение на простой блокируется. |
| `unknown` | Надёжной оценки по данным нет. |

Запись также содержит `reason_codes`, `association_method`, `observation_count`, `stationary_observation_count`, `stationary_since`, `stationary_observation_span_seconds`, `last_change_at`, максимальные изменения bbox, применённый порог и ссылки `evidence_refs`/`visual_activity_refs`. Первый счётчик включает найденную границу изменения, второй — только неподвижную цепочку из двух и более снимков. `last_change_at` обозначает более новый снимок на границе изменения, а не точное время движения. Параметры и версия правил сохранены в ответе. Разрыв или потеря машины прекращает цепочку, не доказывая простой; исчезнувшие машины не оцениваются. Сравнение с текущим bbox выявляет накопленное смещение, даже когда соседние сдвиги малы.

Неизменность bbox не доказывает, что машина не работала на месте или не двигалась между снимками. VLM-взаимодействие относится к текущей группе работ, а не подтверждённой активности каждого её участника; исторические VLM-признаки не учитываются. Пороговые правила пока не откалиброваны на размеченных последовательностях реальных камер. Реализация: equipment_activity.py (путь в исходном репозитории: `construction_analytics/equipment_activity.py`), проверяемые сценарии: test_equipment_activity.py (путь в исходном репозитории: `tests/test_equipment_activity.py`).

## Как активность помогает оценить ход работ

Начиная с 0.4.0 включение `equipment_activity` также возвращает `work_progress` версии `work-progress-result-v1`. Параметры запроса прежние. Требуются реальный план и подтверждённая привязка камеры к его участку; иначе `work_progress.status` равен `no_plan`/`scope_unknown`, а пункты не назначаются. Клиентам расширения нужна обновлённая JSON Schema; старые запросы без расширения сохраняют прежнюю форму ответа.

`work_progress.summary` — короткий вывод по кадру и переданным источникам хода. `steps` следует порядку плана, сохраняя разные `step_key` для повторений одной работы. Каждый пункт содержит два отдельных состояния: `confirmed_state` с `confirmed_at` и ссылкой `progress_evidence_refs` на последнее sourced progress, а также наблюдаемый сигнал `activity_status`:

| `activity_status` | Что можно вывести |
| --- | --- |
| `operation_indicated` | Связанная VLM-группа имеет визуальные признаки операции; пункт может быть неоднозначным кандидатом. |
| `equipment_movement_indicated` | Есть движение совместимой техники; это может быть выполнение операции или переезд. |
| `possible_inactivity` | Все сопоставленные с пунктом видимые машины имеют possible_idle, прямых признаков операции нет. |
| `presence_only` | Есть техника/материал/результат, но активности недостаточно для вывода о выполнении. |
| `insufficient_evidence` | Надёжных соответствующих признаков нет; работа могла остаться вне кадра. |
| `not_visual` | Пункт no_class; его состояние определяется внешним источником. |

В deterministic машины с `possible_idle` исключаются из CV-ранжирования текущей работы и временных свидетельств `transition` **только в пределах их неподвижной цепочки**. Более ранняя граница изменения, наблюдения других камер и другие машины того же класса сохраняются. Поэтому стоящая техника не создаёт положительный сигнал возможного начала следующего этапа. `stationary_observed`/`unknown` остаются контекстом присутствия, не положительным подтверждением активности в `work_progress`.

VLM получает предварительные состояния машин в текстовом контексте до решения. Подозрение на простой не должно служить признаком активности; прямое взаимодействие на текущем фото может его опровергнуть. Финальная оценка по машине пересчитывается с полученными VLM-группами. У выбранной VLM-группы учитываются только машины, на которые она прямо ссылается как supports; неподвижная машина другой зоны не превращает выбранную работу в неактивную. Визуальные признаки операции допустимы и без CV-ссылки, но тогда не доказывают состояние отдельной машины. VLM-кандидат по присутствию/результату может сохраняться в `current_work` как контекст; активность читается из `work_progress` вместе с `visual_state`.

`candidate_match_status=compatibility_only`/`ambiguous` означает, что точный пункт не установлен; один сигнал нельзя считать несколькими параллельными работами. `supporting_detection_refs` содержит машины с положительными признаками активности для пункта; `ignored_idle_detection_refs` — исключённые из таких признаков; `uncertain_detection_refs` — машины без установленной активности. `activity_evidence_refs` и `visual_activity_refs` — JSON Pointer в ответ, `progress_evidence_refs` и корневой `excluded_detection_refs` — во входной metadata. Корневой список исключений включает историческую неподвижную цепочку. `next_stage_status` воспроизводит фактический статус `transition`; VLM оставляет его `not_evaluated`.

`confirmed_completed_steps` — число пунктов с последним внешним состоянием completed, `total_plan_steps` — число пунктов переданного плана. Это счётчики подтверждений, а не готовность объекта в процентах. Из движения/простоя не выставляются in_progress/completed, выполненный объём или отставание; `schedule` сохраняет прежние правила по sourced progress и срокам. Если завершённый пункт имеет новые признаки операции/движения, выдаётся причина `activity_after_sourced_completion` для проверки соответствия; внешнее подтверждение не перезаписывается.

Версия `equipment-work-progress-v1` сохраняется в `versions.work_progress_rules_version`; повтор запроса возвращает исходный вывод. Сохранённые результаты 0.3.0 не пересчитываются: для новой оценки нужен новый request_id и новый снимок истории, если изменилось его содержимое. Дополнительных env-переменных, миграций БД и модельных вызовов не добавлено. Реализация: work_progress.py (путь в исходном репозитории: `construction_analytics/work_progress.py`); сценарии: test_work_progress.py (путь в исходном репозитории: `tests/test_work_progress.py`). Точность этих сигналов на реальных последовательностях требует независимой проверки.

## Что хранится и как обрабатываются повторы

`analytics_catalog.*` хранит матрицу, метаданные и профили. `analytics_runtime.*` хранит неизменяемые снимки плана/истории, реестр внешних ID, точные байты metadata, оригинальный image как BYTEA с дедупликацией SHA-256, версии, результат и все попытки VLM/LLM с usage. Изображения и аудит сохраняются в PostgreSQL и не зависят от файловой системы приложения. Резервная копия и доступ к БД должны учитывать эти данные. Автоматическое удаление пока не реализовано; обещанный минимум идемпотентности — семь дней, фактически записи хранятся без срока удаления.

Fingerprint — SHA-256(metadata bytes + 0x00 + image bytes). Повтор тех же байтов и `(service, site_id, request_id)` возвращает исходный результат с тем же analysis_id, включая после перезапуска приложения или смены настроек. Другие байты при прежнем ключе дают 409. Объектный ID кадра, версия плана, step_key и snapshot_id тоже проверяются на неизменяемость, в том числе между двумя сервисами.

Перед каждым модельным вызовом durable journal фиксирует попытку. Допускается один форматный повтор VLM и один LLM; сетевой таймаут и HTTP 5xx gateway автоматически не повторяются. Неопределённое выполнение сохраняется как unknown; дальнейший POST/lookup даёт execution_uncertain, не новый платный вызов. Старый running тоже становится unknown после порога давности. Ручная сверка такого случая с gateway пока является эксплуатационной задачей, публичного reset endpoint нет.

## Проверки и ограничения

Проверки 0.5.0: 113/113 тестов, включая 36 на отдельной PostgreSQL. Docker-комплект service-release-0.5.0 (путь в исходном репозитории: `service-release-0.5.0/README.md`) прошёл запуск обоих API, 14 входных примеров, повтор/lookup/409, LOGIN/RLS, bootstrap, пересоздание и backup/restore. Отчёт (путь в исходном репозитории: `service-release-0.5.0/verification.json`). Исторические результаты 0.4.0 и 0.2.0 ниже относятся к прежним проверкам.


```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v
$env:LCT_RUN_POSTGRES_TESTS="1"
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv/Scripts/python.exe -m unittest discover -s llm_contour -p "test_*.py" -v
.venv/Scripts/python.exe -m pip wheel --no-deps --no-build-isolation . --wheel-dir tmp/service_verification
.venv/Scripts/python.exe tests/verify_distribution.py service-release-0.5.0/wheels/lct_construction_analytics-0.5.0-py3-none-any.whl
```

Интеграционные тесты создают отдельную базу `analytics_test_<uuid>` и удаляют только её. Требуются административные права на CREATE DATABASE; основная база не получает тестовых анализов. Модельные ответы в HTTP-стенде — явно тестовые протокольные ответы и сохранённый реальный VLM payload; они не служат измерением точности. Проверка wheel запускает оба HTTP-процесса вне репозитория и только читает основной каталог, без модельных запросов.

Совместимый `llm_contour/run.py` использует то же VLM → LLM ядро; его старый `llm-stage-input-v1` / `llm-stage-assessment-v2`, SQLite-источник и файловый аудит сохранены. HTTP-адаптер отдельно преобразует новый контракт; legacy CLI не подменяется новым форматом.

Для пакета 0.4.0 прошли 84/84 теста, включая 25 на отдельной PostgreSQL-БД, legacy CLI 11/11 и проверка обоих HTTP-процессов из wheel вне репозитория с объявлением версии/расширения. VLM readiness при проверке wheel ожидаемо 503: ключ намеренно исключён, платных вызовов нет. Проверки архивного релиза 0.2.0: 45/45 тестов (включая 19 на отдельной PostgreSQL-БД), legacy CLI 11/11, профили 8/8, wheel вне репозитория. `tests/verify_service_release.py` загружает архив и проверяет реальные контейнеры, LOGIN/RLS, повтор bootstrap, примеры/повторы/lookup, сохранность, backup restore и локальный протокольный timeout. Результат — service-release/verification.json. Для повторного запуска сначала соберите оба target Dockerfile, выполните scripts/assemble_service_release.py и docker save в service-release/images/analytics-images.tar; тест сохраняет уникальный тестовый volume, завершает только свой проект и удаляет свой env с секретами.

Нужны отдельная проверка реального gateway с предоставленным ключом, реальные планы/CV/подтверждения хода и независимые метки работ. Прохождение контейнерных проверок не означает измеренную точность. Встраиваемый модуль и совместный прогон внешнего бэка остаются следующими этапами.
