"""Тесты работают на отдельной временной базе и без сети: шлюз видео выключен, кадры в конвейер подаются из теста."""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="stroykontrol-tests-"))
os.environ.update(
    {
        # по умолчанию — временный SQLite; чтобы прогнать те же тесты на PostgreSQL, задайте SK_TEST_DATABASE_URL
        "SK_DATABASE_URL": os.environ.get("SK_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_TMP / 'test.db'}"),
        "SK_DATA_DIR": str(_TMP),
        "SK_VIDEO_ENABLED": "false",  # шлюза и ffmpeg в тестах нет: сверку запускают сами тесты
        "SK_CHECK_INTERVAL_S": "0",
        "SK_INGEST_API_KEY": "ingest-test-key",
        "SK_DEMO_MODE": "true",  # тестовые фикстуры явно используют стендовые данные
        "SK_SEED_ON_START": "true",
        "SK_DEMO_PASSWORD": "stand-password",
        "SK_ANALYSIS_PROVIDER": "mock",
        # сервисы аналитики в тестах подключают сами тесты (имитация без сокетов) — не те, что заданы в backend/.env
        "DETERMINISTIC_SERVICE_URL": "",
        "VLM_LLM_SERVICE_URL": "",
        "ANALYTICS_SERVICE_TOKEN": "",
    }
)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import reset  # noqa: E402
from app.services import pipeline  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    """Свежая демонстрационная база + HTTP-клиент, подключённый к приложению напрямую (без сокетов)."""
    await reset()
    pipeline._pipeline = None  # живое состояние камер у каждого теста своё
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8100") as http:
        yield http
    await engine.dispose()  # у каждого теста свой цикл событий — соединения PostgreSQL между ними не переносим


async def login_as(http: httpx.AsyncClient, role: str) -> dict[str, str]:
    response = await http.post("/api/auth/demo-login", json={"role": role})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def feed(camera_id: str, photo: str, *, at=None) -> None:  # noqa: ANN001
    """Подать в конвейер «кадр из видео» камеры: демо-фото, разобранное демо-анализатором (как делает шлюз + анализ)."""
    from app.db import utcnow
    from app.services.analysis import get_mock
    from app.services.camera_client import mock_frame
    from app.services.pipeline import CameraLive, LiveFrame, get_pipeline

    jpeg, at = mock_frame(photo), at or utcnow()
    result = await get_mock().analyze_photo(jpeg, key=camera_id)
    live = get_pipeline().live.setdefault(camera_id, CameraLive(camera_id))
    live.frame, live.online, live.error, live.received_at = LiveFrame(at=at, jpeg=jpeg, result=result), True, None, at
    live.history.append(live.frame)


async def check(site_id: str, *, at=None, trigger: str = "schedule") -> None:  # noqa: ANN001
    """Одна плановая сверка объекта по тому, что сейчас в конвейере."""
    from app.services.pipeline import get_pipeline

    await get_pipeline().check(site_id, trigger=trigger, at=at)
