"""Календарный план из Excel или CSV: шаблон для заполнения и загрузка — сначала предпросмотр, потом по подтверждению."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select, update

from app.api.deps import get_site
from app.models import Alert, CheckRun, Rule, Stage, new_id
from app.schemas import PlanImportOut, PlanImportPhase, PlanImportWork
from app.security import SITE_MANAGERS, CurrentUser, Session, require_roles
from app.services import audit, plan_import
from app.services.analytics.catalog import Catalog, CatalogError, object_type
from app.services.analytics.runner import get_analytics
from app.services.pipeline import get_pipeline

router = APIRouter(tags=["Календарный план"], dependencies=[require_roles(*SITE_MANAGERS)])


async def _catalog() -> tuple[Catalog | None, str | None]:
    """Справочник сервисов аналитики — для колонки «Вид работ»; нет его — вид работ просто не сохранится."""
    analytics = get_analytics()
    if not analytics.enabled:
        return None, None
    try:
        return await analytics.catalog.get(), None
    except CatalogError as exc:
        return None, f"справочник сервисов аналитики недоступен ({exc})"


async def _rules(session: Session) -> list[Rule]:
    return list(await session.scalars(select(Rule).order_by(Rule.position)))


@router.get(
    "/sites/{site_id}/plan/template",
    summary="Шаблон плана работ (Excel): списки правил и видов работ для этого объекта",
    response_class=Response,
    responses={200: {"content": {plan_import.XLSX_TYPE: {}}}},
)
async def plan_template(site_id: str, user: CurrentUser, session: Session) -> Response:
    site = await get_site(session, user, site_id)
    catalog, _ = await _catalog()
    content = plan_import.template(await _rules(session), catalog, object_type(site.kind), site.name)
    name = f"План работ — {site.name}.xlsx"
    return Response(
        content,
        media_type=plan_import.XLSX_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"plan.xlsx\"; filename*=UTF-8''{quote(name)}"},
    )


@router.post(
    "/sites/{site_id}/plan/import",
    response_model=PlanImportOut,
    summary="Загрузить план из Excel или CSV: без apply — только предпросмотр с ошибками по строкам",
)
async def import_plan(
    site_id: str,
    file: Annotated[UploadFile, File(description="Заполненный шаблон .xlsx или таблица .csv")],
    user: CurrentUser,
    session: Session,
    request: Request,
    apply: Annotated[bool, Query(description="Записать план; без него — только показать, что получится")] = False,
    replace: Annotated[bool, Query(description="Удалить прежний план объекта, а не дополнить его")] = False,
) -> PlanImportOut:
    site = await get_site(session, user, site_id)
    catalog, problem = await _catalog()
    try:
        rows = plan_import.read_table(file.filename or "", await file.read(plan_import.MAX_BYTES + 1))
        phases = plan_import.parse(rows, await _rules(session), catalog, problem, object_type(site.kind))
    except plan_import.PlanFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    existing = await session.scalar(select(func.count()).select_from(Stage).where(Stage.site_id == site_id)) or 0
    out = PlanImportOut(
        file_name=file.filename or "",
        phases=[
            PlanImportPhase(
                line=p.line,
                name=p.name,
                start=p.start,
                end=p.end,
                errors=p.errors,
                works=[
                    PlanImportWork(
                        line=w.line,
                        name=w.work or "",
                        start=w.start,
                        end=w.end,
                        rule_key=w.rule_key,
                        catalog_stage_id=w.catalog_stage_id,
                        errors=w.errors,
                        notes=w.notes,
                    )
                    for w in p.works
                ],
            )
            for p in phases
        ],
        works=sum(len(p.works) for p in phases),
        errors=sum(len(p.errors) + sum(len(w.errors) for w in p.works) for p in phases),
        existing=existing,
        applied=False,
    )
    if not apply:
        return out
    if out.errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "В файле есть ошибки — исправьте отмеченные строки")

    removed = 0
    if replace and existing:
        ids = list(await session.scalars(select(Stage.id).where(Stage.site_id == site_id)))
        # отклонения и проверки остаются в истории — просто без ссылки на удалённые этапы
        await session.execute(update(Alert).where(Alert.stage_id.in_(ids)).values(stage_id=None))
        await session.execute(update(CheckRun).where(CheckRun.stage_id.in_(ids)).values(stage_id=None))
        for level in (2, 1):  # сначала работы: они ссылаются на свои этапы
            for stage in await session.scalars(select(Stage).where(Stage.site_id == site_id, Stage.level == level)):
                await session.delete(stage)
            await session.flush()
        removed = len(ids)
    position = await session.scalar(select(func.max(Stage.position)).where(Stage.site_id == site_id)) or 0
    for phase in phases:
        parent = Stage(
            id=new_id("st"),
            site_id=site_id,
            level=1,
            name=phase.name,
            start_date=phase.start,
            end_date=phase.end,
            position=(position := position + 1),
        )
        session.add(parent)
        for work in phase.works:
            session.add(
                Stage(
                    id=new_id("st"),
                    site_id=site_id,
                    parent_id=parent.id,
                    level=2,
                    name=work.work,
                    start_date=work.start,
                    end_date=work.end,
                    rule_key=work.rule_key,
                    catalog_stage_id=work.catalog_stage_id,
                    catalog_version=catalog.version if catalog and work.catalog_stage_id is not None else None,
                    position=(position := position + 1),
                )
            )
    summary = f"Загрузил план объекта «{site.name}» из файла «{out.file_name}»: этапов — {len(phases)}, работ — {out.works}"
    audit.record(
        session, request, user, "plan.import", summary + (f"; прежний план ({removed}) удалён" if removed else ""),
        entity_type="site", entity_id=site.id, entity_name=site.name,
        details={"file": out.file_name, "phases": len(phases), "works": out.works, "removed": removed},
    )  # fmt: skip
    await session.commit()
    get_pipeline().request_check({site_id})  # текущий этап мог смениться — сверим сразу
    return out.model_copy(update={"applied": True})
