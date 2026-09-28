"""Сверка объекта с планом: кадры камер → техника → правило этапа → отклонения.

Кадры приходят из конвейера живого видео (services/pipeline.py): анализ каждые 2 секунды, сверка раз в минуту.
Методика сверки:
  • Этап объекта берётся из календарного плана по дате проверки.
  • «Нужная» техника считается только в рабочих зонах. Техника на въезде и складе считается подъезжающей:
    она показывается в интерфейсе, но нехватку не закрывает.
  • «Лишняя» техника ищется во всех зонах.
  • Нехватка и лишняя техника подтверждаются, только если повторяются N проверок подряд (N задаётся в правиле) —
    так отсекаются случайные кадры: самосвал уехал на разгрузку, машина проехала мимо. Проверки, сделанные
    почти одновременно (правку правила сверяем сразу), считаются за одну: подтверждение не ускоряется.
  • Простой: техника из списка нужной не сдвинулась (IoU рамок ≥ 0.9) на 3+ кадрах за 2+ часа.
  • Камера без видео дольше 10 минут — отдельное предупреждение: зона стала «слепой».
  • Если условие перестало наблюдаться, предупреждение снимается автоматически (кроме тех, по которым выдано
    предписание: их закрывает инспектор).
"""

import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    OPEN_STATUSES,
    Alert,
    AlertEvent,
    Camera,
    CheckRun,
    Detection,
    Rule,
    Site,
    Snapshot,
    Stage,
    alert_evidence,
    new_id,
)
from app.security import decrypt_secret
from app.services import texts
from app.services.analysis import AnalysisError, AnalysisResult, detectable_types, get_analyzer
from app.services.analytics.observations import cleanup_observations, record_observations
from app.services.camera_client import CameraAddress
from app.services.texts import plural
from app.services.usage import cleanup_usage
from app.services.workhours import working_now

log = logging.getLogger("stroykontrol.engine")
settings = get_settings()
TZ = ZoneInfo(settings.timezone)

# кадр старше — считаем, что камера зону сейчас не видит (не меньше пяти минут и двух с половиной интервалов сверки)
FRESHNESS = max(timedelta(minutes=5), timedelta(seconds=settings.check_interval_s * 2.5))
# проверки ближе друг к другу считаются за одну: «3 проверки подряд» не должны пролетать за секунды
STREAK_MIN_GAP = timedelta(seconds=max(settings.check_interval_s * 0.8, 20))
IDLE_MIN_SNAPSHOTS = 3
IDLE_MIN_SPAN = timedelta(hours=2)
IDLE_WINDOW = timedelta(hours=8)
IDLE_IOU = 0.9
OFFLINE_AFTER = timedelta(minutes=10)
OFFLINE_IMPORTANT = timedelta(hours=1)  # камера молчит дольше часа — отклонение уже «Важно», а не «На заметку»
EVIDENCE_LIMIT = 6
FIRST_ALERT_NUMBER = 131
SYSTEM = "Система"

_site_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_numbering = asyncio.Lock()  # сквозной номер отклонения берём по одному: max+1 из двух мест дал бы одинаковые номера


def local_day(moment: datetime) -> date:
    return moment.astimezone(TZ).date()


def iou(a: Detection, b: Detection) -> float:
    left, top = max(a.x, b.x), max(a.y, b.y)
    right, bottom = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(right - left, 0) * max(bottom - top, 0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union else 0.0


# =====================================================================================
#  Кадры
# =====================================================================================
def camera_address(camera: Camera) -> CameraAddress:
    return CameraAddress(
        scheme=camera.scheme or "rtsp",
        host=camera.host or "",
        port=camera.port or 554,
        path=camera.path or "/",
        username=camera.username,
        password=decrypt_secret(camera.password_enc),
    )


def _store_frame(camera_id: str, jpeg: bytes, at: datetime) -> str:
    folder = settings.frames_dir / camera_id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{at.strftime('%Y%m%dT%H%M%S')}_{new_id('f')[2:]}.jpg"
    (folder / name).write_bytes(jpeg)
    return f"/media/frames/{camera_id}/{name}"


async def process_frame(
    session: AsyncSession,
    camera: Camera,
    jpeg: bytes,
    *,
    at: datetime,
    source: str,
    image_url: str | None = None,
    result: AnalysisResult | None = None,
) -> Snapshot:
    """Сохранить кадр и технику на нём. result — готовый разбор (конвейер видео уже отправлял кадр на анализ)."""
    if result is None:
        try:
            result = await get_analyzer().analyze(jpeg, camera_id=camera.id, taken_at=at)
        except AnalysisError as exc:
            log.warning("Анализ кадра камеры %s не удался: %s", camera.id, exc)
            result = AnalysisResult(provider=get_analyzer().name, supported=False, note=str(exc))
    detections, provider, note, elapsed, analyzed = (
        result.detections,
        result.provider,
        result.note,
        result.elapsed_ms,
        result.supported,
    )

    previous = await session.scalar(
        select(Snapshot)
        .where(Snapshot.camera_id == camera.id, Snapshot.analyzed, Snapshot.taken_at < at)
        .order_by(Snapshot.taken_at.desc())
        .limit(1)
    )
    snapshot = Snapshot(
        id=new_id("sn"),
        camera_id=camera.id,
        site_id=camera.site_id,
        taken_at=at,
        source=source,
        image_url=image_url or _store_frame(camera.id, jpeg, at),
        analyzed=analyzed,
        provider=provider,
        model=result.model,
        analysis_ms=elapsed,
        note=note,
    )
    for d in detections:
        item = Detection(equipment_type=d.type, confidence=d.confidence, x=d.x, y=d.y, w=d.w, h=d.h)
        if previous is not None:  # стоит ли техника на месте по сравнению с прошлым кадром
            same = [p for p in previous.detections if p.equipment_type == d.type]
            item.moving = not any(iou(item, p) >= IDLE_IOU for p in same)
        snapshot.detections.append(item)
    session.add(snapshot)

    camera.status, camera.last_error = "online", None
    camera.last_seen_at = max(at, camera.last_seen_at) if camera.last_seen_at else at
    camera.last_snapshot_at = max(at, camera.last_snapshot_at) if camera.last_snapshot_at else at
    return snapshot


class _Frame(Protocol):
    at: datetime
    jpeg: bytes
    result: AnalysisResult


async def check_site(session: AsyncSession, site_id: str, *, at: datetime, trigger: str, frames: dict[str, _Frame]) -> CheckRun:
    """Сверка объекта по кадрам из живого видео: сохранить по кадру на камеру, сверить с правилом. Сам коммитит.

    frames — камера → кадр, уже разобранный сервисом анализа. Камеры без свежего кадра в сверке не участвуют.
    """
    async with _site_locks[site_id]:
        cameras = {c.id: c for c in await site_cameras(session, site_id)}
        saved: list[tuple[Camera, Snapshot]] = []
        for camera_id, frame in frames.items():
            if camera := cameras.get(camera_id):
                snapshot = await process_frame(session, camera, frame.jpeg, at=frame.at, source="live", result=frame.result)
                saved.append((camera, snapshot))
        await session.flush()
        await record_observations(session, saved)  # история для сервисов аналитики — раз в 20 минут на камеру
        snapshots = [snapshot for _, snapshot in saved]
        check = await run_check(session, site_id, at=at, trigger=trigger)
        for snapshot in snapshots:
            snapshot.check_id = check.id
        await session.commit()
        return check


# =====================================================================================
#  Сверка
# =====================================================================================
async def current_stage(session: AsyncSession, site_id: str, day: date) -> Stage | None:
    return await session.scalar(
        select(Stage)
        .where(Stage.site_id == site_id, Stage.level == 2, Stage.start_date <= day, Stage.end_date >= day)
        .order_by(Stage.start_date, Stage.position)
        .limit(1)
    )


async def site_cameras(session: AsyncSession, site_id: str) -> list[Camera]:
    return list(
        await session.scalars(
            select(Camera).where(Camera.site_id == site_id, Camera.enabled, Camera.deleted_at.is_(None)).order_by(Camera.position)
        )
    )


async def latest_snapshots(session: AsyncSession, site_id: str, at: datetime) -> dict[str, Snapshot]:
    """Свежий разобранный кадр каждой камеры объекта на момент at."""
    rows = await session.scalars(
        select(Snapshot)
        .where(Snapshot.site_id == site_id, Snapshot.analyzed, Snapshot.taken_at <= at, Snapshot.taken_at >= at - FRESHNESS)
        .order_by(Snapshot.taken_at.desc())
    )
    latest: dict[str, Snapshot] = {}
    for snapshot in rows:
        latest.setdefault(snapshot.camera_id, snapshot)
    return latest


@dataclass
class SiteState:
    stage: Stage | None
    rule: Rule | None
    coverage: bool
    observed: Counter
    arriving: Counter
    latest: dict[str, Snapshot]
    cameras: list[Camera]
    working: bool = True  # рабочее время объекта: вне его технику не сверяем


async def site_state(session: AsyncSession, site_id: str, at: datetime) -> SiteState:
    stage = await current_stage(session, site_id, local_day(at))
    rule = await session.get(Rule, stage.rule_key) if stage and stage.rule_key else None
    cameras = await site_cameras(session, site_id)
    latest = await latest_snapshots(session, site_id, at)
    observed, arriving = Counter(), Counter()
    for camera in cameras:
        snapshot = latest.get(camera.id)
        if snapshot:
            target = observed if camera.zone.kind == "work" else arriving
            target.update(d.equipment_type for d in snapshot.detections)
    coverage = any(c.zone.kind == "work" and c.id in latest for c in cameras)
    site = await session.get(Site, site_id)
    return SiteState(stage, rule, coverage, observed, arriving, latest, cameras, working_now(site, at) if site else True)


async def _idle_chain(session: AsyncSession, camera_id: str, newest: Snapshot, kind: str) -> list[Snapshot]:
    """Цепочка кадров назад во времени, на которых техника данного типа стоит в одном и том же месте."""
    history = list(
        await session.scalars(
            select(Snapshot)
            .where(
                Snapshot.camera_id == camera_id,
                Snapshot.analyzed,
                Snapshot.taken_at < newest.taken_at,
                Snapshot.taken_at >= newest.taken_at - IDLE_WINDOW,
            )
            .order_by(Snapshot.taken_at.desc())
        )
    )
    best: list[Snapshot] = []
    for start in (d for d in newest.detections if d.equipment_type == kind):
        chain, anchor = [newest], start
        for snapshot in history:
            match = next((d for d in snapshot.detections if d.equipment_type == kind and iou(anchor, d) >= IDLE_IOU), None)
            if match is None:
                break
            chain.append(snapshot)
            anchor = match
        if len(chain) > len(best):
            best = chain
    return best[::-1]  # от старого к новому


async def run_check(session: AsyncSession, site_id: str, *, at: datetime, trigger: str) -> CheckRun:
    """Сверить состояние объекта с правилом этапа, записать проверку и обновить предупреждения."""
    state = await site_state(session, site_id, at)
    # вне рабочего времени техника не сверяется: ночью её нет, и это не отклонение
    rule, violations = (state.rule if state.working else None), []
    work = [c for c in state.cameras if c.zone.kind == "work"]
    idle_chains: dict[tuple[str, str], list[Snapshot]] = {}
    detectable = detectable_types()

    if rule and state.coverage:
        zone_id = next(c.zone_id for c in work if c.id in state.latest)
        for item in rule.of_kind("required"):
            if item.equipment_type not in detectable:
                continue  # такую технику модель не распознаёт — «не видим» ничего не значит
            have = state.observed[item.equipment_type]
            if have < item.min_count:
                violations.append(
                    {
                        "kind": "missing" if have == 0 else "count_below",
                        "equipment": item.equipment_type,
                        "expected": item.min_count,
                        "observed": have,
                        "zoneId": zone_id,
                        "cameraId": None,
                    }
                )
    if rule:
        unexpected = {i.equipment_type for i in rule.of_kind("unexpected")}
        required = {i.equipment_type for i in rule.of_kind("required")}
        for camera in state.cameras:
            snapshot = state.latest.get(camera.id)
            if not snapshot:
                continue
            for kind, count in Counter(d.equipment_type for d in snapshot.detections if d.equipment_type in unexpected).items():
                violations.append(
                    {
                        "kind": "unexpected",
                        "equipment": kind,
                        "expected": 0,
                        "observed": count,
                        "zoneId": camera.zone_id,
                        "cameraId": camera.id,
                    }
                )
            if camera.zone.kind != "work":
                continue
            for kind in required & {d.equipment_type for d in snapshot.detections}:
                chain = await _idle_chain(session, camera.id, snapshot, kind)
                if len(chain) >= IDLE_MIN_SNAPSHOTS and chain[-1].taken_at - chain[0].taken_at >= IDLE_MIN_SPAN:
                    idle_chains[(camera.id, kind)] = chain
                    violations.append(
                        {
                            "kind": "idle",
                            "equipment": kind,
                            "expected": None,
                            "observed": 1,
                            "zoneId": camera.zone_id,
                            "cameraId": camera.id,
                        }
                    )

    for camera in state.cameras:
        silent_since = camera.last_snapshot_at or camera.created_at
        if camera.status == "offline" and at - silent_since >= OFFLINE_AFTER:
            violations.append(
                {
                    "kind": "camera_offline",
                    "equipment": None,
                    "expected": None,
                    "observed": None,
                    "zoneId": camera.zone_id,
                    "cameraId": camera.id,
                }
            )

    check = CheckRun(
        id=new_id("chk"),
        site_id=site_id,
        at=at,
        trigger=trigger,
        stage_id=state.stage.id if state.stage else None,
        coverage=state.coverage,
        observed=dict(state.observed),
        arriving=dict(state.arriving),
        violations=violations,
    )
    session.add(check)
    await session.flush()
    async with _numbering:
        await _sync_alerts(session, check, state, idle_chains)
    return check


# =====================================================================================
#  Предупреждения
# =====================================================================================
Key = tuple[str, str | None, str | None]


def _violation_key(v: dict) -> Key:
    if v["kind"] in ("missing", "count_below"):
        return ("shortage", v["equipment"], None)
    if v["kind"] == "unexpected":
        return ("unexpected", v["equipment"], v["zoneId"])
    return (v["kind"], v["equipment"], v["cameraId"])


def _alert_key(a: Alert) -> Key:
    if a.kind in ("missing", "count_below"):
        return ("shortage", a.equipment_type, None)
    if a.kind == "unexpected":
        return ("unexpected", a.equipment_type, a.zone_id)
    return (a.kind, a.equipment_type, a.camera_id)


def _streak(runs: list[CheckRun], key: Key) -> tuple[int, datetime | None]:
    """Сколько последних проверок подряд содержат отклонение и когда серия началась.

    Проверка, сделанная ближе STREAK_MIN_GAP к уже засчитанной, отдельной не считается: иначе внеочередные
    сверки (правка правила, приём кадров извне) подтверждали бы отклонение за секунды по одной и той же картине.
    """
    length, started, last = 0, None, None
    for run in runs:  # от новых к старым
        if key[0] == "shortage" and not run.coverage:
            continue  # рабочую зону не было видно — проверка ничего не говорит о нехватке
        if key not in {_violation_key(v) for v in run.violations}:
            break
        started = run.at
        if last is not None and last - run.at < STREAK_MIN_GAP:
            continue
        length, last = length + 1, run.at
    return length, started


async def _next_number(session: AsyncSession) -> int:
    return (await session.scalar(select(func.max(Alert.number))) or FIRST_ALERT_NUMBER - 1) + 1


def _add_event(alert: Alert, at: datetime, text: str, *, who: str = SYSTEM, status: str | None = None) -> None:
    alert.events.append(AlertEvent(at=at, who=who, text=text, status=status))


async def _evidence(
    session: AsyncSession, camera_ids: list[str], start: datetime, end: datetime, kind: str | None = None
) -> list[Snapshot]:
    rows = list(
        await session.scalars(
            select(Snapshot)
            .where(Snapshot.camera_id.in_(camera_ids), Snapshot.analyzed, Snapshot.taken_at >= start, Snapshot.taken_at <= end)
            .order_by(Snapshot.taken_at)
        )
    )
    if kind:
        rows = [s for s in rows if any(d.equipment_type == kind for d in s.detections)]
    return rows[:1] + rows[1:][-(EVIDENCE_LIMIT - 1) :] if len(rows) > EVIDENCE_LIMIT else rows


async def _sync_alerts(
    session: AsyncSession, check: CheckRun, state: SiteState, idle_chains: dict[tuple[str, str], list[Snapshot]]
) -> None:
    rule, stage, at = state.rule, state.stage, check.at
    confirm_after = rule.confirm_after if rule else 3
    runs = list(
        await session.scalars(
            select(CheckRun)
            .where(CheckRun.site_id == check.site_id, CheckRun.at <= at)
            .order_by(CheckRun.at.desc(), CheckRun.id.desc())
            .limit(60)
        )
    )
    open_alerts = {
        _alert_key(a): a
        for a in await session.scalars(
            select(Alert).where(Alert.site_id == check.site_id, Alert.status.in_(OPEN_STATUSES), Alert.cleared_at.is_(None))
        )
    }
    # Закрытые человеком предупреждения, условие которых с тех пор ни разу не исчезало: пока картина та же,
    # повторно не поднимаем («Это ошибка: кран согласован» не должно возвращаться каждые пять минут).
    closed_by_people = list(
        await session.scalars(
            select(Alert).where(
                Alert.site_id == check.site_id, Alert.status.in_(("resolved", "false_positive")), Alert.cleared_at.is_(None)
            )
        )
    )
    suppressed = {_alert_key(a) for a in closed_by_people}
    detectable = detectable_types()
    cameras = {c.id: c for c in state.cameras}
    items = {(i.kind, i.equipment_type): i for i in rule.items} if rule else {}
    work_ids = [c.id for c in state.cameras if c.zone.kind == "work"]
    current: dict[Key, dict] = {_violation_key(v): v for v in check.violations}

    for key, v in current.items():
        kind, equipment, camera = v["kind"], v["equipment"], cameras.get(v["cameraId"] or "")
        length, started = _streak(runs, key)
        alert = open_alerts.get(key)
        if alert is None and key in suppressed:
            continue  # человек уже закрыл это отклонение, а картина не менялась
        if alert is None and kind in ("missing", "count_below", "unexpected") and length < confirm_after:
            continue  # ещё не подтверждено нужным числом проверок подряд

        # ---- тексты, важность и доказательства по виду отклонения ----
        if kind in ("missing", "count_below"):
            item = items.get(("required", equipment))
            started = (alert.started_at if alert else started) or at
            words = texts.shortage(
                equipment=equipment,
                expected=v["expected"],
                observed=v["observed"],
                stage_name=stage.name,
                checks=length,
                start=started,
                end=at,
                risk=item.risk if item else "",
            )
            severity = (item.severity if item else None) or ("high" if v["observed"] == 0 else "medium")
            zone_cameras = [c.id for c in state.cameras if c.zone_id == v["zoneId"]] or work_ids
            evidence = await _evidence(session, zone_cameras, started, at)
            created_note = f"Отклонение подтверждено: {length} {plural(length, 'проверка', 'проверки', 'проверок')} подряд."
        elif kind == "unexpected":
            item = items.get(("unexpected", equipment))
            started = (alert.started_at if alert else started) or at
            words = texts.unexpected(
                equipment=equipment,
                stage_name=stage.name,
                camera_name=camera.name,
                checks=length,
                start=started,
                end=at,
                why=item.why if item else "",
                risk=item.risk if item else "",
            )
            severity = (item.severity if item else None) or "medium"
            evidence = await _evidence(session, [camera.id], started, at, kind=equipment)
            created_note = f"Техника не по этапу видна {length} {plural(length, 'проверку', 'проверки', 'проверок')} подряд."
        elif kind == "idle":
            chain = idle_chains[(camera.id, equipment)]
            started = alert.started_at if alert else chain[0].taken_at
            words = texts.idle(equipment=equipment, camera_name=camera.name, start=started, end=at)
            severity = "medium"
            evidence = chain[:1] + chain[1:][-(EVIDENCE_LIMIT - 1) :] if len(chain) > EVIDENCE_LIMIT else chain
            created_note = f"Техника стоит на одном месте {texts.duration(chain[-1].taken_at - chain[0].taken_at)}."
        else:  # camera_offline
            started = alert.started_at if alert else (camera.last_snapshot_at or camera.created_at)
            words = texts.camera_offline(
                camera_name=camera.name, zone_name=camera.zone.name, last_snapshot=camera.last_snapshot_at, now=at
            )
            # первый час — «на заметку» (камеру могли просто перезагрузить), дальше «слепая» зона — это уже важно
            severity = "medium" if at - started >= OFFLINE_IMPORTANT else "low"
            last = await session.scalar(
                select(Snapshot).where(Snapshot.camera_id == camera.id).order_by(Snapshot.taken_at.desc()).limit(1)
            )
            evidence = [last] if last else []
            created_note = "Видео с камеры нет дольше 10 минут — зона не просматривается."

        if alert is None:
            alert = Alert(
                id=new_id("a"),
                number=await _next_number(session),
                site_id=check.site_id,
                zone_id=v["zoneId"],
                stage_id=stage.id if stage else None,
                camera_id=v["cameraId"],
                kind=kind,
                severity=severity,
                status="new",
                equipment_type=equipment,
                expected=v["expected"],
                observed=v["observed"],
                started_at=started,
                updated_at=at,
                **words.__dict__,
            )
            alert.evidence = list(evidence)
            _add_event(alert, at, created_note, status="new")
            session.add(alert)
            await session.flush()
            continue

        if alert.kind != kind:  # нехватка сменила вид: «нет совсем» ↔ «меньше нормы»
            eq = texts.EQUIPMENT[equipment]
            _add_event(
                alert,
                at,
                f"Теперь видно {v['observed']} из {v['expected']} ({eq.gen_pl})."
                if kind == "count_below"
                else f"Техника снова пропала: {eq.gen_pl} не видно.",
            )
        alert.kind, alert.severity, alert.observed, alert.expected = kind, severity, v["observed"], v["expected"]
        alert.title, alert.summary = words.title, words.summary
        alert.consequence, alert.advice = words.consequence, words.advice
        alert.evidence, alert.updated_at = list(evidence), at

    # ---- условие больше не наблюдается ----
    def judged(alert: Alert) -> bool:
        """Можно ли по этой проверке сказать, что условия больше нет: зону видно, камера на связи, рабочее время."""
        if alert.kind != "camera_offline" and not state.working:
            return False  # ночью техники нет — это не «нехватка устранена»
        if alert.equipment_type and alert.equipment_type not in detectable:
            return False  # модель такую технику не видит — «не видим» не значит «устранено»
        if alert.kind in ("missing", "count_below"):
            return bool(state.coverage and rule)
        if alert.kind in ("unexpected", "idle"):
            return alert.camera_id in state.latest
        return True  # «камера не отвечает»: видео снова пришло

    for alert in closed_by_people:
        # картина изменилась — если отклонение повторится, это будет новое предупреждение. Но «не видно» — не «изменилась»:
        # иначе закрытое человеком возвращалось после любой паузы в видео
        if _alert_key(alert) not in current and judged(alert):
            alert.cleared_at = at
    for key, alert in open_alerts.items():
        if key in current:
            continue
        if alert.camera_id and alert.camera_id not in cameras:
            # камеру выключили или удалили — следить нечем. Это не «отклонение больше не наблюдается»
            note = "Камера выключена или удалена — отслеживать это отклонение больше нечем."
            alert.cleared_at, alert.updated_at = at, at
            if alert.status == "prescribed":
                _add_event(alert, at, f"{note} Предписание закрывает инспектор.")
            else:
                alert.status, alert.resolved_at = "resolved", at
                _add_event(alert, at, f"{note} Снято автоматически.", status="resolved")
            continue
        if not judged(alert):
            continue  # зону не видно или камера молчит — судить рано
        alert.cleared_at = at
        if alert.status == "prescribed":
            _add_event(alert, at, "Отклонение больше не наблюдается. Предписание закрывает инспектор.")
        else:
            alert.status, alert.resolved_at = "resolved", at
            _add_event(alert, at, "Отклонение больше не наблюдается — снято автоматически.", status="resolved")
        alert.updated_at = at


# =====================================================================================
#  Обслуживание
# =====================================================================================
KEEP_CHECKS_PER_SITE = 600


async def cleanup_frames(session: AsyncSession) -> int:
    """Удалить старые кадры сверх лимита на камеру, старые записи проверок, учёт работы техники старше
    keep_usage_days и журнал наблюдений старше окна истории. Кадры-доказательства не трогаем."""
    removed = 0
    evidence_ids = select(alert_evidence.c.snapshot_id)
    await cleanup_usage(session)
    await cleanup_observations(session)
    for site_id in await session.scalars(select(Site.id)):
        stale = select(CheckRun.id).where(CheckRun.site_id == site_id).order_by(CheckRun.at.desc()).offset(KEEP_CHECKS_PER_SITE)
        await session.execute(delete(CheckRun).where(CheckRun.id.in_(stale)))
    for camera_id in await session.scalars(select(Camera.id)):
        old = list(
            await session.scalars(
                select(Snapshot)
                .where(Snapshot.camera_id == camera_id, Snapshot.id.not_in(evidence_ids))
                .order_by(Snapshot.taken_at.desc())
                .offset(settings.keep_frames_per_camera)
            )
        )
        for snapshot in old:
            if snapshot.image_url.startswith("/media/frames/"):
                (settings.data_dir / "media" / snapshot.image_url.removeprefix("/media/")).unlink(missing_ok=True)
        if old:
            await session.execute(delete(Snapshot).where(Snapshot.id.in_([s.id for s in old])))
            removed += len(old)
    await session.commit()
    return removed


async def all_site_ids(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(Site.id).order_by(Site.position)))
