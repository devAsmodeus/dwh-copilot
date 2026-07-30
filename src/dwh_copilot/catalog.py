"""Каталог витрин корпоративного хранилища и отбор витрин под вопрос.

Витрина в терминах этого проекта представляет собой представление (VIEW) в схеме
mart, на которое выданы права чтения. Бизнес-логика расчёта показателей закреплена
внутри представления, а не в тексте запроса, формируемого языковой моделью.

При объёме порядка 50 витрин подать их полные описания в запрос к модели нельзя:
получается 10-12 тысяч токенов, и качество генерации падает из-за большого объёма
нерелевантного текста. Поэтому описание подаётся в двух видах.

Первый вид, компактный указатель. Содержит имя и одну строку назначения по каждой
из 50 витрин, занимает около 900 токенов. Подаётся всегда и целиком. Нужен, чтобы
модель могла обнаружить существование подходящей витрины, даже если её подробное
описание в этот раз не подавалось.

Второй вид, полное описание. Содержит перечень колонок с типами и пояснениями,
связи с другими витринами, примеры значений категориальных полей. Подаётся только
по восьми витринам, отобранным под конкретный вопрос.

Отбор выполняется поиском по смысловой близости, а не дополнительным обращением
к языковой модели. Отдельное обращение добавило бы к времени ответа полторы-две
секунды, тогда как поиск по 50 записям на процессоре занимает менее 50 миллисекунд.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Количество витрин, подробные описания которых попадают в запрос к модели.
TOP_K_VIEWS = 8


@dataclass(frozen=True)
class Column:
    """Колонка витрины."""

    name: str
    type: str
    description: str
    # Примеры значений категориального поля. Без них модель формирует запрос,
    # который выполняется без ошибок и возвращает ноль строк, потому что
    # не угадала написание значения, например "Москва" вместо "МОСКВА".
    sample_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class View:
    """Витрина хранилища.

    Атрибуты:
        name: полное имя вида "mart.v_sales".
        purpose: назначение одной строкой. Попадает в компактный указатель.
        implemented: признак наличия витрины в демонстрационной базе.
            Витрины со значением False описаны в указателе, но физически
            в демонстрационном стенде отсутствуют.
        columns: перечень колонок. Заполняется только для витрин,
            имеющих полное описание.
        joins: пояснения о связях с другими витринами.
        defaults: принятые по умолчанию трактовки неоднозначных запросов.
            Например, при вопросе "продажи по регионам" регион берётся
            по клиенту, а не по месту отгрузки. Решение принимает аналитик
            один раз при описании витрины, а не пользователь при каждом вопросе.
    """

    name: str
    purpose: str
    implemented: bool = False
    columns: tuple[Column, ...] = ()
    joins: tuple[str, ...] = ()
    defaults: dict[str, str] = field(default_factory=dict)

    @property
    def has_full_description(self) -> bool:
        return bool(self.columns)


class Catalog:
    """Каталог витрин, загруженный из манифеста."""

    def __init__(self, views: list[View]) -> None:
        self._views = views
        self._by_name = {view.name: view for view in views}

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        """Загружает каталог из файла манифеста в формате YAML."""
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        views = [cls._parse_view(item) for item in raw["views"]]
        return cls(views)

    @staticmethod
    def _parse_view(item: dict) -> View:
        columns = tuple(
            Column(
                name=column["name"],
                type=column["type"],
                description=column.get("description", ""),
                sample_values=tuple(column.get("sample_values", ())),
            )
            for column in item.get("columns", ())
        )
        return View(
            name=item["name"],
            purpose=item["purpose"],
            implemented=item.get("implemented", False),
            columns=columns,
            joins=tuple(item.get("joins", ())),
            defaults=item.get("defaults", {}) or {},
        )

    @property
    def views(self) -> tuple[View, ...]:
        """Все витрины каталога."""
        return tuple(self._views)

    @property
    def allowed_views(self) -> frozenset[str]:
        """Белый список витрин, то есть перечень разрешённых объектов.

        Значение передаётся в модуль проверки запросов и должно совпадать
        с правами учётной записи чтения в СУБД. Совпадение проверяется
        отдельным тестом.
        """
        return frozenset(self._by_name)

    @property
    def implemented_views(self) -> frozenset[str]:
        """Витрины, физически существующие в демонстрационной базе."""
        return frozenset(view.name for view in self._views if view.implemented)

    def get(self, name: str) -> View | None:
        return self._by_name.get(name)

    def render_index(self) -> str:
        """Формирует компактный указатель по всем витринам.

        Результат неизменен между запросами, поэтому попадает в кэшируемую
        часть запроса к модели.
        """
        lines = ["Доступные витрины хранилища:"]
        for view in sorted(self._views, key=lambda item: item.name):
            lines.append(f"  {view.name}: {view.purpose}")
        return "\n".join(lines)

    def render_details(self, names: list[str]) -> str:
        """Формирует подробные описания указанных витрин."""
        blocks = []
        for name in names:
            view = self._by_name.get(name)
            if view is None or not view.has_full_description:
                continue
            blocks.append(self._render_view(view))
        return "\n\n".join(blocks)

    @staticmethod
    def _render_view(view: View) -> str:
        lines = [f"Витрина {view.name}", f"Назначение: {view.purpose}", "Колонки:"]
        for column in view.columns:
            line = f"  {column.name} ({column.type}) {column.description}".rstrip()
            if column.sample_values:
                examples = ", ".join(column.sample_values)
                line += f". Примеры значений: {examples}"
            lines.append(line)
        if view.joins:
            lines.append("Связи:")
            lines.extend(f"  {item}" for item in view.joins)
        if view.defaults:
            lines.append("Трактовки по умолчанию:")
            lines.extend(f"  {key}: {value}" for key, value in sorted(view.defaults.items()))
        return "\n".join(lines)

    def select(self, question: str, top_k: int = TOP_K_VIEWS) -> list[str]:
        """Отбирает витрины, наиболее подходящие под вопрос.

        Аргументы:
            question: вопрос пользователя на русском языке.
            top_k: сколько витрин вернуть.

        Возвращает:
            Имена витрин в порядке убывания близости. Витрины без подробного
            описания в результат не попадают, поскольку подавать в запрос
            к модели по ним нечего.

        Замечание о реализации. Здесь применяется лексический отбор по совпадению
        основ слов. Он не требует загрузки модели эмбеддингов и поэтому работает
        в среде непрерывной интеграции и на машине без графического ускорителя.
        В промышленной установке отбор выполняется моделью BGE-M3 через класс,
        совместимый по интерфейсу. Точка подмены находится в модуле pipeline.py.
        """
        tokens = tokenize(question)
        scored: list[tuple[float, str]] = []
        for view in self._views:
            if not view.has_full_description:
                continue
            score = _lexical_score(tokens, view)
            if score > 0:
                scored.append((score, view.name))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        selected = [name for _, name in scored[:top_k]]

        # Если совпадений не нашлось, подаём витрины с подробным описанием
        # в алфавитном порядке. Отсутствие подходящей витрины должно приводить
        # к обоснованному отказу модели, а не к пустому запросу.
        if not selected:
            selected = sorted(view.name for view in self._views if view.has_full_description)[
                :top_k
            ]
        return selected


_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# Длина основы слова, по которой выполняется сравнение. Русский язык изменяет
# окончания, поэтому сравнение слов целиком даёт слишком много пропусков:
# "продаж" не совпадает с "продажи", "Гомеле" не совпадает с "Гомель".
# Обрезание до четырёх символов снимает эту проблему ценой отдельных ложных
# срабатываний, что приемлемо, поскольку отбирается восемь витрин из пятидесяти
# и полнота отбора здесь важнее точности.
_STEM_LENGTH = 4

# Слова, не несущие смысловой нагрузки при отборе витрин.
_STOP_WORDS = frozenset(
    {
        "и",
        "в",
        "на",
        "по",
        "за",
        "с",
        "у",
        "к",
        "от",
        "до",
        "для",
        "из",
        "покажи",
        "какая",
        "какой",
        "какие",
        "сколько",
        "как",
        "что",
        "кто",
        "мне",
        "нам",
        "наш",
        "нас",
        "был",
        "было",
        "были",
        "есть",
        "это",
        "последние",
        "последний",
        "последнее",
        "всего",
        "всех",
        "более",
        "менее",
        "год",
        "года",
        "месяц",
        "месяца",
        "неделя",
        "день",
        "дня",
    }
)


def tokenize(text: str) -> set[str]:
    """Разбивает текст на основы значимых слов.

    Возвращает множество основ, а не слов целиком, чтобы совпадали разные
    грамматические формы одного слова.
    """
    stems = set()
    for match in _WORD_RE.finditer(text):
        word = match.group().lower()
        if len(word) < 3 or word in _STOP_WORDS:
            continue
        stems.add(word[:_STEM_LENGTH])
    return stems


def lexical_overlap(tokens: set[str], text: str) -> float:
    """Считает число совпавших основ слов между вопросом и произвольным текстом.

    Используется банком примеров для отбора подходящих пар "вопрос и запрос".
    """
    return float(len(tokens & tokenize(text)))


def _lexical_score(tokens: set[str], view: View) -> float:
    """Оценивает близость витрины к вопросу по совпадению основ слов.

    Веса расставлены по различающей способности источника. Назначение витрины
    описывает предметную область и весит больше всего. Примеры значений
    категориальных полей, такие как "Гомель" или "Ноутбуки", встречаются редко
    и потому хорошо отличают одну витрину от другой. Имена и описания колонок
    повторяются в разных витринах и весят меньше всех.
    """
    purpose_tokens = tokenize(view.purpose)

    column_tokens: set[str] = set()
    sample_tokens: set[str] = set()
    for column in view.columns:
        column_tokens |= tokenize(column.description)
        column_tokens |= tokenize(column.name.replace("_", " "))
        for value in column.sample_values:
            sample_tokens |= tokenize(value)

    score = 3.0 * len(tokens & purpose_tokens)
    score += 2.0 * len(tokens & sample_tokens)
    score += 1.0 * len(tokens & column_tokens)
    return score
