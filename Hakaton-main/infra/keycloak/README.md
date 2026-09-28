# Чистый локальный Keycloak

`compose.yml` и `realm-stroykontrol.json` рядом — старый **демо-стенд** для разработки и тестов. Он остаётся отдельным и не подходит для сайта. Этот каталог запускается только через `compose.clean.yml`.

Чистый стенд использует отдельный Compose project и именованный том `stroykontrol-keycloak-clean-db`; он не подключает, не удаляет и не меняет тома сайта. Контейнеры автоматически перезапускаются вместе с Docker. Keycloak присоединяется к уже существующей сети сайта `hakaton-main_default`: backend обращается к нему как `http://keycloak:8080`, а браузер — только через `http://localhost:8180`.

## Подготовка

Сначала должен быть создан обычный Docker-стенд сайта, чтобы существовала внешняя сеть `hakaton-main_default`. Не запускайте для этого файла `compose.yml` из данного каталога.

Создайте локальный файл с четырьмя разными случайными секретами:

```bash
umask 077
{
  printf 'KC_DB_PASSWORD=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'KC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'KC_SERVICE_CLIENT_SECRET=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'KC_SITE_ADMIN_PASSWORD=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
} > infra/keycloak/clean.env
chmod 600 infra/keycloak/clean.env
```

`clean.env` и результат рендера игнорируются Git. `clean.env.example` — только перечень обязательных имён, не источник значений.

Отрендерьте импорт realm и запустите чистый Keycloak:

```bash
./infra/keycloak/render-clean-realm.py infra/keycloak/clean.env
docker compose --env-file infra/keycloak/clean.env -f infra/keycloak/compose.clean.yml up -d
```

Рендер детерминирован для одного `clean.env` и записывает секреты только в игнорируемый `infra/keycloak/generated/realm.json`. Запускайте его заново после смены `KC_SERVICE_CLIENT_SECRET` или `KC_SITE_ADMIN_PASSWORD`, затем импортируйте realm только в новую Keycloak DB. Keycloak читает импорт лишь при первом создании своей базы: повторный `up` не перезаписывает пользователей или роли.

После первого запуска доступны:

- Issuer для браузера и JWT: `http://localhost:8180/realms/stroykontrol`.
- Консоль Keycloak: `http://localhost:8180/admin/` с bootstrap-учётной записью `keycloak-admin` и `KC_BOOTSTRAP_ADMIN_PASSWORD`.
- Единственная учётная запись человека в realm: `admin`, пароль `KC_SITE_ADMIN_PASSWORD`, realm-роль `admin`.
- Публичный PKCE-клиент `stroykontrol-web`, разрешающий только `http://localhost:8080/*`.
- Конфиденциальный служебный клиент `stroykontrol-backend`; его секрет — `KC_SERVICE_CLIENT_SECRET`. Его service account имеет `manage-users`, `view-users`, `query-users` и `view-realm` в `realm-management`.

Машинная service-account identity создаётся для служебного клиента; демо-пользователи `prorab`, `rukovoditel` и `inspektor` в этот realm не импортируются.

## Подключение сайта

В корневом игнорируемом `Hakaton-main/.env` укажите те же значения:

```dotenv
SK_KEYCLOAK_ISSUER=http://localhost:8180/realms/stroykontrol
SK_KEYCLOAK_BACKCHANNEL_URL=http://keycloak:8080
SK_KEYCLOAK_CLIENT_ID=stroykontrol-web
SK_KEYCLOAK_ADMIN_CLIENT_ID=stroykontrol-backend
SK_KEYCLOAK_ADMIN_CLIENT_SECRET=<значение KC_SERVICE_CLIENT_SECRET из infra/keycloak/clean.env>
```

Перезапустите только backend сайта обычной командой проекта. `SK_KEYCLOAK_ISSUER` всегда остаётся публичным URL, который использует браузер и который указан в токенах. `SK_KEYCLOAK_BACKCHANNEL_URL` предназначен только для backend в Docker: discovery, JWKS, token и Admin API идут к `keycloak` по внутренней сети.

Не используйте `docker compose ... down -v` для существующего сайта. Остановка чистого Keycloak без `-v` сохраняет его отдельную DB; удаление его тома намеренно создаст новый Keycloak и повторно импортирует только этот чистый realm.
