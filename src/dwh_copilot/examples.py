"""Банк примеров "вопрос и эталонный запрос".

Подача трёх-пяти подходящих примеров поднимает долю верных ответов сильнее,
чем расширение описания витрин. Причина в том, что примеры передают модели
не структуру данных, а принятый в организации способ работы: как отсчитывать
периоды, какие фильтры ставятся всегда, как называть вычисляемые показатели.

Банк пополняется в ходе эксплуатации. Каждый ответ, проверенный и подтверждённый
аналитиком, добавляется в файл через обычный запрос на слияние. Система
становится точнее по мере использования, и рост качества виден на наборе
бизнес-вопросов.

Примеры хранятся в системе контроля версий рядом с определениями витрин.
Хранение в базе данных или в корпоративной вики отклонено: там нет обязательной
проверки изменений, а эталонный запрос с ошибкой распространяет эту ошибку
на все последующие ответы модели.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from dwh_copilot.catalog import lexical_overlap, tokenize


@dataclass(frozen=True)
class Example:
    """Пара "вопрос и эталонный запрос".

    Атрибуты:
        question: вопрос на русском языке.
        sql: эталонный запрос, написанный человеком.
        views: витрины, задействованные в запросе. Используются при отборе:
            пример по той же витрине полезнее примера по другой предметной
            области, даже если формулировки вопросов похожи.
    """

    question: str
    sql: str
    views: tuple[str, ...] = ()


class ExampleBank:
    """Хранилище примеров с отбором по близости к вопросу."""

    def __init__(self, examples: list[Example]) -> None:
        self._examples = examples

    @classmethod
    def load(cls, path: str | Path) -> ExampleBank:
        """Загружает банк примеров из файла в формате YAML."""
        file = Path(path)
        if not file.exists():
            return cls([])
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        examples = [
            Example(
                question=item["question"],
                sql=item["sql"],
                views=tuple(item.get("views", ())),
            )
            for item in raw.get("examples", ())
        ]
        return cls(examples)

    def __len__(self) -> int:
        return len(self._examples)

    def select(
        self, question: str, top_k: int = 4, views: list[str] | None = None
    ) -> list[tuple[str, str]]:
        """Отбирает примеры, наиболее близкие к вопросу.

        Аргументы:
            question: вопрос пользователя.
            top_k: сколько примеров вернуть.
            views: витрины, отобранные под вопрос. При совпадении витрины
                пример получает надбавку к оценке близости.

        Возвращает:
            Пары "вопрос и запрос" в порядке убывания близости.
        """
        if not self._examples:
            return []

        tokens = tokenize(question)
        selected_views = set(views or ())

        scored: list[tuple[float, int, Example]] = []
        for index, example in enumerate(self._examples):
            score = lexical_overlap(tokens, example.question)
            if selected_views and selected_views & set(example.views):
                score += 2.0
            if score > 0:
                scored.append((score, index, example))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(example.question, example.sql) for _, _, example in scored[:top_k]]
