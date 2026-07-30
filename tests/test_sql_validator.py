"""Проверки модуля безопасности SQL.

Основу набора составляют шесть сценариев нарушения, разобранных при
проектировании системы. Сценарии описаны в docs/architecture.md,
раздел "Рубежи защиты".

Сценарии 4 и 5 представляют собой запросы, законные с точки зрения
статического анализа: объекты верные, синтаксис чистый, белый список пройден.
Они отсекаются на рубеже 5 по оценке стоимости выполнения, который требует
подключения к СУБД и проверяется в tests/test_db.py.
"""

import pytest

from dwh_copilot.catalog import Catalog
from dwh_copilot.sql_validator import MAX_ROWS, Accepted, Reason, Rejected, validate

ALLOWED = Catalog.load("config/views.yaml").allowed_views


def reason_of(sql: str) -> Reason | None:
    """Возвращает причину отказа либо None, если запрос допущен."""
    result = validate(sql, ALLOWED)
    return result.reason if isinstance(result, Rejected) else None


# --- Сценарий 1. Несколько инструкций в одном запросе


def test_multiple_statements_rejected():
    sql = "SELECT * FROM mart.v_sales; DROP TABLE dbo.orders"
    assert reason_of(sql) == Reason.MULTIPLE_STATEMENTS


def test_comment_obfuscation_does_not_help():
    """Приём, который обходит проверку по списку запрещённых слов."""
    sql = "SELECT * FROM mart.v_sales;/**/DROP TABLE dbo.orders"
    assert reason_of(sql) == Reason.MULTIPLE_STATEMENTS


# --- Сценарий 2. Обращение к присоединённому серверу


def test_linked_server_rejected():
    sql = "SELECT * FROM [DWH_LINK].[master].[dbo].[sysusers]"
    assert reason_of(sql) == Reason.CROSS_SERVER


# --- Сценарий 3. Системный каталог во вложенном подзапросе.
# Поиск по тексту нашёл бы только mart.v_sales и пропустил бы sys.databases.


def test_system_catalog_in_subquery_rejected():
    sql = """
        SELECT * FROM mart.v_sales
        WHERE region = (SELECT TOP 1 name FROM sys.databases)
    """
    assert reason_of(sql) == Reason.NOT_WHITELISTED


def test_forbidden_table_inside_cte_rejected():
    sql = """
        WITH leaked AS (SELECT * FROM hr.salaries)
        SELECT * FROM leaked
    """
    assert reason_of(sql) == Reason.NOT_WHITELISTED


def test_cte_alias_is_not_treated_as_table():
    """Имя выражения WITH выглядит в дереве как таблица, но объектом не является."""
    sql = """
        WITH monthly AS (
            SELECT division, SUM(revenue_net) AS total
            FROM mart.v_sales GROUP BY division
        )
        SELECT * FROM monthly
    """
    assert isinstance(validate(sql, ALLOWED), Accepted)


# --- Сценарий 5. Декартово произведение двух разрешённых витрин


def test_cross_join_rejected():
    sql = "SELECT * FROM mart.v_sales CROSS JOIN mart.v_clients"
    assert reason_of(sql) == Reason.CARTESIAN_JOIN


def test_comma_join_without_where_rejected():
    sql = "SELECT a.*, b.* FROM mart.v_sales a, mart.v_clients b"
    assert reason_of(sql) == Reason.CARTESIAN_JOIN


def test_proper_join_accepted():
    sql = """
        SELECT c.segment, SUM(s.revenue_net) AS revenue
        FROM mart.v_sales s
        JOIN mart.v_clients c ON c.client_id = s.client_id
        GROUP BY c.segment
    """
    assert isinstance(validate(sql, ALLOWED), Accepted)


# --- Сценарий 6. Попытка подмены инструкций через текст вопроса.
# Модуль не фильтрует вопрос пользователя. Он делает результат подмены
# безвредным: обращение к закрытой схеме не проходит белый список,
# а у учётной записи чтения нет прав на эту схему.


def test_injection_result_is_harmless():
    sql = "SELECT * FROM hr.salaries"
    assert reason_of(sql) == Reason.NOT_WHITELISTED


# --- Рубеж 1. Отказ по умолчанию


def test_unparseable_is_rejected_not_passed():
    """Не "не нашли запрещённого, пропускаем", а "не смогли проверить, отказ"."""
    assert reason_of("SELECT FROM WHERE ((((") == Reason.PARSE_FAILED


# --- Рубеж 2. Только чтение


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE mart.v_sales SET revenue_net = 0",
        "DELETE FROM mart.v_sales",
        "INSERT INTO mart.v_sales (region) VALUES ('X')",
        "DROP TABLE mart.v_sales",
        "EXEC xp_cmdshell 'dir'",
    ],
)
def test_write_operations_rejected(sql):
    assert reason_of(sql) is not None


def test_select_into_rejected():
    """Конструкция SELECT ... INTO создаёт таблицу, то есть выполняет запись."""
    sql = "SELECT * INTO dbo.stolen FROM mart.v_sales"
    assert reason_of(sql) == Reason.FORBIDDEN_NODE


def test_openrowset_rejected():
    sql = "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=other;', 'SELECT 1')"
    assert reason_of(sql) == Reason.FORBIDDEN_FUNCTION


# --- Рубеж 4. Ограничение числа строк


def test_row_limit_injected():
    result = validate("SELECT region, revenue_net FROM mart.v_sales", ALLOWED)
    assert isinstance(result, Accepted)
    assert str(MAX_ROWS) in result.sql


def test_smaller_user_limit_preserved():
    """Собственное ограничение пользователя до предельного не расширяется."""
    result = validate("SELECT TOP 10 region FROM mart.v_sales", ALLOWED)
    assert isinstance(result, Accepted)
    assert "10" in result.sql
    assert str(MAX_ROWS) not in result.sql


# --- Оба примера вопросов из технического задания


def test_example_sales_dynamics():
    """Покажи динамику продаж по регионам за последние три месяца."""
    sql = """
        SELECT region, DATEFROMPARTS(YEAR(report_date), MONTH(report_date), 1) AS m,
               SUM(revenue_net) AS revenue
        FROM mart.v_sales
        WHERE report_date >= '2026-04-28'
        GROUP BY region, DATEFROMPARTS(YEAR(report_date), MONTH(report_date), 1)
        ORDER BY m
    """
    result = validate(sql, ALLOWED)
    assert isinstance(result, Accepted)
    assert result.tables == frozenset({"mart.v_sales"})


def test_example_plan_fact_gap():
    """В каких подразделениях фактическая выручка ниже плана более чем на 10 процентов."""
    sql = """
        SELECT division, SUM(revenue_plan) AS plan, SUM(revenue_fact) AS fact
        FROM mart.v_sales_plan_fact
        WHERE report_month >= '2026-01-01'
        GROUP BY division
        HAVING SUM(revenue_fact) < SUM(revenue_plan) * 0.9
    """
    assert isinstance(validate(sql, ALLOWED), Accepted)
