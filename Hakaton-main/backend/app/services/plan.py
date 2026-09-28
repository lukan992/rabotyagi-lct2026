"""Выполнение плана объекта целиком: сколько должно быть сделано по графику и сколько сделано по факту."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stage


def _days(stage: Stage) -> int:
    return (stage.end_date - stage.start_date).days + 1


def site_progress(stages: list[Stage], today: date) -> tuple[int, int] | None:
    """(по графику, по факту), %: работы плана с весом по длительности, у этапа без работ — он сам. None — плана нет."""
    with_works = {s.parent_id for s in stages if s.level == 2}
    units = [s for s in stages if s.level == 2 or s.id not in with_works]
    total = sum(_days(s) for s in units)
    if not total:
        return None
    plan = round(sum(s.plan_progress_on(today) * _days(s) for s in units) / total)
    fact = round(sum(s.fact_progress * _days(s) for s in units) / total)
    return plan, fact


async def stages_by_site(session: AsyncSession, site_ids: list[str]) -> dict[str, list[Stage]]:
    grouped: dict[str, list[Stage]] = {site_id: [] for site_id in site_ids}
    for stage in await session.scalars(select(Stage).where(Stage.site_id.in_(site_ids))):
        grouped.setdefault(stage.site_id, []).append(stage)
    return grouped
