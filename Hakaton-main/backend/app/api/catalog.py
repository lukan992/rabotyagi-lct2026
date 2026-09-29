"""Объекты, зоны, календарный план, правила, сотрудники, сверка «план / факт»."""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SiteIdQuery, get_site, scope
from app.db import utcnow
from app.models import CameraOverlap, CheckRun, Rule, RuleItem, Site, Stage, Zone, new_id
from app.schemas import (
    CheckRow,
    CheckRunOut,
    EquipmentCheckOut,
    ExtraRow,
    RuleCreate,
    RuleIn,
    RuleOut,
    SiteOut,
    StageOut,
    ZoneOut,
    check_out,
    rule_out,
    site_out,
    stage_out,
    zone_out,
)
from app.security import CurrentUser, Session, require_roles
from app.services import audit
from app.services.analysis import detectable_types
from app.services.engine import current_stage, local_day, site_state
from app.services.overlap_count import estimate_counts
from app.services.pipeline import get_pipeline
from app.services.plan import site_progress, stages_by_site
from app.services.workhours import describe as describe_hours

router = APIRouter()


@router.get("/sites", response_model=list[SiteOut], tags=["Объекты"], summary="Объекты, доступные пользователю")
async def list_sites(user: CurrentUser, session: Session) -> list[SiteOut]:
    today = local_day(utcnow())
    sites = list(await session.scalars(scope(select(Site), Site.id, user).order_by(Site.position)))
    plans = await stages_by_site(session, [s.id for s in sites])
    out = []
    for site in sites:
        stage = await current_stage(session, site.id, today)
        out.append(site_out(site, stage.id if stage else None, site_progress(plans[site.id], today)))
    return out


@router.get("/zones", response_model=list[ZoneOut], tags=["Объекты"], summary="Зоны объектов")
async def list_zones(user: CurrentUser, session: Session, site_id: SiteIdQuery = None) -> list[ZoneOut]:
    rows = await session.scalars(scope(select(Zone), Zone.site_id, user, site_id).order_by(Zone.position, Zone.name))
    return [zone_out(z) for z in rows]


@router.get("/stages", response_model=list[StageOut], tags=["Календарный план"], summary="Этапы календарного плана")
async def list_stages(user: CurrentUser, session: Session, site_id: SiteIdQuery = None) -> list[StageOut]:
    today = local_day(utcnow())
    rows = await session.scalars(scope(select(Stage), Stage.site_id, user, site_id).order_by(Stage.position))
    return [stage_out(s, today) for s in rows]


@router.get("/rules", response_model=list[RuleOut], tags=["Правила"], summary="Методика «этап → техника»")
async def list_rules(_: CurrentUser, session: Session) -> list[RuleOut]:
    return [rule_out(r) for r in await session.scalars(select(Rule).order_by(Rule.position))]


def _rule_items(body: RuleIn, old: dict[tuple[str, str], RuleItem]) -> list[RuleItem]:
    """Строки правила из формы. У прежних строк сохраняем тексты риска и важность — в форме их нет."""
    required, unexpected = {r.type for r in body.required}, {u.type for u in body.unexpected}
    if len(required) != len(body.required) or len(unexpected) != len(body.unexpected):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Техника в списке повторяется")
    if required & unexpected:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Одна и та же техника не может быть и нужной, и лишней")
    items, n = [], 0
    for r in body.required:
        prev = old.get(("required", r.type))
        items.append(
            RuleItem(
                kind="required",
                equipment_type=r.type,
                min_count=r.min,
                why=r.why,
                risk=r.risk or (prev.risk if prev else ""),
                severity=prev.severity if prev else None,
                position=(n := n + 1),
            )
        )
    for kind in dict.fromkeys(body.allowed):
        if kind not in required and kind not in unexpected:
            items.append(RuleItem(kind="allowed", equipment_type=kind, position=(n := n + 1)))
    for u in body.unexpected:
        prev = old.get(("unexpected", u.type))
        items.append(
            RuleItem(
                kind="unexpected",
                equipment_type=u.type,
                why=u.why,
                risk=u.risk or (prev.risk if prev else ""),
                severity=prev.severity if prev else None,
                position=(n := n + 1),
            )
        )
    return items


async def _rule_name(session: AsyncSession, name: str, key: str | None = None) -> str:
    """Название этапа без пробелов по краям; два правила с одним названием путали бы выбор правила у работы плана."""
    name = " ".join(name.split())
    if len(name) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Название этапа — не короче 2 символов")
    # сравниваем здесь, а не в запросе: lower() в SQLite не понимает кириллицу
    for other_key, other in await session.execute(select(Rule.key, Rule.stage_name)):
        if other_key != key and other.casefold() == name.casefold():
            raise HTTPException(status.HTTP_409_CONFLICT, f"Правило «{other}» уже есть — выберите его или назовите новое иначе")
    return name


@router.post(
    "/rules",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Правила"],
    summary="Новое правило (администратор)",
    dependencies=[require_roles("admin")],
)
async def create_rule(body: RuleCreate, user: CurrentUser, session: Session, request: Request) -> RuleOut:
    name = await _rule_name(session, body.stage_name)
    position = (await session.scalar(select(func.max(Rule.position)))) or 0
    rule = Rule(
        key=new_id("rule"),
        stage_name=name,
        description=body.description.strip(),
        confirm_after=body.confirm_after_snapshots,
        position=position + 1,
        items=_rule_items(body, {}),
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    audit.record(
        session, request, user, "rule.create", f"Добавил правило «{name}»",
        entity_type="rule", entity_id=rule.key, entity_name=name, details=rule_out(rule).model_dump(by_alias=True),
    )  # fmt: skip
    await session.commit()
    return rule_out(rule)


@router.put(
    "/rules/{key}",
    response_model=RuleOut,
    tags=["Правила"],
    summary="Изменить правило (администратор)",
    dependencies=[require_roles("admin")],
)
async def update_rule(key: str, body: RuleIn, user: CurrentUser, session: Session, request: Request) -> RuleOut:
    rule = await session.get(Rule, key)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Правило не найдено")
    before = rule_out(rule).model_dump(by_alias=True)
    items = _rule_items(body, {(i.kind, i.equipment_type): i for i in rule.items})
    if body.stage_name is not None:
        rule.stage_name = await _rule_name(session, body.stage_name, key)
    if body.description is not None:
        rule.description = body.description.strip()
    rule.items, rule.confirm_after = items, body.confirm_after_snapshots
    await session.flush()
    await session.refresh(rule)
    after = rule_out(rule).model_dump(by_alias=True)
    audit.record(
        session, request, user, "rule.update", f"Изменил правило «{rule.stage_name}»",
        entity_type="rule", entity_id=key, entity_name=rule.stage_name, details=audit.changes(before, after),
    )  # fmt: skip
    await session.commit()

    # правило изменилось — объекты, где сейчас идёт этап с этим правилом, сверяем вне очереди (не дожидаясь минуты)
    today = local_day(utcnow())
    affected = set()
    for site_id in await session.scalars(select(Site.id)):
        stage = await current_stage(session, site_id, today)
        if stage and stage.rule_key == key:
            affected.add(site_id)
    get_pipeline().request_check(affected)
    return rule_out(rule)


@router.delete(
    "/rules/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Правила"],
    summary="Удалить правило (администратор)",
    dependencies=[require_roles("admin")],
)
async def delete_rule(key: str, user: CurrentUser, session: Session, request: Request) -> None:
    rule = await session.get(Rule, key)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Правило не найдено")
    # правило работы плана не отвязываем молча: без него работа перестала бы сверяться с техникой
    used = (
        await session.execute(
            select(Stage.name, Site.name)
            .join(Site, Site.id == Stage.site_id)
            .where(Stage.rule_key == key)
            .order_by(Site.position, Stage.position)
        )
    ).all()
    if used:
        more = f" и ещё {len(used) - 3}" if len(used) > 3 else ""
        works = "; ".join(f"«{stage}» ({site})" for stage, site in used[:3]) + more
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Правило используют работы плана: {works}. Сначала выберите для них другое правило"
        )
    audit.record(
        session, request, user, "rule.delete", f"Удалил правило «{rule.stage_name}»",
        entity_type="rule", entity_id=key, entity_name=rule.stage_name, details=rule_out(rule).model_dump(by_alias=True),
    )  # fmt: skip
    await session.delete(rule)
    await session.commit()


@router.get(
    "/sites/{site_id}/equipment-check",
    response_model=EquipmentCheckOut,
    tags=["Сверка"],
    summary="План и факт по технике для текущего этапа",
)
async def equipment_check(site_id: str, user: CurrentUser, session: Session) -> EquipmentCheckOut:
    site = await get_site(session, user, site_id)
    state = await site_state(session, site_id, utcnow())
    overlaps = list(await session.scalars(select(CameraOverlap).where(CameraOverlap.site_id == site_id)))
    estimated, overlap_matches = estimate_counts(state.latest, state.cameras, overlaps, max_skew_seconds=60)
    estimated_site, _ = estimate_counts(
        state.latest, state.cameras, overlaps, max_skew_seconds=60, work_only=False,
    )
    rows, extra = [], []
    detectable = detectable_types()
    if state.rule:
        for item in state.rule.of_kind("required"):
            have = state.observed[item.equipment_type]
            if item.equipment_type not in detectable:
                verdict = "not_detected"  # модель такую технику не распознаёт — проверяют на месте
            else:
                verdict = "ok" if have >= item.min_count else "missing" if have == 0 else "low"
            rows.append(CheckRow(type=item.equipment_type, need=item.min_count, have=have, why=item.why, state=verdict))
        seen = state.observed + state.arriving
        extra = [
            ExtraRow(type=i.equipment_type, have=seen[i.equipment_type], why=i.why)
            for i in state.rule.of_kind("unexpected")
            if seen[i.equipment_type]
        ]
    checked_at = max((s.taken_at for s in state.latest.values()), default=None)
    return EquipmentCheckOut(
        site_id=site_id,
        stage_id=state.stage.id if state.stage else None,
        stage_name=state.stage.name if state.stage else None,
        coverage=state.coverage,
        working=state.working,
        work_hours=describe_hours(site),
        checked_at=checked_at,
        rows=rows,
        extra=extra,
        arriving=dict(state.arriving),
        estimated_observed=estimated,
        estimated_site_observed=estimated_site,
        overlap_matches=overlap_matches,
    )


@router.get("/sites/{site_id}/checks", response_model=list[CheckRunOut], tags=["Сверка"], summary="Журнал проверок объекта")
async def list_checks(site_id: str, user: CurrentUser, session: Session, limit: int = 20) -> list[CheckRunOut]:
    await get_site(session, user, site_id)
    rows = await session.scalars(
        select(CheckRun).where(CheckRun.site_id == site_id).order_by(CheckRun.at.desc()).limit(min(max(limit, 1), 200))
    )
    return [check_out(c) for c in rows]
