# Ресурсный контракт v2 — аналитика 0.5.0

Реализован в обоих сервисах. Машинный контракт: [frame-analysis-v2.schema.json](contracts/frame-analysis-v2.schema.json).
Действующий [контракт v1](BACKEND_INTEGRATION.md) сохранён. Внешний адаптер и живая синхронизация со Spider требуют отдельного внедрения и совместного прогона.

## 1. Маршруты и совместимость

| Маршрут | Назначение |
| --- | --- |
| `POST /v2/analyze/frame` | Принимает multipart `metadata` (application/json) и `image` с реальными байтами одного кадра. |
| `GET /v2/capabilities` | Версии, режимы, лимиты и доступное расширение equipment-activity-v1. |
| `GET /v2/analyses/by-request/{request_id}?site_id=...` | Возвращает сохранённый результат v2 без пересчёта. |
| `GET /v1/catalog` | Общий каталог канонических классов и работ. Каталог v2 не создаётся. |

Для всех маршрутов нужен `Authorization: Bearer <service-token>`. Для POST также нужен `Idempotency-Key`, равный `request_id`.
Основные поля и проверки изображения/плана/истории такие же, как в v1. Передача v2 на маршрут v1 и ресурсных полей в v1 отклоняется. Имена сообщений: `frame-analysis-input-v2`, `frame-analysis-result-v2`, `frame-analysis-error-v2`, `frame-analysis-capabilities-v2`.

| Свойство | v1 | v2 |
| --- | --- | --- |
| Основной план, реальный CV, история | Сохраняются | Та же семантика |
| equipment_activity / work_progress | Необязательное расширение | То же расширение |
| Ресурсные поля | Запрещены | Обязательные ключи, предусмотренные значения null |
| Ресурсное сравнение deterministic | Нет | Отдельный resource_assessment |
| Ресурсное сравнение VLM | Нет | not_evaluated, версия правил null |
| Старые сохранённые результаты | Воспроизводятся | Не переписываются |

## 2. Дополнительные поля запроса

Помимо основных полей v1 передаются:

| Поле | Содержимое |
| --- | --- |
| `analysis_mode` | demonstration либо operational. |
| `source_context` | source_system, source_snapshot_id, source_ref, data_type, timestamp_quality, spider_live_sync. |
| `resource_plan` | null либо plan_id, revision_id, source_ref, plan_stream_code, stages. |
| `resource_target` | null либо stage_code, selection_basis, source_ref. |
| `equipment_observation` | basis, source_ref, observed_at, coverage, requires_validation, items. |

`source_context.data_type`: synthetic_demo / production / unknown. `timestamp_quality`: verified / source_declared / demonstration / unknown. `spider_live_sync`: boolean либо null. Эти утверждения предоставляет источник; сервис сохраняет их, но не проверяет внешний Spider по сети. Новый ID снимка сам по себе не является подтверждением качества данных.

В operational synthetic_demo, демонстрационные времена и ручные счётчики с requires_validation=true отклоняются с `data_mode_not_allowed`. Unknown-происхождение, непроверенные времена и CV с requires_validation=true допускаются как неполный контекст, но числовое сравнение не становится compared. Для operational-сравнения нужны production, verified и requires_validation=false. Внешняя синхронизация Spider не обязательна: подтверждённые данные могут поступать из другого источника.

В demonstration ресурсный результат всегда помечен demonstration. Арифметика допустима при достаточном охвате и привязке, но не создаёт оперативных тревог. Для demonstration и непроверенного происхождения/времени deterministic возвращает schedule.status=insufficient_evidence и transition.status=insufficient_evidence; результаты распознавания и sourced progress сохраняют явную маркировку режима/происхождения.

## 3. Ресурсные этапы и соответствия

Каждый stage содержит stage_code, name, sequence_no, planned_start_at, planned_end_at, source_ref, mapping_status, mapped_step_keys, planned_work_shifts, planned_volume, planned_productivity, equipment. Даты имеют timezone; конец строго позже начала, начала следуют последовательности. Коды и sequence_no уникальны.

- mapping_status=mapped требует непустые уникальные mapped_step_keys, существующие в основном plan. Их интервалы должны лежать внутри интервала исходного ресурсного этапа.
- unmapped/ambiguous требуют пустые mapped_step_keys. Если полный подробный план невозможно достоверно подготовить, адаптер передаёт plan=null. Нельзя удалить неизвестную работу из цепочки.
- P01–P10 сохраняются как внешние stage_code и не становятся stage_id каталога.
- Непустой resource_plan.plan_stream_code обязан совпадать с scope.plan_stream_code. Null сохраняет непривязанный ресурсный снимок, но исключает сравнение с кадром.
- Ресурсная цель не устанавливает текущую работу. planned_at_frame_time проверяется по полуоткрытому интервалу `[planned_start_at, planned_end_at)`. confirmed_current требует предоставленную ссылку на подтверждённый источник; explicit сохраняет ссылку на явный выбор.
- При отсутствии resource_plan допускается сохранить цель, но сравнение сообщает no_resource_plan. Ссылка на отсутствующий этап в существующем плане — ошибка.

Плановая строка equipment: item_id, source_name, class_code, mapping_status (mapped/unmapped), planned_quantity, source_ref. Mapped-класс должен существовать в каталоге техники; unmapped требует class_code=null. Item_id уникальны внутри этапа. Повтор класса в разных строках не суммируется: все строки этого класса получают unknown / duplicate_planned_class.

Количество — целое от 0 до 1 000 000 либо null. Ноль означает явно нулевой план. Смены — положительное число либо null. Объём и производительность — `{value, unit}` либо null; число конечное, от 0 до 10^15, unit до 100 символов. Единицы сохраняются без конверсии. Нормы широкого этапа не распределяются между mapped_step_keys.

## 4. Счёт техники

observed_at должен совпадать по времени с текущим кадром. Coverage: full_scope / partial_scope / unknown. Full_scope является заявлением источника о покрытии участка, не гарантией точности CV.

- **cv_detections:** cv.status=ok, items пуст, source_ref совпадает с cv.source_ref. Сервис считает отдельные текущие detections. Успешный пустой CV даёт видимый счёт 0. История не прибавляется к текущему кадру. Повторы detection_id, track_id и полностью совпадающие class/bbox отклоняются; другие физические дубли обязан устранить адаптер/детектор. Непроверенное слияние ракурсов запрещено.
- **manual_visual_estimate:** допустим при недоступном/ошибочном CV. Items: item_id, source_class_code, class_code, mapping_status, count, confidence_label, source_ref. Count — целое 0..1 000 000; confidence_label — исходная строка до 100 символов либо null. Неизвестный класс сохраняется как unmapped/null. Нет строки нужного класса — количество неизвестно, а не ноль. Повтор одного класса в нескольких ручных строках даёт unknown / duplicate_observed_class.
- **unavailable:** items пуст, source_ref допускает null, число неизвестно.

При cv.status=ok основой должен быть CV, даже если найдено ноль объектов. Ручной массив не добавляется к CV и не передаётся в ranking, transition или модельный контекст. Адаптер отдельно хранит исходную ручную разметку для аудита. Возможный простой и исключение idle-машины из ranking не уменьшают её видимый счёт.

## 5. Ответ

Основные разделы v1 сохранены. Добавлены analysis_mode, source_context, resource_assessment и versions.resource_rules_version.
Для deterministic версия — resource-rules-v1; для VLM — null.

resource_assessment содержит status, reason_codes, stage_code, selection_basis, basis, coverage, equipment_items, planned_volume, planned_work_shifts, planned_productivity, actual_volume, actual_productivity, evidence_refs, limitations.

| Статус | Семантика |
| --- | --- |
| compared | В operational хотя бы одна строка достоверно сравнима. Другие строки могут быть unknown; читать статусы строк и причины. |
| demonstration | Явно демонстрационный результат; читать ограничения каждой строки. |
| insufficient_evidence | Нет допустимого сравнения из-за отсутствующих или непригодных данных. |
| not_evaluated | VLM сохраняет контекст ресурсов, но числовое сравнение выполняет deterministic. Строки оборудования пусты. |

Строка equipment_items: item_id, class_code, planned_quantity, visible_count, visible_count_delta, status, reason_codes, evidence_refs.
Delta = visible_count − planned_quantity. Статусы: visible_below_plan, visible_equal_plan, visible_above_plan, unknown.
При partial/unknown coverage, непроверенном operational-источнике или отсутствии привязки видимый счёт сохраняется, delta=null и status=unknown.
Ссылки — JSON Pointer в неизменённый metadata: `/resource_plan/stages/0/equipment/0`, `/cv/detections/0`, `/equipment_observation/items/0` и т.д.
actual_volume и actual_productivity всегда null, причина no_independent_measurements. Число машин не устанавливает дефицит парка, объём, производительность, отставание или прогноз срока.

## 6. Лимиты

Общие лимиты v1 сохранены: metadata 1 000 000 байт, фото 25 000 000 байт, сторона 8192, 40 000 000 пикселей, 200 detections, 500 шагов, 500 исторических наблюдений, 1000 progress events, модельный контекст 120 000 символов.
Дополнительные лимиты одинаковы в схеме, semantic validation и capabilities:

| Поле capabilities.limits | Значение |
| --- | --- |
| resource_stages_max | 500 |
| resource_equipment_per_stage_max | 200 |
| resource_equipment_total_max | 10000 |
| manual_observation_items_max | 200 |
| resource_quantity_max | 1000000 |
| resource_source_ref_max_chars | 1000 |

Opaque ID до 100 символов; источники и названия до 1000. Превышение общего числа ресурсных строк и байтов — 413 payload_too_large. Превышение ограничений структуры/отдельного массива — 422 invalid_context. Данные не обрезаются.

## 7. Хранение, ошибки и повторы

Миграция [006_resource_plan.sql](db/006_resource_plan.sql). CLI migrate/bootstrap выполняет 005 и 006 по порядку и допускает повторный запуск. Readiness проверяет наличие таблиц v2; при неустановленной миграции сервис не объявляет готовность.
Добавляются input_version в журнал запросов и неизменяемые resource_plan_snapshot / source_snapshot. Исходный metadata сохраняется с точными UTF-8-байтами, изображение с точными байтами, также сохраняются версии правил, попытки модели и полный ответ.

Ключ ресурса: `(site_id, resource_plan.plan_stream_code или пустая строка, plan_id, revision_id)`.
Ключ источника: `(site_id, scope.plan_stream_code или пустая строка, source_system, source_snapshot_id)`; payload включает source_context, plan, resource_plan. Цель и наблюдения меняются между кадрами и сохраняются в журнале запроса. Изменять план/происхождение внутри того же снимка нельзя; нужен новый подтверждённый снимок/ревизия.
Роли приложений могут читать/добавлять снимки, но не изменять/удалять их. RLS сохраняет разделение журналов и попыток между сервисами. Как и в v1, site_id задаётся доверенным интеграционным бэком, а общий Bearer-токен не является пользовательской многопользовательской авторизацией.

request_id уникален внутри service/site для обеих версий. Точные повторы возвращают сохранённый ответ, изменение байтов — 409 idempotency_conflict. Lookup другого API-поколения возвращает 404 analysis_not_found. Busy — 429, retryable=true, Retry-After: 2. Неопределённый модельный вызов — 409 execution_uncertain, retryable=false, execution_state=unknown; автоматического повтора модели нет.

| Новая ошибка v2 | HTTP | retryable | execution_state до начала |
| --- | --- | --- | --- |
| invalid_resource_context | 422 | false | not_started |
| data_mode_not_allowed | 422 | false | not_started |
| resource_plan_identity_conflict | 409 | false | not_started |
| source_snapshot_identity_conflict | 409 | false | not_started |

Ошибки каталога, изображения, авторизации, модели и БД сохраняют коды/статусы v1. Ошибка выполнения отделена от успешного аналитического отказа: допустимый недостаток данных возвращает 200 с причиной.

## 8. Запуск и передача

Docker-поставка 0.5.0 содержит оба образа, PostgreSQL, Compose, пустой шаблон секретов, каталог, capabilities, схемы, демонстрационные входы и записанные HTTP-ответы. Старый архив 0.2.0 сохраняется. Команды миграции/запуска и резервного копирования описаны в README комплекта.
Все resource-* примеры явно synthetic_demo/demonstration и используют синтетическую иллюстрацию. Production-пример не выдаётся за проверенный производственный источник. Защищённые operational-ветви проверяются искусственными протокольными тестами, а не реальными строительными фактами.
Для подключения внешняя команда должна предоставить собственные реальные фото, временную привязку, каталог соответствий, CV/проверенные счётчики и sourced progress. Сквозная интеграция со Spider считается проверенной только после совместного прогона с настоящим адаптером.
