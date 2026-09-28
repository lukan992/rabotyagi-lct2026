"""Разбор одного снимка: техника → сверка с правилом этапа → отклонения. Для страниц «Проверить фото» и /demo."""

import base64
from collections import Counter

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import utcnow
from app.equipment import EQUIPMENT, at_least
from app.models import Rule
from app.schemas import AnalyzeOut, ApiModel, BoxOut, CheckRow, DetectionOut, DeviationOut, RuleOut, rule_out
from app.security import CurrentUser, Session
from app.services.analysis import AnalysisError, detectable_types, get_analyzer, get_mock
from app.services.camera_client import CameraError, mock_frame, normalize_frame_async
from app.services.engine import current_stage, local_day

settings = get_settings()
router = APIRouter(tags=["Разбор снимка"])

SAMPLES = [  # имя кадра, подпись
    ("pit-excavator", "Котлован: только экскаватор"),
    ("pit-loading", "Котлован: экскаватор и самосвал"),
    ("gate-crane", "Въезд: автокран"),
    ("foundation-mixers", "Фундамент: два бетоносмесителя"),
    ("road-roller-b", "Дорога: один каток"),
]


class SampleOut(ApiModel):
    id: str
    label: str
    image_url: str


class DemoOut(ApiModel):
    rules: list[RuleOut]
    samples: list[SampleOut]
    provider: str


def _samples() -> list[SampleOut]:
    return [SampleOut(id=name, label=label, image_url=f"/media/seed/{name}.jpg") for name, label in SAMPLES]


async def _analyze(session: AsyncSession, *, image: UploadFile | None, sample: str | None, rule_key: str) -> AnalyzeOut:
    rule = await session.get(Rule, rule_key)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Правило этапа не найдено")
    if sample:
        if sample not in get_mock().annotations:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого примера нет")
        jpeg, image_url = mock_frame(sample), f"/media/seed/{sample}.jpg"
    elif image is not None:
        raw = await image.read(settings.max_frame_bytes + 1)
        if len(raw) > settings.max_frame_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл слишком большой")
        try:
            jpeg = await normalize_frame_async(raw)
        except CameraError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Это не картинка. Загрузите фото JPG или PNG.") from None
        # рамки считаются по кадру 16:9 — возвращаем именно его, чтобы они легли на изображение точно
        image_url = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нужно фото или пример")

    try:
        result = await get_analyzer().analyze(jpeg, taken_at=utcnow())
    except AnalysisError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None

    observed = Counter(d.type for d in result.detections)
    rows, deviations = [], []
    detectable = detectable_types()
    if result.supported:
        for item in rule.of_kind("required"):
            have, eq = observed[item.equipment_type], EQUIPMENT[item.equipment_type]
            if item.equipment_type not in detectable:  # модель такую технику не распознаёт — не отклонение
                state = "not_detected"
            else:
                state = "ok" if have >= item.min_count else "missing" if have == 0 else "low"
            rows.append(CheckRow(type=item.equipment_type, need=item.min_count, have=have, state=state, why=item.why))
            if state in ("missing", "low"):
                title = (
                    f"Нет {eq.gen_pl} — возможное снижение темпа работ"
                    if have == 0
                    else f"Мало {eq.gen_pl}: {have} из {item.min_count}"
                )
                why = (
                    f"Этап «{rule.stage_name}» требует {at_least(item.min_count, eq)} ({item.why.lower()}), "
                    f"а на снимке — {have}. {item.risk}"
                ).strip()
                deviations.append(
                    DeviationOut(
                        kind="missing" if have == 0 else "count_below",
                        type=item.equipment_type,
                        need=item.min_count,
                        have=have,
                        title=title,
                        why=why,
                    )
                )
        for item in rule.of_kind("unexpected"):
            if have := observed[item.equipment_type]:
                eq = EQUIPMENT[item.equipment_type]
                deviations.append(
                    DeviationOut(
                        kind="unexpected",
                        type=item.equipment_type,
                        need=None,
                        have=have,
                        title=f"{eq.name} не соответствует этапу",
                        why=f"Этап «{rule.stage_name}» не предусматривает такую технику ({item.why.lower()}), а на снимке она есть: {have} шт.",
                    )
                )
    return AnalyzeOut(
        provider=result.provider,
        model=result.model,
        supported=result.supported,
        note=result.note,
        elapsed_ms=result.elapsed_ms,
        image_url=image_url,
        rule_key=rule.key,
        stage_name=rule.stage_name,
        rows=rows,
        deviations=deviations,
        detections=[
            DetectionOut(id=f"u{i}", type=d.type, confidence=d.confidence, box=BoxOut(x=d.x, y=d.y, w=d.w, h=d.h))
            for i, d in enumerate(result.detections)
        ],
    )


@router.get("/analyze/samples", response_model=list[SampleOut], summary="Готовые примеры снимков")
async def samples(_: CurrentUser) -> list[SampleOut]:
    _demo_only()
    return _samples()


@router.post("/analyze", response_model=AnalyzeOut, summary="Разобрать фото и сверить с этапом объекта или правилом")
async def analyze(
    user: CurrentUser,
    session: Session,
    image: UploadFile | None = File(None),
    sample: str | None = Form(None),
    site_id: str | None = Form(None, alias="siteId"),
    rule_key: str | None = Form(None, alias="ruleKey"),
) -> AnalyzeOut:
    if not rule_key and site_id:
        from app.api.deps import get_site

        await get_site(session, user, site_id)
        stage = await current_stage(session, site_id, local_day(utcnow()))
        rule_key = stage.rule_key if stage else None
    if not rule_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите объект или правило этапа")
    if sample:
        _demo_only()
    return await _analyze(session, image=image, sample=sample, rule_key=rule_key)


# ---------- без входа: страница /demo (только в демо-режиме) ----------
def _demo_only() -> None:
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Демо-режим выключен")


@router.get("/public/demo", response_model=DemoOut, summary="Данные страницы сверки без входа")
async def public_demo(session: Session) -> DemoOut:
    _demo_only()
    rules = [rule_out(r) for r in await session.scalars(select(Rule).order_by(Rule.position))]
    return DemoOut(rules=rules, samples=_samples(), provider=get_analyzer().name)


@router.post("/public/analyze", response_model=AnalyzeOut, summary="Разобрать фото без входа")
async def public_analyze(
    session: Session,
    rule_key: str = Form(..., alias="ruleKey"),
    image: UploadFile | None = File(None),
    sample: str | None = Form(None),
) -> AnalyzeOut:
    _demo_only()
    return await _analyze(session, image=image, sample=sample, rule_key=rule_key)
