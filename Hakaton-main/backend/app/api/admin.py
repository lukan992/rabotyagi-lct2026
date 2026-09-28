"""Администрирование: объекты, зоны, календарный план, выполнение работ, сотрудники и их пароли.

Объекты, зоны и план ведут администратор и руководитель проекта; удалить объект и управлять сотрудниками может только
администратор; сколько сделано по факту, отмечают они же (прораб — нет). Каждое действие пишется в журнал
(без паролей). Когда вход идёт через Keycloak, сотрудники, роли и пароли одновременно меняются и в Keycloak.
"""

import asyncio
import shutil

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db import utcnow
from app.keycloak_admin import KeycloakAdminError, get_keycloak_admin
from app.models import Alert, Camera, CheckRun, Rule, Site, Stage, User, Zone, new_id
from app.schemas import (
    PasswordIn,
    SiteIn,
    SiteOut,
    StageIn,
    StageOut,
    StageProgressIn,
    UserIn,
    UserOut,
    UserPatch,
    ZoneIn,
    ZoneOut,
    site_out,
    stage_out,
    user_out,
    zone_out,
)
from app.security import (
    PROGRESS_REPORTERS,
    SITE_MANAGERS,
    CurrentUser,
    Session,
    ensure_site_access,
    hash_password,
    require_roles,
)
from app.services import audit, tracker_control
from app.services.analytics.catalog import CatalogError, object_type
from app.services.analytics.runner import get_analytics
from app.services.audit import ROLE_TITLES
from app.services.engine import current_stage, local_day
from app.services.pipeline import get_pipeline
from app.services.plan import site_progress, stages_by_site
from app.services.workhours import describe as describe_hours

settings = get_settings()
router = APIRouter()
admin_only = [require_roles("admin")]
site_managers = [require_roles(*SITE_MANAGERS)]
reporters = [require_roles(*PROGRESS_REPORTERS)]


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def _invalid(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _keycloak():
    try:
        return get_keycloak_admin()
    except KeycloakAdminError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None


async def _site(session, site_id: str) -> Site:  # noqa: ANN001
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
    return site


# =====================================================================================
#  Объекты
# =====================================================================================
async def _set_foreman(session, site: Site, body: SiteIn) -> None:  # noqa: ANN001
    """Прораб объекта один: новому даём доступ к объекту, у прежних прорабов этого объекта — снимаем.
    Поля foremanId в запросе нет — прораба не трогаем; foremanId: null — снимаем прораба с объекта."""
    if "foreman_id" not in body.model_fields_set:
        return
    foreman = None
    if body.foreman_id is not None:
        foreman = await session.get(User, body.foreman_id)
        if foreman is None or foreman.role != "foreman":
            raise _invalid("Прорабом объекта можно назначить только сотрудника с ролью «Прораб»")
    keep = foreman.id if foreman else None
    for other in await session.scalars(select(User).where(User.role == "foreman", User.id != keep)):
        other.sites = [s for s in other.sites if s.id != site.id]
    if foreman and site.id not in {s.id for s in foreman.sites}:
        foreman.sites = [*foreman.sites, site]
    site.foreman_name = foreman.name if foreman else ""


def _site_fields(site: Site) -> dict:
    return {
        "name": site.name,
        "address": site.address,
        "contractor": site.contractor,
        "kind": site.kind,
        "work_hours": describe_hours(site),
        "foreman": site.foreman_name,
    }


@router.post(
    "/sites",
    response_model=SiteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=site_managers,
    tags=["Объекты"],
    summary="Создать объект",
)
async def create_site(body: SiteIn, user: CurrentUser, session: Session, request: Request) -> SiteOut:
    position = (await session.scalar(select(func.max(Site.position))) or 0) + 1
    site = Site(
        id=new_id("s"),
        name=body.name.strip(),
        address=body.address.strip(),
        contractor=body.contractor.strip(),
        kind=body.kind,
        work_from=body.work_from,
        work_to=body.work_to,
        work_days=body.work_days,
        position=position,
    )
    session.add(site)
    await session.flush()
    await _set_foreman(session, site, body)
    audit.record(
        session, request, user, "site.create", f"Создал объект «{site.name}»",
        entity_type="site", entity_id=site.id, entity_name=site.name, details=_site_fields(site),
    )  # fmt: skip
    await session.commit()
    return site_out(site, None, None)  # у нового объекта плана ещё нет


@router.patch("/sites/{site_id}", response_model=SiteOut, dependencies=site_managers, tags=["Объекты"], summary="Изменить объект")
async def update_site(site_id: str, body: SiteIn, user: CurrentUser, session: Session, request: Request) -> SiteOut:
    site = await _site(session, site_id)
    before = _site_fields(site)
    site.name, site.address, site.contractor = body.name.strip(), body.address.strip(), body.contractor.strip()
    if "kind" in body.model_fields_set:  # старая форма без поля «вид» не сбрасывает его в «другое»
        site.kind = body.kind
    for name in ("work_from", "work_to", "work_days"):  # и рабочее время — только если его прислали
        if name in body.model_fields_set:
            setattr(site, name, getattr(body, name))
    await _set_foreman(session, site, body)
    if changed := audit.changes(before, _site_fields(site)):
        audit.record(
            session, request, user, "site.update", f"Изменил объект «{site.name}»",
            entity_type="site", entity_id=site.id, entity_name=site.name, details=changed,
        )  # fmt: skip
    await session.commit()
    today = local_day(utcnow())
    stage = await current_stage(session, site.id, today)
    plans = await stages_by_site(session, [site.id])
    return site_out(site, stage.id if stage else None, site_progress(plans[site.id], today))


@router.delete(
    "/sites/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=admin_only,
    tags=["Объекты"],
    summary="Удалить объект целиком",
)
async def delete_site(site_id: str, user: CurrentUser, session: Session, request: Request) -> None:
    site = await _site(session, site_id)
    camera_ids = list(await session.scalars(select(Camera.id).where(Camera.site_id == site_id)))
    alerts = await session.scalar(select(func.count()).select_from(Alert).where(Alert.site_id == site_id))
    audit.record(
        session, request, user, "site.delete",
        f"Удалил объект «{site.name}» вместе с камерами ({len(camera_ids)}), планом и отклонениями ({alerts})",
        entity_type="site", entity_id=site.id, entity_name=site.name, details=_site_fields(site),
    )  # fmt: skip
    await session.delete(site)  # зоны, камеры, план, кадры, проверки и отклонения удаляет база (ON DELETE CASCADE)
    await session.commit()
    for camera_id in camera_ids:
        await tracker_control.notify_camera("stop", camera_id)
    for camera_id in camera_ids:  # кадры на диске больше ни на что не ссылаются
        await asyncio.to_thread(shutil.rmtree, settings.frames_dir / camera_id, True)
    await get_pipeline().sync()  # потоки камер объекта убираются из шлюза


# =====================================================================================
#  Зоны
# =====================================================================================
ZONE_KINDS = {"work": "рабочая", "gate": "въезд", "storage": "склад"}


@router.post(
    "/sites/{site_id}/zones",
    response_model=ZoneOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=site_managers,
    tags=["Объекты"],
    summary="Добавить зону",
)
async def create_zone(site_id: str, body: ZoneIn, user: CurrentUser, session: Session, request: Request) -> ZoneOut:
    site = await _site(session, site_id)
    position = (await session.scalar(select(func.max(Zone.position)).where(Zone.site_id == site_id)) or 0) + 1
    zone = Zone(id=new_id("z"), site_id=site_id, name=body.name.strip(), kind=body.kind, position=position)
    session.add(zone)
    audit.record(
        session, request, user, "zone.create", f"Добавил зону «{zone.name}» ({ZONE_KINDS[zone.kind]}) на объекте «{site.name}»",
        entity_type="zone", entity_id=zone.id, entity_name=zone.name,
    )  # fmt: skip
    await session.commit()
    return zone_out(zone)


@router.patch("/zones/{zone_id}", response_model=ZoneOut, dependencies=site_managers, tags=["Объекты"], summary="Изменить зону")
async def update_zone(zone_id: str, body: ZoneIn, user: CurrentUser, session: Session, request: Request) -> ZoneOut:
    zone = await session.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Зона не найдена")
    before = {"name": zone.name, "kind": zone.kind}
    zone.name, zone.kind = body.name.strip(), body.kind
    if changed := audit.changes(before, {"name": zone.name, "kind": zone.kind}):
        audit.record(
            session, request, user, "zone.update", f"Изменил зону «{zone.name}»",
            entity_type="zone", entity_id=zone.id, entity_name=zone.name, details=changed,
        )  # fmt: skip
    await session.commit()
    return zone_out(zone)


@router.delete(
    "/zones/{zone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=site_managers,
    tags=["Объекты"],
    summary="Удалить пустую зону",
)
async def delete_zone(zone_id: str, user: CurrentUser, session: Session, request: Request) -> None:
    zone = await session.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Зона не найдена")
    used = await session.scalar(
        select(func.count()).select_from(Camera).where(Camera.zone_id == zone_id)
    ) or await session.scalar(select(func.count()).select_from(Alert).where(Alert.zone_id == zone_id))
    if used:
        raise _conflict("В зоне есть камеры или по ней были отклонения — перенесите камеры в другую зону")
    audit.record(
        session,
        request,
        user,
        "zone.delete",
        f"Удалил зону «{zone.name}»",
        entity_type="zone",
        entity_id=zone.id,
        entity_name=zone.name,
    )
    await session.delete(zone)
    await session.commit()


# =====================================================================================
#  Календарный план
# =====================================================================================
async def _validate_stage(session, site_id: str, body: StageIn, stage_id: str | None = None) -> None:  # noqa: ANN001
    if body.end < body.start:
        raise _invalid("Дата окончания раньше даты начала")
    if body.level == 2:
        parent = await session.get(Stage, body.parent_id) if body.parent_id else None
        if parent is None or parent.site_id != site_id or parent.level != 1 or parent.id == stage_id:
            raise _invalid("Работы должны входить в укрупнённый этап этого объекта")
    elif body.parent_id:
        raise _invalid("Укрупнённый этап не входит в другой этап")
    if body.rule_key and await session.get(Rule, body.rule_key) is None:
        raise _invalid("Нет такого правила «этап → техника»")


def _stage_fields(stage: Stage) -> dict:
    return {
        "name": stage.name,
        "start": stage.start_date.isoformat(),
        "end": stage.end_date.isoformat(),
        "rule": stage.rule_key,
        "fact": stage.fact_progress,
        "catalog": stage.catalog_stage_id,
    }


async def _set_catalog(stage: Stage, body: StageIn, site: Site) -> None:
    """Вид работ по справочнику сервисов аналитики. Новый — проверяется по справочнику и запоминается с его версией;
    прежний при сохранении работы перепроверяется, если справочник доступен (так он «переезжает» на новую версию),
    но недоступный справочник не мешает поменять даты."""
    if "catalog_stage_id" not in body.model_fields_set:
        return
    value = body.catalog_stage_id
    if value is None:
        stage.catalog_stage_id = stage.catalog_version = None
        return
    if stage.level != 2:
        raise _invalid("Вид работ по справочнику выбирают у работ, а не у укрупнённого этапа")
    changed = value != stage.catalog_stage_id
    try:
        catalog = await get_analytics().catalog.get()
    except CatalogError as exc:
        if changed:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Справочник сервисов аналитики недоступен — вид работ сейчас не выбрать: {exc}",
            ) from None
        return
    if problem := catalog.step_problem(value, object_type(site.kind)):
        if changed:
            raise _invalid(f"Этот вид работ не подходит: {problem}")
        return
    stage.catalog_stage_id, stage.catalog_version = value, catalog.version


def _set_fact(stage: Stage, fact: int | None) -> None:
    if fact is not None and fact != stage.fact_progress:
        stage.fact_progress, stage.fact_updated_at = fact, utcnow()


@router.post(
    "/sites/{site_id}/stages",
    response_model=StageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=site_managers,
    tags=["Календарный план"],
    summary="Добавить этап",
)
async def create_stage(site_id: str, body: StageIn, user: CurrentUser, session: Session, request: Request) -> StageOut:
    site = await _site(session, site_id)
    await _validate_stage(session, site_id, body)
    position = (await session.scalar(select(func.max(Stage.position)).where(Stage.site_id == site_id)) or 0) + 1
    stage = Stage(
        id=new_id("st"),
        site_id=site_id,
        parent_id=body.parent_id if body.level == 2 else None,
        level=body.level,
        name=body.name.strip(),
        start_date=body.start,
        end_date=body.end,
        rule_key=body.rule_key if body.level == 2 else None,
        position=position,
        fact_progress=0,
    )
    _set_fact(stage, body.fact_progress)
    await _set_catalog(stage, body, site)
    session.add(stage)
    audit.record(
        session, request, user, "stage.create", f"Добавил в план объекта «{site.name}» этап «{stage.name}»",
        entity_type="stage", entity_id=stage.id, entity_name=stage.name, details=_stage_fields(stage),
    )  # fmt: skip
    await session.commit()
    get_pipeline().request_check({site_id})  # этап мог стать текущим — сверим сразу
    return stage_out(stage, local_day(utcnow()))


@router.patch(
    "/stages/{stage_id}", response_model=StageOut, dependencies=site_managers, tags=["Календарный план"], summary="Изменить этап"
)
async def update_stage(stage_id: str, body: StageIn, user: CurrentUser, session: Session, request: Request) -> StageOut:
    stage = await session.get(Stage, stage_id)
    if stage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этап не найден")
    if body.level != stage.level:
        raise _invalid("Уровень этапа менять нельзя — удалите этап и создайте заново")
    await _validate_stage(session, stage.site_id, body, stage_id)
    before = _stage_fields(stage)
    stage.name, stage.start_date, stage.end_date = body.name.strip(), body.start, body.end
    if stage.level == 2:
        stage.parent_id, stage.rule_key = body.parent_id, body.rule_key
    _set_fact(stage, body.fact_progress)
    await _set_catalog(stage, body, await session.get(Site, stage.site_id))
    if changed := audit.changes(before, _stage_fields(stage)):
        audit.record(
            session, request, user, "stage.update", f"Изменил этап «{stage.name}»",
            entity_type="stage", entity_id=stage.id, entity_name=stage.name, details=changed,
        )  # fmt: skip
    await session.commit()
    get_pipeline().request_check({stage.site_id})
    return stage_out(stage, local_day(utcnow()))


@router.delete(
    "/stages/{stage_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=site_managers,
    tags=["Календарный план"],
    summary="Удалить этап",
)
async def delete_stage(stage_id: str, user: CurrentUser, session: Session, request: Request) -> None:
    stage = await session.get(Stage, stage_id)
    if stage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этап не найден")
    ids = [stage.id, *await session.scalars(select(Stage.id).where(Stage.parent_id == stage.id))]  # этап вместе с его работами
    # отклонения и проверки остаются в истории — просто без ссылки на удалённый этап
    await session.execute(update(Alert).where(Alert.stage_id.in_(ids)).values(stage_id=None))
    await session.execute(update(CheckRun).where(CheckRun.stage_id.in_(ids)).values(stage_id=None))
    for row in await session.scalars(select(Stage).where(Stage.id.in_(ids[1:]))):
        await session.delete(row)
    await session.flush()
    audit.record(
        session, request, user, "stage.delete", f"Удалил из плана этап «{stage.name}»" + (f" и его работы ({len(ids) - 1})" if len(ids) > 1 else ""),
        entity_type="stage", entity_id=stage.id, entity_name=stage.name, details=_stage_fields(stage),
    )  # fmt: skip
    await session.delete(stage)
    await session.commit()
    get_pipeline().request_check({stage.site_id})


@router.patch(
    "/stages/{stage_id}/progress",
    response_model=StageOut,
    dependencies=reporters,
    tags=["Календарный план"],
    summary="Отметить, сколько работ сделано по факту",
)
async def set_stage_progress(
    stage_id: str, body: StageProgressIn, user: CurrentUser, session: Session, request: Request
) -> StageOut:
    stage = await session.get(Stage, stage_id)
    if stage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этап не найден")
    ensure_site_access(user, stage.site_id)
    before = stage.fact_progress
    _set_fact(stage, body.fact_progress)
    if stage.fact_progress != before:
        site = await session.get(Site, stage.site_id)
        audit.record(
            session, request, user, "stage.progress",
            f"Отметил выполнение «{stage.name}» ({site.name if site else stage.site_id}): {stage.fact_progress}% (было {before}%)",
            entity_type="stage", entity_id=stage.id, entity_name=stage.name, details={"fact": [before, stage.fact_progress]},
        )  # fmt: skip
    await session.commit()
    return stage_out(stage, local_day(utcnow()))


# =====================================================================================
#  Сотрудники и пароли
# =====================================================================================
async def _sites(session, site_ids: list[str]) -> list[Site]:  # noqa: ANN001
    sites = list(await session.scalars(select(Site).where(Site.id.in_(site_ids)))) if site_ids else []
    if len(sites) != len(set(site_ids)):
        raise _invalid("Среди объектов есть несуществующий")
    return sites


async def _active_admins(session) -> int:  # noqa: ANN001
    return await session.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active)) or 0


async def _get_user(session, user_id: str) -> User:  # noqa: ANN001
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден")
    return target


def _user_fields(u: User) -> dict:
    return {"name": u.name, "role": u.role, "phone": u.phone, "sites": sorted(s.id for s in u.sites), "active": u.is_active}


@router.get("/users", response_model=list[UserOut], dependencies=site_managers, tags=["Сотрудники"], summary="Сотрудники")
async def list_users(session: Session) -> list[UserOut]:
    return [user_out(u) for u in await session.scalars(select(User).order_by(User.role, User.name))]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=admin_only,
    tags=["Сотрудники"],
    summary="Завести сотрудника",
)
async def create_user(body: UserIn, user: CurrentUser, session: Session, request: Request) -> UserOut:
    login = body.login.strip().lower()
    if await session.scalar(select(User.id).where(func.lower(User.login) == login)):
        raise _conflict("Сотрудник с таким логином уже есть")
    sites = await _sites(session, body.site_ids)
    if keycloak := _keycloak():
        try:
            await keycloak.create(username=login, name=body.name.strip(), enabled=True, password=body.password, role=body.role)
        except KeycloakAdminError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    new = User(
        id=new_id("u"),
        login=login,
        name=body.name.strip(),
        role=body.role,
        phone=body.phone.strip(),
        password_hash=await asyncio.to_thread(hash_password, body.password),
        sites=sites,
    )
    session.add(new)
    audit.record(
        session, request, user, "user.create", f"Завёл сотрудника «{new.name}» ({login}), роль — {ROLE_TITLES[new.role]}",
        entity_type="user", entity_id=new.id, entity_name=new.name, details=_user_fields(new),
    )  # fmt: skip
    await session.commit()
    return user_out(new)


@router.patch(
    "/users/{user_id}", response_model=UserOut, dependencies=admin_only, tags=["Сотрудники"], summary="Изменить сотрудника"
)
async def update_user(user_id: str, body: UserPatch, user: CurrentUser, session: Session, request: Request) -> UserOut:
    target = await _get_user(session, user_id)
    before = _user_fields(target)
    if target.id == user.id and ((body.role and body.role != "admin") or body.is_active is False):
        raise _conflict("Нельзя снять права администратора или отключить самого себя")
    losing_admin = (
        target.role == "admin" and target.is_active and ((body.role and body.role != "admin") or body.is_active is False)
    )
    if losing_admin and await _active_admins(session) <= 1:
        raise _conflict("Это последний администратор — сначала назначьте другого")
    if body.name is not None:
        target.name = body.name.strip()
    if body.role is not None:
        target.role = body.role
    if body.phone is not None:
        target.phone = body.phone.strip()
    if body.site_ids is not None:
        target.sites = await _sites(session, body.site_ids)
    if body.is_active is not None:
        target.is_active = body.is_active
    changed = audit.changes(before, _user_fields(target))
    if changed and (keycloak := _keycloak()):
        try:
            kc_id = await keycloak.ensure(username=target.login, name=target.name, enabled=target.is_active, role=target.role)
            await keycloak.update(kc_id, name=target.name, enabled=target.is_active)
            if "role" in changed:
                await keycloak.set_role(kc_id, target.role)
        except KeycloakAdminError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    if changed:
        verb = (
            "Отключил"
            if changed.keys() == {"active"} and not target.is_active
            else "Включил"
            if changed.keys() == {"active"}
            else "Изменил"
        )
        audit.record(
            session, request, user, "user.update", f"{verb} сотрудника «{target.name}» ({target.login})",
            entity_type="user", entity_id=target.id, entity_name=target.name, details=changed,
        )  # fmt: skip
    await session.commit()
    return user_out(target)


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=admin_only,
    tags=["Сотрудники"],
    summary="Задать пароль",
)
async def set_password(user_id: str, body: PasswordIn, user: CurrentUser, session: Session, request: Request) -> None:
    target = await _get_user(session, user_id)
    if keycloak := _keycloak():
        try:  # в Keycloak сотрудника могло не быть (заведён только у нас) — тогда заводим сразу с этим паролем
            kc_id = await keycloak.ensure(
                username=target.login, name=target.name, enabled=target.is_active, role=target.role, password=body.password
            )
            await keycloak.set_password(kc_id, body.password)
        except KeycloakAdminError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    target.password_hash = await asyncio.to_thread(hash_password, body.password)
    audit.record(
        session, request, user, "user.password", f"Сменил пароль сотрудника «{target.name}» ({target.login})",
        entity_type="user", entity_id=target.id, entity_name=target.name,
    )  # fmt: skip
    await session.commit()


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=admin_only,
    tags=["Сотрудники"],
    summary="Удалить сотрудника",
)
async def delete_user(user_id: str, user: CurrentUser, session: Session, request: Request) -> None:
    target = await _get_user(session, user_id)
    if target.id == user.id:
        raise _conflict("Нельзя удалить самого себя")
    if target.role == "admin" and target.is_active and await _active_admins(session) <= 1:
        raise _conflict("Это последний администратор — сначала назначьте другого")
    if keycloak := _keycloak():
        try:
            if found := await keycloak.find(target.login):
                await keycloak.delete(found["id"])
        except KeycloakAdminError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    # объект, где он значился прорабом, остаётся — просто без прораба
    await session.execute(
        update(Site).where(Site.foreman_name == target.name, Site.id.in_([s.id for s in target.sites])).values(foreman_name="")
    )
    audit.record(
        session, request, user, "user.delete", f"Удалил сотрудника «{target.name}» ({target.login}), роль — {ROLE_TITLES[target.role]}",
        entity_type="user", entity_id=target.id, entity_name=target.name, details=_user_fields(target),
    )  # fmt: skip
    await session.delete(target)
    await session.commit()
