"""Проверки конвейера обработки вопроса.

Проверяется поведение системы целиком, от вопроса до ответа, на заглушках
сервера вывода модели и базы данных. Запуск модели и Microsoft SQL Server
не требуется, поэтому проверки выполняются в среде непрерывной интеграции.
"""

import pandas as pd
import pytest

from dwh_copilot.catalog import Catalog
from dwh_copilot.charts import ChartKind
from dwh_copilot.db import InMemoryDatabase
from dwh_copilot.examples import ExampleBank
from dwh_copilot.llm import ScriptedClient
from dwh_copilot.pipeline import Pipeline

VALID_SQL = """
SELECT region, SUM(revenue_net) AS revenue
FROM mart.v_sales
WHERE report_date >= '2026-06-01'
GROUP BY region
"""

RESULT = pd.DataFrame(
    {"region": ["Москва", "Урал", "Сибирь"], "revenue": [120.0, 80.0, 45.0]}
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load("config/views.yaml")


@pytest.fixture(scope="module")
def examples() -> ExampleBank:
    return ExampleBank.load("config/examples.yaml")


def make_pipeline(catalog, examples, responses, database=None) -> Pipeline:
    return Pipeline(
        catalog=catalog,
        examples=examples,
        llm=ScriptedClient(responses),
        database=database or InMemoryDatabase(default=RESULT),
        data_as_of="2026-07-28",
    )


def test_successful_answer(catalog, examples):
    """Основной сценарий: запрос сформирован верно с первой попытки."""
    pipeline = make_pipeline(
        catalog, examples, [VALID_SQL, "Наибольшая выручка получена в Москве."]
    )
    answer = pipeline.ask("Покажи выручку по регионам за июнь")

    assert answer.ok
    assert not answer.refused
    assert answer.retry_count == 0
    assert len(answer.frame) == 3
    assert answer.summary.startswith("Наибольшая")
    assert answer.trace_id


def test_chart_is_chosen_by_rule(catalog, examples):
    """Вид графика определяется составом колонок, а не моделью."""
    pipeline = make_pipeline(catalog, examples, [VALID_SQL, "Вывод."])
    answer = pipeline.ask("Покажи выручку по регионам за июнь")

    assert answer.chart is not None
    assert answer.chart.kind is ChartKind.BAR
    assert answer.chart.x == "region"
    assert answer.chart.y == "revenue"


def test_row_limit_is_added_to_executed_sql(catalog, examples):
    """В выполненный запрос добавлено принудительное ограничение строк."""
    pipeline = make_pipeline(catalog, examples, [VALID_SQL, "Вывод."])
    answer = pipeline.ask("Покажи выручку по регионам за июнь")

    assert "TOP" in answer.sql.upper()


def test_repairs_query_after_rejection(catalog, examples):
    """Отказ проверки возвращается модели, и она исправляет запрос."""
    forbidden = "SELECT * FROM hr.salaries"
    pipeline = make_pipeline(
        catalog, examples, [forbidden, VALID_SQL, "Наибольшая выручка в Москве."]
    )
    answer = pipeline.ask("Покажи выручку по регионам за июнь")

    assert answer.ok
    assert answer.retry_count == 1
    assert answer.attempts[0].outcome == "not_whitelisted"
    assert answer.attempts[1].outcome == "ok"


def test_repair_prompt_contains_reason(catalog, examples):
    """Модели передаётся причина отказа, а не только факт отказа."""
    client = ScriptedClient(["SELECT * FROM hr.salaries", VALID_SQL, "Вывод."])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=InMemoryDatabase(default=RESULT),
        data_as_of="2026-07-28",
    )
    pipeline.ask("Покажи выручку по регионам за июнь")

    _, messages, _ = client.calls[1]
    repair_text = messages[-1][1]
    assert "hr.salaries" in repair_text
    assert "недоступен" in repair_text


def test_gives_up_after_attempt_limit(catalog, examples):
    """Число попыток исправления ограничено, иначе время ответа не ограничено."""
    pipeline = make_pipeline(
        catalog,
        examples,
        ["SELECT * FROM hr.salaries"] * 3,
    )
    answer = pipeline.ask("Покажи зарплаты")

    assert not answer.ok
    assert len(answer.attempts) == 3
    # Пользователю не показывается имя объекта из сообщения об ошибке.
    assert "hr.salaries" not in answer.message


def test_refusal_is_a_valid_outcome(catalog, examples):
    """Отказ считается успешной обработкой, а не ошибкой."""
    pipeline = make_pipeline(
        catalog,
        examples,
        ["NO_ANSWER: данных по себестоимости нет в доступных витринах"],
    )
    answer = pipeline.ask("Какая себестоимость по группам товаров?")

    assert answer.ok
    assert answer.refused
    assert "себестоимости" in answer.message
    assert answer.frame.empty


def test_database_error_is_returned_to_model(catalog, examples):
    """Ошибка СУБД передаётся модели для исправления, а не пользователю."""
    database = InMemoryDatabase(
        default=RESULT, error="Invalid column name 'revenue_total'"
    )
    client = ScriptedClient([VALID_SQL, VALID_SQL, VALID_SQL])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=database,
        data_as_of="2026-07-28",
    )
    answer = pipeline.ask("Покажи выручку по регионам")

    assert not answer.ok
    assert all(attempt.outcome == "db_error" for attempt in answer.attempts)
    _, messages, _ = client.calls[1]
    assert "Invalid column name" in messages[-1][1]
    # Текст ошибки СУБД раскрывает структуру хранилища и наружу не уходит.
    assert "Invalid column name" not in answer.message


def test_sql_generation_is_deterministic(catalog, examples):
    """Формирование запроса выполняется без случайности.

    Это обязательное условие воспроизводимости прогона набора вопросов:
    иначе повторный прогон на том же наборе даёт другой результат.
    """
    client = ScriptedClient([VALID_SQL, "Вывод."])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=InMemoryDatabase(default=RESULT),
        data_as_of="2026-07-28",
    )
    pipeline.ask("Покажи выручку по регионам")

    _, _, sql_temperature = client.calls[0]
    assert sql_temperature == 0.0


def test_data_as_of_date_is_passed_to_model(catalog, examples):
    """Дата актуальности данных подаётся в модель.

    Без неё период "последние три месяца" отсчитывается от сегодняшней даты,
    и ответ содержит пустой хвост за ещё не загруженные дни.
    """
    client = ScriptedClient([VALID_SQL, "Вывод."])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=InMemoryDatabase(default=RESULT),
        data_as_of="2026-07-28",
    )
    pipeline.ask("Покажи выручку за последние три месяца")

    system_prompt, _, _ = client.calls[0]
    assert "2026-07-28" in system_prompt


def test_system_prompt_is_stable_between_questions(catalog, examples):
    """Постоянная часть подсказки не зависит от вопроса.

    От этого зависит работа кэширования префикса на сервере вывода: кэш
    применяется только к совпадающему началу текста. Попадание вопроса
    в постоянную часть обнулило бы экономию на обработке подсказки.
    """
    client = ScriptedClient([VALID_SQL, "Вывод.", VALID_SQL, "Вывод."])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=InMemoryDatabase(default=RESULT),
        data_as_of="2026-07-28",
    )
    pipeline.ask("Покажи выручку по регионам")
    pipeline.ask("Сколько возвратов по причине брака")

    first_prompt = client.calls[0][0]
    second_prompt = client.calls[2][0]
    assert first_prompt == second_prompt


def test_only_limited_rows_are_sent_to_model(catalog, examples):
    """В модель уходит ограниченное число строк результата."""
    large = pd.DataFrame({"region": [f"Р{i}" for i in range(500)], "revenue": range(500)})
    client = ScriptedClient([VALID_SQL, "Вывод."])
    pipeline = Pipeline(
        catalog=catalog,
        examples=examples,
        llm=client,
        database=InMemoryDatabase(default=large),
        data_as_of="2026-07-28",
        max_rows_to_llm=100,
    )
    pipeline.ask("Покажи выручку по регионам")

    _, messages, _ = client.calls[1]
    summary_prompt = messages[-1][1]
    assert "Р99" in summary_prompt
    assert "Р100" not in summary_prompt
