"""Прогон набора бизнес-вопросов и расчёт показателей качества.

Величина, по которой оценивается система, называется долей верных ответов
по совпадению данных. Тексты запросов не сравниваются: эталонный и полученный
запрос выполняются на одном и том же зафиксированном наборе данных,
после чего сравниваются полученные таблицы. Запрос, написанный иначе,
но дающий те же данные, засчитывается как верный.

Прогон выполняется автоматически при каждом изменении текста подсказки,
манифеста витрин, версии модели или правил проверки запросов. Снижение доли
верных ответов ниже достигнутого уровня блокирует приём изменения.

Ключевая мысль: текст подсказки для модели является кодом. Он хранится
в системе контроля версий, проходит проверку изменений и покрыт тестами.
Без этого любая правка подсказки превращается в лотерею, поскольку исправление
одного вопроса ломает три других незаметно для разработчика.

Запуск:
    python -m dwh_copilot.eval_runner --check-only
        Проверяет целостность набора без обращения к модели и СУБД.

    python -m dwh_copilot.eval_runner
        Полный прогон. Требует запущенного сервера вывода и базы данных.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from dwh_copilot.catalog import Catalog
from dwh_copilot.sql_validator import Rejected, validate

DEFAULT_QUESTIONS = "eval/questions.yaml"
DEFAULT_MANIFEST = "config/views.yaml"

# Точность сравнения чисел с плавающей точкой. Разные способы записать один
# и тот же расчёт дают расхождение в последних разрядах, которое не является
# ошибкой ответа.
ROUNDING = 2


@dataclass
class CaseResult:
    """Итог по одному вопросу набора."""

    id: str
    category: str
    correct: bool
    detail: str = ""
    retries: int = 0
    elapsed_seconds: float = 0.0
    valid_first_try: bool = False


@dataclass
class Report:
    """Показатели качества по итогам прогона."""

    total: int = 0
    correct: int = 0
    valid_first_try: int = 0
    retries: list[int] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    abstention_expected: int = 0
    abstention_correct: int = 0
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def execution_accuracy(self) -> float:
        """Доля верных ответов по совпадению данных."""
        return self.correct / self.total if self.total else 0.0

    @property
    def valid_sql_rate(self) -> float:
        """Доля запросов, прошедших проверку с первой попытки."""
        return self.valid_first_try / self.total if self.total else 0.0

    @property
    def retry_rate(self) -> float:
        """Среднее число повторных попыток на вопрос."""
        return statistics.fmean(self.retries) if self.retries else 0.0

    @property
    def latency_p50(self) -> float:
        return _percentile(self.latencies, 50)

    @property
    def latency_p95(self) -> float:
        """Время ответа, которое не превышается в 95 случаях из 100.

        Показатель важнее среднего: пользователь запоминает худший случай,
        а не типичный.
        """
        return _percentile(self.latencies, 95)

    @property
    def abstention_recall(self) -> float:
        """Доля вопросов вне охвата витрин, на которые система отказалась отвечать.

        Система, которая не отказывается никогда, придумывает ответы.
        Система, которая отказывается всегда, бесполезна.
        """
        if not self.abstention_expected:
            return 1.0
        return self.abstention_correct / self.abstention_expected

    def to_dict(self) -> dict:
        return {
            "execution_accuracy": round(self.execution_accuracy, 4),
            "valid_sql_rate": round(self.valid_sql_rate, 4),
            "retry_rate": round(self.retry_rate, 4),
            "latency_p50": round(self.latency_p50, 2),
            "latency_p95": round(self.latency_p95, 2),
            "abstention_recall": round(self.abstention_recall, 4),
            "total": self.total,
            "correct": self.correct,
            "cases": [asdict(case) for case in self.cases],
        }


def load_questions(path: str | Path = DEFAULT_QUESTIONS) -> tuple[dict, list[dict]]:
    """Загружает набор вопросов и его описание."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return raw.get("meta", {}), raw["questions"]


def normalize(frame: pd.DataFrame, ordered: bool) -> pd.DataFrame:
    """Приводит таблицу к виду, пригодному для сравнения.

    Снимаются три источника ложных расхождений: имена колонок, поскольку
    псевдоним не влияет на данные; разряды после запятой в числах
    с плавающей точкой; порядок строк, если он не был задан явно.
    """
    result = frame.copy()
    result.columns = range(len(result.columns))
    for column in result.columns:
        if pd.api.types.is_float_dtype(result[column]):
            result[column] = result[column].round(ROUNDING)
    if not ordered:
        result = result.sort_values(list(result.columns), kind="stable")
    return result.reset_index(drop=True)


def frames_match(expected: pd.DataFrame, actual: pd.DataFrame, ordered: bool) -> bool:
    """Сравнивает таблицы результата.

    Аргумент ordered равен True, если в эталонном запросе задана сортировка.
    В этом случае порядок строк значим: вопрос о первой пятёрке подразделений
    подразумевает именно порядок. В остальных случаях порядок строк в языке SQL
    не определён, и обе таблицы сортируются перед сравнением.
    """
    if expected.shape != actual.shape:
        return False
    return normalize(expected, ordered).equals(normalize(actual, ordered))


def check_integrity(questions: list[dict], catalog: Catalog) -> list[str]:
    """Проверяет целостность набора без обращения к модели и СУБД.

    Проверяется четыре условия: уникальность кодов вопросов, наличие эталонного
    запроса у отвечаемых вопросов, наличие пояснения об отказе у неотвечаемых,
    прохождение каждого эталонного запроса через собственный модуль безопасности.

    Последняя проверка важнее остальных. Расхождение означает одно из двух:
    либо эталон ошибочен, либо правила проверки стали строже, чем требуется
    для работы системы. Оба случая требуют вмешательства человека.
    """
    problems: list[str] = []
    seen: set[str] = set()

    for item in questions:
        code = item["id"]
        if code in seen:
            problems.append(f"{code}: повторяющийся код вопроса")
        seen.add(code)

        if item.get("answerable", True):
            sql = item.get("golden_sql")
            if not sql:
                problems.append(f"{code}: отсутствует эталонный запрос")
                continue
            verdict = validate(sql, catalog.allowed_views)
            if isinstance(verdict, Rejected):
                problems.append(
                    f"{code}: эталонный запрос отклонён проверкой "
                    f"({verdict.reason.value}) {verdict.message}"
                )
        else:
            if not item.get("expected_refusal"):
                problems.append(f"{code}: отсутствует ожидаемое пояснение об отказе")

    return problems


def check_golden_sql_executes(questions: list[dict], database) -> list[tuple[str, str]]:
    """Выполняет каждый эталонный запрос в СУБД и возвращает перечень неудач.

    Проверка разбора синтаксическим анализатором недостаточна. Библиотека
    sqlglot принимает конструкции, которые Microsoft SQL Server отвергает.
    Показательный пример: слово plan является в T-SQL зарезервированным
    и не может служить псевдонимом колонки без обрамления скобками, тогда как
    разбор такого запроса проходит без замечаний.

    Ошибка в эталонном запросе обесценивает весь прогон: вопрос засчитывается
    неверным независимо от того, что сформировала модель. Поэтому проверка
    выполнимости эталонов вынесена в отдельный режим и запускается на стенде
    до полного прогона.
    """
    failures: list[tuple[str, str]] = []
    for item in questions:
        sql = item.get("golden_sql")
        if not sql:
            continue
        try:
            database.execute(sql)
        except Exception as error:
            failures.append((item["id"], str(error)))
    return failures


def run(pipeline, questions: list[dict], database, verbose: bool = True) -> Report:
    """Выполняет полный прогон набора.

    Аргументы:
        pipeline: настроенный конвейер обработки вопросов.
        questions: набор вопросов.
        database: подключение к базе для выполнения эталонных запросов.
        verbose: выводить ход выполнения по каждому вопросу.

    Возвращает:
        Отчёт с показателями качества.

    Замечание об удобстве. Ход выполнения выводится по каждому вопросу, поскольку
    на процессоре без ускорителя один вопрос занимает до полутора минут, а весь
    набор до сорока. Молчание на такой срок не позволяет отличить работу
    от зависания.
    """
    report = Report(total=len(questions))

    for position, item in enumerate(questions, start=1):
        if verbose:
            print(
                f"[{position:>2}/{len(questions)}] {item['id']:<12} {item['question'][:56]}",
                flush=True,
            )
        answerable = item.get("answerable", True)
        if not answerable:
            report.abstention_expected += 1

        answer = pipeline.ask(item["question"], with_summary=False)
        report.retries.append(answer.retry_count)
        report.latencies.append(answer.elapsed_seconds)
        valid_first = answer.retry_count == 0 and not answer.refused
        if valid_first:
            report.valid_first_try += 1

        if not answerable:
            correct = answer.refused
            if correct:
                report.abstention_correct += 1
            detail = "" if correct else "Ожидался отказ, получен ответ"
        elif answer.refused:
            correct = False
            detail = f"Неожиданный отказ: {answer.message}"
        elif not answer.ok:
            correct = False
            detail = answer.message or "Запрос не выполнен"
        else:
            expected = database.execute(item["golden_sql"]).frame
            correct = frames_match(expected, answer.frame, item.get("ordered", False))
            detail = "" if correct else "Данные не совпали с эталоном"

        if correct:
            report.correct += 1

        if verbose:
            mark = "верно " if correct else "ошибка"
            note = f"  {detail}" if detail else ""
            print(
                f"     {mark}  {answer.elapsed_seconds:5.1f} с  "
                f"повторов: {answer.retry_count}{note}",
                flush=True,
            )

        report.cases.append(
            CaseResult(
                id=item["id"],
                category=item["category"],
                correct=correct,
                detail=detail,
                retries=answer.retry_count,
                elapsed_seconds=round(answer.elapsed_seconds, 2),
                valid_first_try=valid_first,
            )
        )

    return report


def format_report(report: Report) -> str:
    """Формирует сводку для вывода в консоль."""
    lines = [
        "",
        "Показатели качества",
        "-" * 52,
        f"  Доля верных ответов            {report.execution_accuracy:6.1%}"
        f"   ({report.correct} из {report.total})",
        f"  Запросы без повторов           {report.valid_sql_rate:6.1%}",
        f"  Среднее число повторов         {report.retry_rate:6.2f}",
        f"  Время ответа, медиана          {report.latency_p50:6.1f} с",
        f"  Время ответа, 95-й процентиль  {report.latency_p95:6.1f} с",
        f"  Полнота отказов                {report.abstention_recall:6.1%}",
        "",
    ]

    failed = [case for case in report.cases if not case.correct]
    if failed:
        lines.append("Вопросы с неверным ответом")
        lines.append("-" * 52)
        for case in failed:
            lines.append(f"  {case.id:12} {case.category:20} {case.detail}")
        lines.append("")

    return "\n".join(lines)


def _percentile(values: list[float], percent: int) -> float:
    """Возвращает значение процентиля методом ближайшего ранга."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(percent / 100 * len(ordered) + 0.5) - 1)
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    """Точка входа командной строки."""
    parser = argparse.ArgumentParser(
        description="Прогон набора бизнес-вопросов для оценки качества системы"
    )
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Проверить целостность набора без обращения к модели и базе данных",
    )
    parser.add_argument(
        "--check-sql",
        action="store_true",
        help=(
            "Дополнительно выполнить каждый эталонный запрос в СУБД. "
            "Требует поднятого стенда, обращений к языковой модели не делает"
        ),
    )
    parser.add_argument("--output", help="Файл для сохранения отчёта в формате JSON")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.0,
        help="Наименьшая допустимая доля верных ответов. Ниже неё запуск считается неуспешным",
    )
    args = parser.parse_args(argv)

    catalog = Catalog.load(args.manifest)
    meta, questions = load_questions(args.questions)

    problems = check_integrity(questions, catalog)
    if problems:
        print("Набор вопросов содержит ошибки:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"Набор проверен: {len(questions)} вопросов, ошибок нет.")
    print(f"Данные зафиксированы на дату {meta.get('snapshot_date', 'не указана')}.")

    if args.check_only and not args.check_sql:
        return 0

    # Полный прогон требует запущенного сервера вывода и базы данных.
    # Сборка зависимостей вынесена в отдельный модуль, чтобы проверка
    # целостности работала без установки драйвера ODBC.
    from dwh_copilot.factory import build_pipeline

    pipeline, database = build_pipeline(catalog)

    if args.check_sql:
        failures = check_golden_sql_executes(questions, database)
        if failures:
            print("Эталонные запросы, не выполнившиеся в СУБД:", file=sys.stderr)
            for code, error in failures:
                print(f"  {code}: {error}", file=sys.stderr)
            return 1
        answerable = sum(1 for item in questions if item.get("answerable", True))
        print(f"Эталонные запросы выполнены в СУБД: {answerable} из {answerable}.")
        if args.check_only:
            return 0

    report = run(pipeline, questions, database)
    print(format_report(report))

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Отчёт сохранён: {args.output}")

    if report.execution_accuracy < args.min_accuracy:
        print(
            f"Доля верных ответов {report.execution_accuracy:.1%} ниже порога "
            f"{args.min_accuracy:.1%}.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
