from collections import Counter
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import scope
from app.db import utcnow
from app.models import OPEN_STATUSES, Alert, Site
from app.schemas import DayCount, NamedCount, SiteCount, WeeklyReportOut
from app.security import Session, require_roles
from app.services.engine import TZ, local_day

router = APIRouter(prefix="/reports", tags=["Отчёты"])
_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@router.get("/weekly", response_model=WeeklyReportOut, summary="Сводка нарушений за 7 дней")
async def weekly(session: Session, user=require_roles("inspector", "manager", "admin")) -> WeeklyReportOut:  # noqa: ANN001
    today = local_day(utcnow())
    first = today - timedelta(days=6)
    # неработающая камера — не нарушение подрядчика, в отчёт не входит
    alerts = [
        a
        for a in await session.scalars(scope(select(Alert).where(Alert.kind != "camera_offline"), Alert.site_id, user))
        if first <= a.started_at.astimezone(TZ).date() <= today
    ]
    per_day = Counter(a.started_at.astimezone(TZ).date() for a in alerts)
    days = [first + timedelta(days=i) for i in range(7)]
    sites = list(await session.scalars(scope(select(Site), Site.id, user).order_by(Site.position)))
    return WeeklyReportOut(
        date_from=first,
        date_to=today,
        total=len(alerts),
        open=sum(a.status in OPEN_STATUSES for a in alerts),
        resolved=sum(a.status == "resolved" for a in alerts),
        false_positive=sum(a.status == "false_positive" for a in alerts),
        by_day=[DayCount(date=d, label=f"{_WEEKDAYS[d.weekday()]} {d.day}", count=per_day[d]) for d in days],
        by_site=[
            SiteCount(
                site_id=s.id,
                name=s.name,
                count=sum(a.site_id == s.id for a in alerts),
                high=sum(a.site_id == s.id and a.severity == "high" for a in alerts),
            )
            for s in sites
        ],
        by_kind=[NamedCount(key=k, count=n) for k, n in Counter(a.kind for a in alerts).most_common()],
        by_equipment=[
            NamedCount(key=k, count=n) for k, n in Counter(a.equipment_type for a in alerts if a.equipment_type).most_common()
        ],
    )
