"""Проверка безопасности SQL-запросов, сформированных языковой моделью.

Модуль реализует рубежи защиты с первого по четвёртый. Полная схема защиты
описана в docs/architecture.md, раздел "Рубежи защиты".

Исходное допущение: языковая модель считается недоверенным источником.
Проверка строится так, как если бы модель была полностью скомпрометирована
и формировала произвольный текст запроса.

Второе допущение называется fail-closed, то есть "отказ по умолчанию".
Запрос блокируется, если проверка не может доказать его безопасность.
Формулировка "не нашли ничего запрещённого, значит пропускаем" считается
ошибочной. Верная формулировка: "не смогли проверить, значит отказ".

Рубежи, реализованные вне этого модуля:
    рубеж 0  ограничение грамматикой на этапе генерации (сервер vLLM)
    рубеж 5  оценка стоимости запроса до выполнения (модуль db.py)
    рубеж 6  права доступа на стороне СУБД (sql/02_security.sql)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import sqlglot
from sqlglot import exp

DIALECT = "tsql"

# Максимальное число строк, которое разрешено вернуть из СУБД.
# Не путать с двумя другими ограничениями: объёмом данных, передаваемым
# в языковую модель (около 100 строк), и объёмом отображения в интерфейсе
# (от 1000 до 5000 строк). Подробнее в docs/architecture.md.
MAX_ROWS = 10_000

# Узлы синтаксического дерева, недопустимые в аналитическом запросе.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Into,  # конструкция SELECT ... INTO создаёт таблицу
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Grant,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    # Библиотека sqlglot сворачивает нераспознанные инструкции в узел Command.
    # Сюда попадают EXEC, DBCC, вызовы расширенных процедур xp_. Отказ по
    # умолчанию требует блокировать всё, что не разобрано структурно.
    exp.Command,
)

# Функции, дающие доступ к данным за пределами разрешённых витрин.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {"openrowset", "openquery", "opendatasource", "openxml"}
)


class Reason(str, Enum):
    """Причина отказа.

    Значение передаётся обратно языковой модели, чтобы она исправила запрос.
    Пользователю причина отказа в исходном виде не показывается.
    """

    PARSE_FAILED = "parse_failed"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_A_SELECT = "not_a_select"
    FORBIDDEN_NODE = "forbidden_node"
    FORBIDDEN_FUNCTION = "forbidden_function"
    CROSS_SERVER = "cross_server"
    NOT_WHITELISTED = "not_whitelisted"
    CARTESIAN_JOIN = "cartesian_join"


@dataclass(frozen=True)
class Rejected:
    """Запрос отклонён.

    Атрибуты:
        reason: класс отказа для журнала и метрик.
        message: пояснение для языковой модели. Текст должен объяснять,
            как исправить запрос, а не только констатировать ошибку.
    """

    reason: Reason
    message: str


@dataclass(frozen=True)
class Accepted:
    """Запрос допущен к выполнению.

    Атрибуты:
        sql: текст запроса после нормализации и добавления ограничения строк.
        tables: набор витрин, к которым обращается запрос. Пишется в журнал
            аудита и используется для проверки прав пользователя.
    """

    sql: str
    tables: frozenset[str]


Result = Accepted | Rejected


def validate(sql: str, allowed_views: frozenset[str]) -> Result:
    """Проверяет запрос на рубежах с первого по четвёртый.

    Аргументы:
        sql: текст запроса, сформированный языковой моделью.
        allowed_views: белый список, то есть перечень полных имён витрин вида
            "mart.v_sales", к которым разрешено обращаться. Список загружается
            из манифеста витрин и совпадает с правами учётной записи в СУБД.

    Возвращает:
        Accepted с исправленным текстом запроса либо Rejected с причиной отказа.
        Исключения наружу не выбрасываются: любая внутренняя ошибка разбора
        превращается в отказ.
    """
    # Рубеж 1. Разбор запроса в синтаксическое дерево.
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except Exception:
        return Rejected(
            Reason.PARSE_FAILED,
            "Запрос не удалось разобрать. Упростите синтаксис и используйте "
            "стандартные конструкции языка T-SQL.",
        )

    statements = [item for item in statements if item is not None]

    if len(statements) != 1:
        return Rejected(
            Reason.MULTIPLE_STATEMENTS,
            f"Допускается ровно один запрос, получено {len(statements)}. "
            "Уберите точку с запятой и дополнительные инструкции.",
        )

    root = statements[0]

    # Рубеж 2. Структурные правила.
    if not isinstance(root, (exp.Select, exp.Union, exp.With)):
        return Rejected(
            Reason.NOT_A_SELECT,
            f"Разрешены только запросы SELECT, получено "
            f"{type(root).__name__.upper()}. Доступ предоставлен только на чтение.",
        )

    for node_type in FORBIDDEN_NODES:
        node = root.find(node_type)
        if node is not None:
            return Rejected(
                Reason.FORBIDDEN_NODE,
                f"Конструкция {type(node).__name__.upper()} запрещена. "
                "Доступ предоставлен только на чтение.",
            )

    for function in root.find_all(exp.Anonymous):
        if str(function.this).lower() in FORBIDDEN_FUNCTIONS:
            return Rejected(
                Reason.FORBIDDEN_FUNCTION,
                f"Функция {function.this} запрещена: она открывает доступ "
                "к данным за пределами разрешённых витрин.",
            )

    if _has_join_without_condition(root):
        return Rejected(
            Reason.CARTESIAN_JOIN,
            "Соединение таблиц без условия ON порождает декартово произведение, "
            "то есть все возможные сочетания строк. Укажите условие соединения.",
        )

    # Рубеж 3. Проверка по белому списку.
    checked = _check_tables(root, allowed_views)
    if isinstance(checked, Rejected):
        return checked

    # Рубеж 4. Принудительное ограничение числа строк.
    limited = _enforce_row_limit(root)

    return Accepted(sql=limited.sql(dialect=DIALECT, pretty=True), tables=checked)


def _cte_names(root: exp.Expression) -> set[str]:
    """Возвращает имена временных выражений CTE.

    CTE, то есть Common Table Expression, это именованный подзапрос в конструкции
    WITH. В синтаксическом дереве обращение к нему выглядит так же, как обращение
    к таблице, но реальным объектом базы данных оно не является и проверке
    по белому списку не подлежит.
    """
    return {
        cte.alias_or_name.lower()
        for cte in root.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _check_tables(
    root: exp.Expression, allowed_views: frozenset[str]
) -> frozenset[str] | Rejected:
    """Проверяет все обращения к таблицам по белому списку.

    Обход выполняется по всему дереву, включая вложенные подзапросы и выражения
    WITH. Поиск по тексту запроса здесь не годится: он нашёл бы только первую
    таблицу после слова FROM и пропустил бы обращение к системному представлению
    из вложенного подзапроса.
    """
    cte_names = _cte_names(root)
    found: set[str] = set()

    for table in root.find_all(exp.Table):
        name = table.name.lower()
        if not name or name in cte_names:
            continue

        # Имя из четырёх частей вида [СЕРВЕР].[база].[схема].[таблица] означает
        # обращение к присоединённому серверу в обход белого списка.
        if table.catalog:
            return Rejected(
                Reason.CROSS_SERVER,
                "Обращения к другим серверам запрещены. Используйте только "
                "разрешённые витрины схемы mart.",
            )

        qualified = f"{table.db.lower()}.{name}" if table.db else name
        if qualified not in allowed_views:
            listed = ", ".join(sorted(allowed_views)[:10])
            return Rejected(
                Reason.NOT_WHITELISTED,
                f"Объект {qualified} недоступен. Доступные витрины: {listed}.",
            )
        found.add(qualified)

    return frozenset(found)


def _has_join_without_condition(root: exp.Expression) -> bool:
    """Ищет соединения без условия, порождающие декартово произведение.

    Такой запрос полностью законен с точки зрения синтаксиса: объекты верные,
    белый список пройден. Отсекается здесь и повторно на рубеже 5 по оценке
    стоимости выполнения.
    """
    for join in root.find_all(exp.Join):
        if join.args.get("on") or join.args.get("using"):
            continue
        if join.side or join.kind:
            if (join.kind or "").upper() == "CROSS":
                return True
            continue
        return True

    # Форма записи FROM a, b помещает второй источник не в узел Join,
    # а в список источников конструкции FROM.
    for select in root.find_all(exp.Select):
        from_clause = select.args.get("from")
        if from_clause is None:
            continue
        sources = getattr(from_clause, "expressions", [])
        if len(sources) > 1 and not select.args.get("where"):
            return True

    return False


def _enforce_row_limit(root: exp.Expression) -> exp.Expression:
    """Добавляет ограничение TOP N через синтаксическое дерево.

    Изменение вносится в дерево, а не приклеиванием текста к запросу. Склейка
    строк вида sql + " TOP 10000" ломается на любом нетривиальном запросе
    и обходится добавлением комментария.

    Собственное ограничение пользователя, если оно меньше предельного,
    сохраняется без изменений.
    """
    target = root.this if isinstance(root, exp.With) else root
    if not isinstance(target, exp.Select):
        return root

    existing = target.args.get("limit")
    if existing is not None:
        current = existing.expression
        if isinstance(current, exp.Literal) and int(current.this) <= MAX_ROWS:
            return root

    return root.limit(MAX_ROWS, dialect=DIALECT, copy=True)
