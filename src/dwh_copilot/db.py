"""Выполнение запросов к хранилищу на Microsoft SQL Server.

Модуль реализует рубеж 5 защиты, то есть оценку стоимости запроса до его
выполнения, и рубеж 6, то есть ограничения на стороне СУБД.

Рубеж 5 закрывает случай, который не ловится никаким разбором текста запроса.
Запрос вида "SELECT region, SUM(revenue_net) FROM mart.v_sales GROUP BY region"
и запрос, соединяющий две разрешённые витрины по неудачному условию, внешне
одинаково законны: объекты верные, синтаксис чистый, белый список пройден.
Различие проявляется только в стоимости выполнения.

Microsoft SQL Server умеет возвращать оценочный план выполнения, не выполняя
сам запрос. Инструкция SET SHOWPLAN_XML ON переводит соединение в режим, когда
вместо результата возвращается план в формате XML. Из плана берутся два
показателя: оценочное число строк и оценочная стоимость поддерева. Превышение
любого из порогов приводит к отказу, причина которого передаётся модели
для исправления запроса.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

# Извлечение показателей из плана выполнения. Полноценный разбор XML здесь
# избыточен: нужны два числа из корневого элемента, а формат плана устойчив
# между версиями Microsoft SQL Server.
_ROWS_RE = re.compile(r'StatementEstRows="([\d.eE+-]+)"')
_COST_RE = re.compile(r'StatementSubTreeCost="([\d.eE+-]+)"')


@dataclass(frozen=True)
class PlanEstimate:
    """Оценка стоимости запроса, полученная до его выполнения."""

    estimated_rows: float
    estimated_cost: float


@dataclass(frozen=True)
class TooExpensive:
    """Запрос отклонён по оценке стоимости."""

    message: str
    estimate: PlanEstimate


@dataclass(frozen=True)
class QueryResult:
    """Результат выполнения запроса."""

    frame: pd.DataFrame
    elapsed_seconds: float
    estimate: PlanEstimate | None = None

    @property
    def row_count(self) -> int:
        return len(self.frame)


class QueryFailed(Exception):
    """Ошибка выполнения запроса на стороне СУБД.

    Текст ошибки передаётся языковой модели для исправления запроса.
    Пользователю показывается обобщённая формулировка и код обращения:
    сообщение вида "Invalid object name" подтверждает наличие или отсутствие
    объекта в хранилище, а серия таких вопросов раскрывает структуру закрытых
    схем, права на которые не выданы.
    """


class Database(Protocol):
    """Интерфейс доступа к хранилищу."""

    def estimate(self, sql: str) -> PlanEstimate:
        """Возвращает оценку стоимости без выполнения запроса."""
        ...

    def execute(self, sql: str) -> QueryResult:
        """Выполняет запрос и возвращает результат."""
        ...


class MsSqlDatabase:
    """Подключение к Microsoft SQL Server через драйвер ODBC.

    Соединение открывается под учётной записью, у которой отозваны права
    на базовые таблицы и выданы права чтения только на витрины схемы mart.
    Права описаны в sql/03_security.sql и являются последним рубежом защиты:
    ошибка в коде проверки запросов не даёт доступа к закрытым данным.
    """

    def __init__(
        self,
        connection_string: str,
        query_timeout: int = 30,
        max_estimated_rows: float = 1_000_000,
        max_estimated_cost: float = 50.0,
    ) -> None:
        self._connection_string = connection_string
        self._query_timeout = query_timeout
        self._max_rows = max_estimated_rows
        self._max_cost = max_estimated_cost

    def _connect(self) -> Any:
        import pyodbc

        connection = pyodbc.connect(self._connection_string, timeout=self._query_timeout)
        connection.timeout = self._query_timeout
        return connection

    def estimate(self, sql: str) -> PlanEstimate:
        """Получает оценочный план выполнения, не выполняя запрос."""
        with self._connect() as connection:
            cursor = connection.cursor()
            cursor.execute("SET SHOWPLAN_XML ON")
            try:
                cursor.execute(sql)
                rows = cursor.fetchall()
            except Exception as error:
                raise QueryFailed(str(error)) from error
            finally:
                cursor.execute("SET SHOWPLAN_XML OFF")

        plan = "".join(str(row[0]) for row in rows if row and row[0])
        return parse_plan(plan)

    def check_cost(self, sql: str) -> TooExpensive | PlanEstimate:
        """Проверяет запрос по порогам стоимости.

        Возвращает оценку, если запрос допустим, либо описание отказа.
        Текст отказа сформулирован как указание к действию, поскольку
        передаётся модели для исправления запроса.
        """
        estimate = self.estimate(sql)

        if estimate.estimated_rows > self._max_rows:
            return TooExpensive(
                message=(
                    f"Запрос вернёт около {estimate.estimated_rows:,.0f} строк, "
                    f"предел составляет {self._max_rows:,.0f}. Добавьте агрегацию "
                    "или сузьте период."
                ).replace(",", " "),
                estimate=estimate,
            )

        if estimate.estimated_cost > self._max_cost:
            return TooExpensive(
                message=(
                    f"Оценочная стоимость запроса {estimate.estimated_cost:.1f} "
                    f"превышает предел {self._max_cost:.1f}. Сузьте период "
                    "или уберите лишние соединения таблиц."
                ),
                estimate=estimate,
            )

        return estimate

    def execute(self, sql: str) -> QueryResult:
        """Выполняет запрос и возвращает результат в виде таблицы."""
        import time

        started = time.perf_counter()
        try:
            with self._connect() as connection:
                frame = pd.read_sql(sql, connection)
        except Exception as error:
            raise QueryFailed(str(error)) from error

        return QueryResult(frame=frame, elapsed_seconds=time.perf_counter() - started)


def parse_plan(plan_xml: str) -> PlanEstimate:
    """Извлекает оценочные показатели из плана выполнения.

    При отсутствии показателей в плане возвращаются нули. Такой исход
    не должен приводить к пропуску запроса: порядок проверок построен так,
    что отсутствие оценки означает отсутствие оснований для отказа именно
    на этом рубеже, тогда как рубежи с первого по четвёртый уже пройдены,
    а ограничение времени выполнения на стороне СУБД остаётся в силе.
    """
    rows_match = _ROWS_RE.search(plan_xml)
    cost_match = _COST_RE.search(plan_xml)
    return PlanEstimate(
        estimated_rows=float(rows_match.group(1)) if rows_match else 0.0,
        estimated_cost=float(cost_match.group(1)) if cost_match else 0.0,
    )


class InMemoryDatabase:
    """Заглушка хранилища для проверок.

    Возвращает заранее заданные таблицы по тексту запроса. Позволяет проверять
    конвейер обработки без запуска Microsoft SQL Server.
    """

    def __init__(
        self,
        results: dict[str, pd.DataFrame] | None = None,
        default: pd.DataFrame | None = None,
        estimate_value: PlanEstimate | None = None,
        error: str | None = None,
    ) -> None:
        self._results = results or {}
        self._default = default if default is not None else pd.DataFrame()
        self._estimate = estimate_value or PlanEstimate(100.0, 1.0)
        self._error = error
        self.executed: list[str] = []

    def estimate(self, sql: str) -> PlanEstimate:
        return self._estimate

    def check_cost(self, sql: str) -> TooExpensive | PlanEstimate:
        return self._estimate

    def execute(self, sql: str) -> QueryResult:
        self.executed.append(sql)
        if self._error:
            raise QueryFailed(self._error)
        frame = self._results.get(sql.strip(), self._default)
        return QueryResult(frame=frame, elapsed_seconds=0.0, estimate=self._estimate)
