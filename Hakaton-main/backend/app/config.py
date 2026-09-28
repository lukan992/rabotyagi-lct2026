"""Настройки приложения. Все переменные окружения начинаются с префикса SK_ (см. .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # каталог backend/
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


# ключи из примеров в README, docker-compose и launch.json — открыты всем, в боевом запуске их быть не должно
PUBLIC_KEYS = {"dev-tracker-key", "dev-ingest-key", "ingest-test-key", "change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SK_", env_file=".env", extra="ignore")

    # --- общее ---
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'stroykontrol.db'}"
    data_dir: Path = BASE_DIR / "data"
    timezone: str = "Europe/Moscow"
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    # --- доступ ---
    secret_key: str = "dev-only-secret-change-me"  # подпись токенов и шифрование паролей камер
    token_ttl_hours: int = 12
    demo_mode: bool = False  # явный стендовый режим: вход по роли, публичная сверка, демо-камера
    demo_password: str = "demo"  # пароль учётных записей стенда, задаётся при первом наполнении базы
    seed_on_start: bool = False  # явный стендовый режим: наполнить пустую базу демонстрационными данными

    # --- Keycloak (включается, если задан публичный issuer) ---
    keycloak_issuer: str | None = None  # напр. http://localhost:8180/realms/stroykontrol — URL для браузера и проверки iss
    # внутренний base URL только для запросов backend (discovery, JWKS, Admin REST); issuer токена не меняет
    keycloak_backchannel_url: str | None = None  # напр. http://keycloak:8080 в Docker-сети
    keycloak_client_id: str = "stroykontrol-web"  # клиент, чьи токены принимаем (проверяется azp/aud)
    keycloak_auto_provision: bool = True  # пускать пользователя Keycloak, которого нет в базе (без привязки к объектам)
    # служебный клиент, от имени которого наша админка меняет в Keycloak пользователей, роли и пароли
    keycloak_admin_client_id: str = "stroykontrol-backend"
    keycloak_admin_client_secret: str | None = None

    # --- анализ кадров ---
    # local — своя модель (detector_model) прямо в сервере; auto — local, если файл модели на месте, иначе mock;
    # mock — демо-заглушка (знает только демо-ролики и демо-фото); http — каждый кадр (раз в frame_interval_s) уходит
    # во внешний сервис; push — внешний сервис сам присылает детекции в /api/ingest, сервер кадры не разбирает
    analysis_provider: Literal["auto", "local", "mock", "http", "push"] = "auto"
    analysis_api_url: str | None = None  # полный адрес метода, например http://ml:8200/analyze
    analysis_api_key: str | None = None
    analysis_timeout_s: float = 20.0
    ingest_api_key: str | None = None  # ключ для приёма готовых детекций от внешнего сервиса (push-режим)

    # --- своя модель распознавания: YOLO в формате ONNX (новая версия — tools/export_model.py) ---
    detector_model: Path = BASE_DIR / "models" / "detector.onnx"
    detector_confidence: float = Field(0.35, gt=0, lt=1)  # рамки с уверенностью ниже не показываем и не учитываем
    detector_threads: int = Field(0, ge=0)  # ядер процессора на модель; 0 — решает ONNX Runtime
    detector_accelerate: bool = True  # CoreML на Mac (вдвое быстрее, ответы те же), CUDA — если стоит onnxruntime-gpu
    # класс модели → тип техники, если имена не совпали с нашими: {"Truck": "truck", "Pump truck": ""} ("" — не показывать)
    detector_classes: dict[str, str] = {}
    # рамки в реальном времени своей моделью: сколько раз в секунду разбирать видео камеры, которую сейчас смотрят;
    # 0 — не разбирать видео, рамки только из анализа кадров раз в frame_interval_s
    realtime_fps: float = Field(8.0, ge=0, le=25)
    realtime_max_cameras: int = Field(8, ge=1)  # больше камер сразу видео не разбираем: модели не хватит на всех

    # --- камеры и видео ---
    # Видео идёт через шлюз mediamtx: он забирает RTSP с камер и отдаёт браузеру WebRTC. Сервер сам заводит в шлюзе
    # по потоку на камеру (через его API), берёт из потоков кадры на анализ и отвечает шлюзу, кому можно смотреть.
    video_enabled: bool = True  # False — без шлюза (тесты): потоки не заводятся, кадры не берутся
    video_api_url: str = "http://127.0.0.1:9997"  # API шлюза
    video_rtsp_url: str = "rtsp://127.0.0.1:8554"  # RTSP шлюза: отсюда сервер берёт кадры и сюда публикует демо-ролики
    video_webrtc_url: str = "http://localhost:8889"  # WebRTC шлюза — этот адрес получает браузер
    frame_interval_s: float = 2.0  # раз во сколько секунд кадр с каждой камеры уходит на анализ
    check_interval_s: int = 60  # раз во сколько секунд объект сверяется с планом; 0 — не сверять в фоне
    camera_timeout_s: float = 6.0
    allow_loopback_cameras: bool = True  # разрешить 127.0.0.1 (демо-ролики крутит шлюз на этом же компьютере)
    max_frame_bytes: int = 12 * 1024 * 1024
    keep_frames_per_camera: int = 200  # сколько сохранённых кадров держать на камеру (доказательства не удаляются)
    keep_usage_days: int = Field(30, ge=1)  # сколько дней хранить учёт работы техники по часам и ответы аналитики

    # --- какая работа идёт на кадре: два сервиса аналитики коллеги (контракт frame-analysis-v1, app/services/analytics) ---
    # Имена переменных — из контракта; с приставкой SK_ тоже работают. Базовые адреса без пути метода,
    # например http://analytics-deterministic:8000; не задан ни один — работы по камерам не определяются.
    deterministic_service_url: str | None = Field(
        None, validation_alias=AliasChoices("DETERMINISTIC_SERVICE_URL", "SK_DETERMINISTIC_SERVICE_URL")
    )  # по технике на кадре, истории и правилам плана
    vlm_llm_service_url: str | None = Field(
        None, validation_alias=AliasChoices("VLM_LLM_SERVICE_URL", "SK_VLM_LLM_SERVICE_URL")
    )  # по изображению: VLM → LLM
    analytics_service_token: str | None = Field(
        None, validation_alias=AliasChoices("ANALYTICS_SERVICE_TOKEN", "SK_ANALYTICS_SERVICE_TOKEN")
    )  # уходит обоим в Authorization: Bearer …
    # сколько ждать ответ: модельные попытки сервиса по изображению — до 4 × 240 с
    analytics_http_timeout_seconds: float = Field(
        1020.0,
        gt=0,
        le=3600,
        validation_alias=AliasChoices("ANALYTICS_HTTP_TIMEOUT_SECONDS", "SK_ANALYTICS_HTTP_TIMEOUT_SECONDS"),
    )
    analytics_interval_min: int = Field(20, ge=1, le=24 * 60)  # раз во сколько минут отправлять свежий кадр каждой камеры
    # «по снимку» — реже: каждый вызов платный (модель через gateway коллеги) и думает около минуты
    analytics_vlm_interval_min: int = Field(60, ge=1, le=24 * 60)
    analytics_history_days: float = Field(7.0, gt=0, le=30)  # окно истории наблюдений в запросе (сервис смотрит неделю)
    # история для сервисов: раз во сколько минут записывать кадр камеры в журнал наблюдений (без картинки). Сервису
    # нужны точки не чаще 15 минут, а в запросе их не больше 500: неделя одной камеры — это шаг 20 минут
    analytics_observation_min: int = Field(20, ge=1, le=24 * 60)
    # тип техники → класс справочника сервиса, если его не нашлось среди кодов и синонимов справочника:
    # {"manipulator": "crane_manipulator"}; "" — такую технику сервису не отправлять
    analytics_classes: dict[str, str] = {}

    # --- рамки техники в реальном времени от внешнего сервиса разметки (детектор + трекер) ---
    # Не нужен, если кадры разбирает своя модель (local): она сама ведёт рамки по видео (realtime_fps).
    # Сервис сам читает видео из шлюза и отдаёт рамки по WebSocket; сервер пересылает их браузерам (с проверкой прав).
    tracker_url: str | None = None  # WebSocket сервиса, например ws://127.0.0.1:8200/stream
    tracker_api_key: str | None = None  # ключ сервиса: список камер, чтение видео из шлюза, подключение к его WebSocket
    # адрес шлюза, по которому сервис разметки читает видео, если он работает не там, где сервер (другой компьютер, своя
    # сеть Docker): например rtsp://192.168.1.10:8554. Не задан — тот же, что у сервера (SK_VIDEO_RTSP_URL)
    tracker_rtsp_url: str | None = None
    # на столько браузер придерживает видео, чтобы рамки совпадали с картинкой; браузер принимает 0–4000 мс
    tracker_video_delay_ms: int = Field(150, ge=0, le=4000)

    # --- журнал интервалов техники от tracker ---
    equipment_observation_timeout_seconds: float = Field(90.0, gt=0)

    # --- Camera Stage Monitor (Spider): независимый источник демонстрационного плана и ресурсов ---
    camera_stage_monitor_url: str | None = Field(
        None,
        validation_alias=AliasChoices("CAMERA_STAGE_MONITOR_URL", "SK_CAMERA_STAGE_MONITOR_URL"),
    )
    camera_stage_monitor_token: SecretStr | None = Field(
        None,
        validation_alias=AliasChoices("CAMERA_STAGE_MONITOR_TOKEN", "SK_CAMERA_STAGE_MONITOR_TOKEN"),
    )
    camera_stage_monitor_timeout_seconds: float = Field(
        15.0,
        gt=0,
        validation_alias=AliasChoices("CAMERA_STAGE_MONITOR_TIMEOUT_SECONDS", "SK_CAMERA_STAGE_MONITOR_TIMEOUT_SECONDS"),
    )
    camera_stage_monitor_refresh_seconds: float = Field(0.0, ge=0)
    camera_stage_monitor_max_retries: int = Field(1, ge=0, le=1)

    @field_validator("detector_classes")
    @classmethod
    def _known_equipment(cls, value: dict[str, str]) -> dict[str, str]:
        from app.equipment import EQUIPMENT_TYPES

        if unknown := {k: v for k, v in value.items() if v and v not in EQUIPMENT_TYPES}:
            raise ValueError(f"неизвестные типы техники {unknown}; можно: {', '.join(EQUIPMENT_TYPES)} или пусто")
        return value

    @field_validator("analytics_classes")
    @classmethod
    def _known_types(cls, value: dict[str, str]) -> dict[str, str]:
        from app.equipment import EQUIPMENT_TYPES

        if unknown := set(value) - set(EQUIPMENT_TYPES):
            raise ValueError(f"неизвестные типы техники {sorted(unknown)}; можно: {', '.join(EQUIPMENT_TYPES)}")
        return value

    @field_validator("deterministic_service_url", "vlm_llm_service_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        value = (value or "").strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(f"нужен адрес http(s)://…, а задан {value!r}")
        if value.endswith("/v1/analyze/frame"):
            raise ValueError("нужен базовый адрес сервиса без пути метода: без /v1/analyze/frame")
        return value or None

    @field_validator("keycloak_backchannel_url")
    @classmethod
    def _keycloak_backchannel_url(cls, value: str | None) -> str | None:
        value = (value or "").strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(f"нужен адрес http(s)://…, а задан {value!r}")
        return value or None

    def insecure_defaults(self) -> list[str]:
        """Что нельзя оставлять по умолчанию в боевом запуске (демо-режим выключен)."""
        if self.demo_mode:
            return []
        fields = type(self).model_fields
        problems = []
        # оба стандартных ключа (здесь и в docker-compose.yml) открыты в репозитории — по ним любой подпишет себе вход администратора
        if self.secret_key in {fields["secret_key"].default, "change-me-before-real-use"} or len(self.secret_key) < 16:
            problems.append("SK_SECRET_KEY: задайте свой случайный ключ не короче 16 символов (стандартный открыт в репозитории)")
        if self.seed_on_start:
            problems.append("SK_SEED_ON_START=true требует SK_DEMO_MODE=true; демонстрационные данные не добавляются в рабочую базу")
        # ключ сервиса открывает список камер, чтение их видео по RTSP и приём кадров — примеры из README не годятся
        for name in ("tracker_api_key", "ingest_api_key"):
            key = getattr(self, name)
            if key and (key in PUBLIC_KEYS or len(key) < 16):
                problems.append(
                    f"SK_{name.upper()}: задайте свой случайный ключ не короче 16 символов (пример открыт в репозитории)"
                )
        return problems

    @property
    def analytics_services(self) -> dict[str, str]:
        """Подключённые сервисы аналитики: имя по контракту → базовый адрес."""
        urls = {"deterministic": self.deterministic_service_url, "vlm_llm": self.vlm_llm_service_url}
        return {name: url for name, url in urls.items() if url}

    @property
    def auth_mode(self) -> str:
        return "keycloak" if self.keycloak_issuer else "local"

    @property
    def keycloak_url(self) -> str | None:
        """База Keycloak без /realms/<realm> — нужна фронтенду для keycloak-js."""
        return self.keycloak_issuer.split("/realms/")[0] if self.keycloak_issuer else None

    @property
    def keycloak_realm(self) -> str | None:
        return self.keycloak_issuer.rstrip("/").split("/realms/")[-1] if self.keycloak_issuer else None

    @property
    def keycloak_backchannel_issuer(self) -> str | None:
        """URL realm для серверных запросов; без backchannel остаётся публичным issuer."""
        if not self.keycloak_issuer:
            return None
        if not self.keycloak_backchannel_url:
            return self.keycloak_issuer
        return f"{self.keycloak_backchannel_url}/realms/{self.keycloak_realm}"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "media" / "frames"


    @property
    def spider_images_dir(self) -> Path:
        return self.data_dir / "spider" / "images"
    @property
    def clips_dir(self) -> Path:
        """Ролики демо-камер (H.264, крутятся по кругу)."""
        return ASSETS_DIR / "clips"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
