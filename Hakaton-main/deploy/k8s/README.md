# Развёртывание СтройКонтроля в Rancher

Адрес: `https://construction.g-309.ru`. Все namespaced-ресурсы находятся **только** в `construction`; cluster-scoped PV начинаются с `stroykontrol-` либо `construction-` и имеют метку `app.kubernetes.io/part-of=stroykontrol`. Манифесты не меняют существующие ingress controller, сертификаты, namespace и приложения других команд.

## Перед установкой

- DNS адреса указывает на существующий HTTPS ingress; в кластере доступны IngressClass `traefik`, ClusterIssuer `letsencrypt-prod` и NVIDIA device plugin на `lab51-asus`. Проверяйте имена узлов и свободные GPU/место перед повторным развёртыванием. Этот кластер не предоставляет динамическое хранилище: PV используют `hostPath` с `Retain`, жёстко закреплены за `vm-k8s-worker-01`, `graviton4` и `lab51-asus`. При потере узла данные автоматически **не** переедут.
- Локально нужны Docker и `kubectl`, собранные образы приложения, Keycloak 26, MediaMTX 1.21.1, PostgreSQL 17/16 и исходные `.pt`, видео, дампы трёх БД. Источники сборки: `Hakaton-main/backend`, `Hakaton-main/frontend`, `yolo_service`, а также поставляемые `lct-analytics-deterministic:0.5.0` и `lct-analytics-vlm-llm:0.5.0-spider`. `Dockerfile.analytics-bootstrap` собирается из deterministic-образа и каталога `service-release_1/service-release/references` (контекст сборки должен включать оба).
- Конфиденциальные `.env`, дампы и Docker config держите вне репозитория, с правами `0700` на каталог и `0600` на файлы. **Не меняйте `SK_SECRET_KEY` при переносе**: им зашифрованы существующие учётные данные камер. Сохраняйте согласованными секреты между сайтом, трекером и обоими аналитическими сервисами.
- Файл kubeconfig может содержать устаревший встроенный CA, хотя сертификат Rancher подписан действующим публичным CA. Используйте системный CA и проверяйте TLS, **не** используйте `--insecure-skip-tls-verify`:

```sh
KCFG='/home/aidar/Загрузки/local (1).yaml'
chmod 600 "$KCFG"
k() { kubectl --kubeconfig="$KCFG" --certificate-authority=/etc/ssl/certs/ca-certificates.crt "$@"; }
k get namespace construction
```

Для другого кластера используйте его собственный доверенный CA, имена узлов, storage и домен. Жёстко заданные узлы и адрес здесь отражают именно эту установку.

## Секреты (создавать до Deployment)

Сначала `k apply -f deploy/k8s/00-bootstrap.yaml` создаёт namespace, приватный реестр и его PVC. Для самого первого запуска реестра создайте в `construction` Secret `registry-auth` с ключом `htpasswd` (bcrypt) **до ожидания readiness**; `registry-pull` типа `kubernetes.io/dockerconfigjson` с учётной записью этого же реестра — до запуска приложений. Пример без пароля в истории команд: `htpasswd -Bn construction > /private/registry.htpasswd`, затем `k -n construction create secret generic registry-auth --from-file=htpasswd=/private/registry.htpasswd`; `docker login construction.g-309.ru -u construction` спросит пароль интерактивно, затем `k -n construction create secret generic registry-pull --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/private/docker/config.json`. Не помещайте Docker config в общую домашнюю папку.

Остальные Secret можно создать через `k -n construction create secret generic ИМЯ --from-env-file=/private/ИМЯ.env` после подготовки приватных файлов. Обязательные ключи (значения сюда **не** записывать):

| Secret | Ключи |
| --- | --- |
| `site-db-password` | `POSTGRES_PASSWORD` |
| `keycloak-runtime` | `KC_DB_PASSWORD`, `KC_BOOTSTRAP_ADMIN_PASSWORD` |
| `analytics-runtime` | `POSTGRES_PASSWORD`, `ANALYTICS_DETERMINISTIC_DB_PASSWORD`, `ANALYTICS_VLM_DB_PASSWORD`, `ANALYTICS_SERVICE_TOKEN`, `LITELLM_API_KEY` |
| `site-runtime` | `SK_DATABASE_URL`, `SK_SECRET_KEY`, `SK_KEYCLOAK_ADMIN_CLIENT_SECRET`, `SK_TRACKER_API_KEY`, `ANALYTICS_SERVICE_TOKEN`, `CAMERA_STAGE_MONITOR_URL`, `CAMERA_STAGE_MONITOR_TOKEN` |
| `tracker-runtime` | `SK_TRACKER_API_KEY` |

`SK_DATABASE_URL` указывает на `site-db:5432/stroykontrol` и использует пароль `site-db-password`. У существующего Keycloak переносите пароль bootstrap admin и секрет клиента `stroykontrol-backend` из локальной конфигурации. `CAMERA_STAGE_MONITOR_URL/TOKEN` и `LITELLM_API_KEY` должны соответствовать доступным сервисам; отсутствие рабочего внешнего поставщика модели не превращает VLM-анализ в успешный.

## Сохранение данных и запуск

1. Сделайте консистентные приватные дампы локальных БД: `pg_dump -Fc` для БД `stroykontrol` из сервиса `db` в `Hakaton-main/docker-compose.yml`, `keycloak` из `infra/keycloak/compose.clean.yml`, а также фактической БД аналитики из `service-release_1/service-release/compose.yaml` (здесь это `construction_stages`, пользователь `construction_app`). Для аналитики отдельно сохраните `pg_dumpall --roles-only`, чтобы восстановить роли `lct_deterministic_app` и `lct_vlm_app`. Сохраните содержимое Docker volume `hakaton-main_media`, действующую YOLO-модель и текущие ключи. Не переносите данные в чужие БД или PV.
2. Создайте только собственные PV/PVC и БД: `k apply -f deploy/k8s/10-storage.yaml` и `k apply -f deploy/k8s/20-databases.yaml`. Дождитесь readiness `site-db`, `keycloak-db`, `analytics-db`. Восстановите дампы **до** старта приложения (`k -n construction exec -i deploy/site-db -- pg_restore -U stroykontrol -d stroykontrol --no-owner --no-acl < /private/site.pgdump`, аналогично `keycloak-db`; для аналитики сначала восстановите роли и их права, затем схему/данные с владельцем `construction_app`). Перенесите media в PVC `site-media`, а `.pt` — в `tracker-model`, через временные pods **только namespace `construction`**; проверьте контрольные суммы. Не повторяйте `pg_restore` поверх работающей БД без плана отката.
3. Запустите `k -n construction rollout status deploy/registry`; при первоначальной публикации образов HTTPS proxy может разрывать долгую отправку слоя. Без изменения общего ingress используйте собственный локальный канал `k -n construction port-forward --address 127.0.0.1 svc/registry 15000:5000`: после `docker login 127.0.0.1:15000` образ `127.0.0.1:15000/stroykontrol/<имя>:<тег>` пишется в тот же repository, откуда узлы читают `construction.g-309.ru/stroykontrol/<имя>:<тег>`. Публикуются `backend`, `frontend`, `tracker-gpu`, `deterministic`, `vlm`, `bootstrap`; реальные теги перечислены в `30-app.yaml`. Для загрузки несколькими потоками открывайте отдельные `port-forward`, не меняя кластерную сеть/настройки ingress.
4. Запустите `k apply -f deploy/k8s/30-app.yaml` и проверьте `k -n construction get deploy,pods,ingress,certificate` и `k -n construction rollout status deploy/backend`. Job `analytics-bootstrap` намеренно **suspend: true** при восстановленной БД: повторная инициализация не должна стирать перенесённые справочники. На новой пустой БД восстановите обязательные роли и отдельно спланируйте первый bootstrap, не запускайте Job поверх данных.
5. В **собственном** Keycloak клиента `stroykontrol-web` разрешите redirect URI `https://construction.g-309.ru` и `https://construction.g-309.ru/*`, web origin `https://construction.g-309.ru` и отдельно post-logout redirect URI `https://construction.g-309.ru` (атрибут клиента `post.logout.redirect.uris`). Без последнего выход из приложения приводит к ошибке «Неверный uri для переадресации». Проверьте issuer `https://construction.g-309.ru/auth/realms/stroykontrol`. Локальные перенесённые учётные записи находятся в отдельной БД `keycloak-db`; их нельзя заменить одной импортированной тестовой realm-конфигурацией.

## Проверка и ограничения

- `curl -fSs https://construction.g-309.ru/auth/realms/stroykontrol/.well-known/openid-configuration` должен вернуть issuer с публичным HTTPS-адресом; `curl -I https://construction.g-309.ru/` проверяет TLS и сайт; анонимный `/api/sites` должен отвечать `401`.
- В браузере войдите через Keycloak, проверьте оба перенесённых объекта, обе камеры, план, каталог аналитики, отчёты и управление. Backend `/api/meta` должен показать `demoMode: false`, `authMode: keycloak`, `analysisProvider: push`, а `/api/tracker/status` — фактическую активность GPU, когда трекер запустился. Сверьте доступ прораба к назначенным объектам.
- Публичный ingress проксирует HTTP(S), но **не** публикует WebRTC ICE UDP 8189. Поэтому браузер получает HLS через same-origin `/hls/`: `hls.js` передаёт обновляемый Bearer-токен на **каждом** запросе, а frontend nginx через `auth_request` отдельно проверяет токен и право на конкретную камеру для master/media playlist, part и segment. Это необходимо: MediaMTX после первого входа сам по себе допускает чтение `?session=…` без Bearer. Проверяйте анонимный запрос **с действующим session ID**: он должен вернуть `401`; чужая камера — `403`. Не открывайте `cam-*` анонимно и не помещайте JWT в URL. HLS добавляет секунды задержки относительно локального WebRTC: синхронизацию рамок с кадром нужно оценивать по фактической задержке, а не предполагать точность прежних 150 мс.
- PV имеют `Retain`: удаление namespace не удаляет данные и registry на закреплённых узлах. Перед выводом установки снимите резервные копии, затем удаляйте только перечисленные здесь собственные ресурсы. Не удаляйте shared ingress, issuers, namespaces или приложения других команд.
