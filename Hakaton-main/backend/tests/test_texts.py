"""Тексты предупреждений: период проверок читается однозначно, даже если он длится несколько дней."""

from datetime import UTC, datetime, timedelta

from app.services.texts import _period, camera_offline, shortage

END = datetime(2026, 9, 24, 14, 14, tzinfo=UTC)  # 17:14 по Москве


def test_period_within_a_day_is_just_times():
    assert _period(END - timedelta(hours=2), END) == "с 15:14 до 17:14"
    assert _period(END, END) == "в 17:14"


def test_period_over_several_days_names_the_days():
    assert _period(END - timedelta(days=2, minutes=-41), END) == "с 22 сентября 17:55 до 24 сентября 17:14"
    # ровно сутки: раньше выходило «в 17:14», будто это одна проверка
    assert _period(END - timedelta(days=1), END) == "с 23 сентября 17:14 до 24 сентября 17:14"


def test_offline_camera_names_the_day_of_the_last_snapshot():
    today = camera_offline(camera_name="Камера 3", zone_name="Склад", last_snapshot=END - timedelta(hours=3), now=END)
    assert "не приходит с 14:14." in today.summary
    earlier = camera_offline(camera_name="Камера 3", zone_name="Склад", last_snapshot=END - timedelta(days=2, hours=23), now=END)
    assert "не приходит с 21 сентября 18:14." in earlier.summary


def test_long_shortage_is_told_in_hours_not_checks():
    long = shortage(
        equipment="dump_truck", expected=2, observed=0, stage_name="Котлован", checks=56,
        start=END - timedelta(hours=6, minutes=36), end=END, risk="",
    )  # fmt: skip
    assert "6 часов подряд (с 10:38 до 17:14)" in long.summary and "56" not in long.summary
    short = shortage(
        equipment="dump_truck", expected=2, observed=1, stage_name="Котлован", checks=3,
        start=END - timedelta(minutes=2), end=END, risk="",
    )  # fmt: skip
    assert "только 1 — на 3 последних проверках (с 17:12 до 17:14)" in short.summary
    assert "нажмите" not in long.advice  # кнопки у каждой роли свои — их подсказывает интерфейс
