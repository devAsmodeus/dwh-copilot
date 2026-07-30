"""Выбор вида графика по составу результата запроса.

Вид графика определяется правилом на основе типов колонок, а не обращением
к языковой модели. Решение принято сознательно: задача решается двумя десятками
строк детерминированного кода, тогда как обращение к модели добавило бы к времени
ответа полторы секунды и ещё одну точку отказа. Модель может вернуть ссылку
на несуществующую колонку или неверный формат, и это придётся обрабатывать.

Правило:
    дата и одно число                         линейный график
    одна категория и одно число               столбчатая диаграмма
    категория, дата и число                   линейный график с несколькими рядами
    одно число без разрезов                   отдельное значение
    во всех прочих случаях                    только таблица
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

# Порог числа категорий, выше которого столбчатая диаграмма теряет читаемость.
MAX_CATEGORIES = 30


class ChartKind(StrEnum):
    """Вид представления результата."""

    LINE = "line"
    BAR = "bar"
    VALUE = "value"
    NONE = "none"


@dataclass(frozen=True)
class ChartSpec:
    """Описание графика.

    Атрибуты:
        kind: вид представления.
        x: колонка горизонтальной оси.
        y: колонка значений.
        series: колонка разбиения на ряды.
        reason: пояснение выбора. Показывается в интерфейсе и пишется в журнал,
            чтобы поведение системы оставалось объяснимым.
    """

    kind: ChartKind
    x: str | None = None
    y: str | None = None
    series: str | None = None
    reason: str = ""


def choose_chart(frame: pd.DataFrame) -> ChartSpec:
    """Подбирает вид графика под таблицу результата."""
    if frame.empty:
        return ChartSpec(ChartKind.NONE, reason="Запрос не вернул строк")

    numeric = [name for name in frame.columns if pd.api.types.is_numeric_dtype(frame[name])]
    temporal = [name for name in frame.columns if _is_temporal(frame[name])]
    categorical = [name for name in frame.columns if name not in numeric and name not in temporal]

    if not numeric:
        return ChartSpec(ChartKind.NONE, reason="В результате нет числовых колонок")

    if len(frame) == 1 and len(frame.columns) == 1:
        return ChartSpec(ChartKind.VALUE, y=numeric[0], reason="Результат содержит одно значение")

    if temporal and len(numeric) >= 1:
        series = categorical[0] if categorical else None
        reason = (
            "Есть колонка с датой, показана динамика"
            if series is None
            else "Есть дата и разрез по категории, показана динамика по рядам"
        )
        return ChartSpec(ChartKind.LINE, x=temporal[0], y=numeric[0], series=series, reason=reason)

    if categorical and len(frame) <= MAX_CATEGORIES:
        return ChartSpec(
            ChartKind.BAR,
            x=categorical[0],
            y=numeric[0],
            reason="Сравнение значений по категориям",
        )

    if categorical:
        return ChartSpec(
            ChartKind.NONE,
            reason=(
                f"Категорий больше {MAX_CATEGORIES}, диаграмма нечитаема. Показана только таблица"
            ),
        )

    return ChartSpec(ChartKind.NONE, reason="Состав колонок не подходит под график")


def _is_temporal(column: pd.Series) -> bool:
    """Определяет, содержит ли колонка даты.

    Проверяется как тип данных, так и текстовое представление даты: драйверы
    СУБД возвращают тип date по-разному в зависимости от версии.
    """
    if pd.api.types.is_datetime64_any_dtype(column):
        return True
    if column.dtype == object and len(column) > 0:
        sample = column.dropna()
        if sample.empty:
            return False
        try:
            pd.to_datetime(sample.head(5), format="%Y-%m-%d")
        except (ValueError, TypeError):
            return False
        return True
    return False
