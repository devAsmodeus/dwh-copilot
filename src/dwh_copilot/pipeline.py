"""Конвейер обработки вопроса пользователя.

Последовательность шагов:

    вопрос
      ├─ отбор витрин по смысловой близости         каталог, процессор
      ├─ отбор примеров запросов по близости        банк примеров, процессор
      ▼
    обращение 1 к модели: формирование запроса SQL
      ▼
    проверка безопасности, рубежи 1-4
      │   отказ, причина возвращается модели, до двух повторов
      ▼
    оценка стоимости выполнения, рубеж 5
      │   отказ, причина возвращается модели
      ▼
    выполнение запроса, рубеж 6
      │   ошибка, текст возвращается модели
      ▼
    результат
      ├─ таблица
      ├─ график по правилу, без обращения к модели
      └─ обращение 2 к модели: текстовый вывод

Основной сценарий требует двух обращений к модели. Повторные попытки
исправления добавляют до двух обращений и происходят только при отказе.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import pandas as pd

from dwh_copilot.catalog import Catalog
from dwh_copilot.charts import ChartSpec, choose_chart
from dwh_copilot.db import Database, QueryFailed, TooExpensive
from dwh_copilot.examples import ExampleBank
from dwh_copilot.llm import LlmClient
from dwh_copilot.prompts import (
    build_repair_prompt,
    build_summary_prompt,
    build_system_prompt,
    build_user_prompt,
    today_iso,
)
from dwh_copilot.sql_validator import Accepted, Rejected, validate

# Обобщённые формулировки для пользователя. Подробности отказа остаются
# в журнале: сообщения СУБД раскрывают структуру хранилища.
USER_FACING_FAILURE = (
    "Не удалось построить корректный запрос к хранилищу. "
    "Попробуйте переформулировать вопрос или обратитесь к администратору, "
    "указав код обращения."
)


@dataclass
class Attempt:
    """Одна попытка формирования запроса.

    Записывается в журнал аудита. По совокупности попыток считается величина
    "среднее число повторов", показывающая качество текста подсказки.
    """

    sql: str
    outcome: str
    detail: str = ""


@dataclass
class Answer:
    """Результат обработки вопроса.

    Атрибуты:
        trace_id: код обращения. Показывается пользователю при ошибке
            и позволяет найти полную запись в журнале.
        question: исходный вопрос.
        ok: признак успешной обработки.
        refused: признак обоснованного отказа. Отказ не является ошибкой:
            вопрос вне охвата доступных витрин обязан приводить к отказу,
            а не к придуманному запросу.
        sql: выполненный запрос.
        frame: таблица результата.
        chart: описание графика.
        summary: текстовый вывод.
        message: сообщение для пользователя при отказе или ошибке.
        attempts: журнал попыток.
        selected_views: витрины, отобранные под вопрос.
        elapsed_seconds: полное время обработки.
    """

    trace_id: str
    question: str
    ok: bool = False
    refused: bool = False
    sql: str = ""
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    chart: ChartSpec | None = None
    summary: str = ""
    message: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    selected_views: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def retry_count(self) -> int:
        """Число повторных попыток сверх первой."""
        return max(0, len(self.attempts) - 1)


class Pipeline:
    """Обработчик вопросов пользователя."""

    def __init__(
        self,
        catalog: Catalog,
        examples: ExampleBank,
        llm: LlmClient,
        database: Database,
        data_as_of: str,
        max_repair_attempts: int = 2,
        max_rows_to_llm: int = 100,
        top_k_views: int = 8,
        top_k_examples: int = 4,
        sql_temperature: float = 0.0,
        summary_temperature: float = 0.3,
    ) -> None:
        self._catalog = catalog
        self._examples = examples
        self._llm = llm
        self._db = database
        self._data_as_of = data_as_of
        self._max_repair_attempts = max_repair_attempts
        self._max_rows_to_llm = max_rows_to_llm
        self._top_k_views = top_k_views
        self._top_k_examples = top_k_examples
        self._sql_temperature = sql_temperature
        self._summary_temperature = summary_temperature

    def ask(self, question: str, with_summary: bool = True) -> Answer:
        """Обрабатывает вопрос и возвращает ответ.

        Аргументы:
            question: вопрос на русском языке.
            with_summary: формировать ли текстовый вывод. При прогоне набора
                бизнес-вопросов выключается, поскольку правильность оценивается
                по совпадению данных, а не по формулировке вывода.
        """
        started = time.perf_counter()
        answer = Answer(trace_id=uuid.uuid4().hex[:12], question=question)

        views = self._catalog.select(question, top_k=self._top_k_views)
        answer.selected_views = views

        system_prompt = build_system_prompt(
            self._catalog, data_as_of=self._data_as_of, today=today_iso()
        )
        user_prompt = build_user_prompt(
            question=question,
            catalog=self._catalog,
            view_names=views,
            examples=self._examples.select(question, top_k=self._top_k_examples),
        )

        messages: list[tuple[str, str]] = [("user", user_prompt)]

        for _ in range(self._max_repair_attempts + 1):
            completion = self._llm.complete(
                system_prompt, messages, temperature=self._sql_temperature
            )

            if completion.is_refusal:
                answer.refused = True
                answer.ok = True
                answer.message = completion.refusal_reason
                answer.attempts.append(Attempt(sql="", outcome="refused"))
                answer.elapsed_seconds = time.perf_counter() - started
                return answer

            sql = completion.text
            failure = self._try_execute(sql, answer)
            if failure is None:
                if with_summary:
                    answer.summary = self._summarize(question, answer.frame)
                answer.ok = True
                answer.elapsed_seconds = time.perf_counter() - started
                return answer

            messages.append(("assistant", sql))
            messages.append(("user", build_repair_prompt(sql, failure)))

        answer.message = USER_FACING_FAILURE
        answer.elapsed_seconds = time.perf_counter() - started
        return answer

    def _try_execute(self, sql: str, answer: Answer) -> str | None:
        """Проводит запрос через рубежи защиты и выполняет его.

        Возвращает None при успехе либо текст причины отказа для передачи
        модели. Причина адресована модели, не пользователю.
        """
        verdict = validate(sql, self._catalog.allowed_views)
        if isinstance(verdict, Rejected):
            answer.attempts.append(
                Attempt(sql=sql, outcome=verdict.reason.value, detail=verdict.message)
            )
            return verdict.message

        assert isinstance(verdict, Accepted)
        checked_sql = verdict.sql

        cost = self._db.check_cost(checked_sql)
        if isinstance(cost, TooExpensive):
            answer.attempts.append(
                Attempt(sql=checked_sql, outcome="too_expensive", detail=cost.message)
            )
            return cost.message

        try:
            result = self._db.execute(checked_sql)
        except QueryFailed as error:
            answer.attempts.append(
                Attempt(sql=checked_sql, outcome="db_error", detail=str(error))
            )
            return str(error)

        answer.attempts.append(Attempt(sql=checked_sql, outcome="ok"))
        answer.sql = checked_sql
        answer.frame = result.frame
        answer.chart = choose_chart(result.frame)
        return None

    def _summarize(self, question: str, frame: pd.DataFrame) -> str:
        """Формирует текстовый вывод по таблице результата.

        В модель передаётся ограниченное число строк. Ограничение защищает
        от переполнения контекста: результат, прошедший рубежи защиты, всё ещё
        может содержать тысячи строк, тогда как для вывода достаточно первых ста.
        """
        if frame.empty:
            return "Запрос выполнен, подходящих данных не найдено."

        head = frame.head(self._max_rows_to_llm)
        prompt = build_summary_prompt(question, head.to_markdown(index=False))
        completion = self._llm.complete(
            system_prompt="Ты аналитик. Отвечай кратко и по-деловому на русском языке.",
            messages=[("user", prompt)],
            temperature=self._summary_temperature,
        )
        return completion.text
