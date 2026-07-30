"""Обращение к серверу вывода языковой модели.

Используется интерфейс, совместимый с OpenAI. Такой интерфейс предоставляют
vLLM, SGLang и Ollama, поэтому смена сервера вывода не требует правок кода.

В промышленной установке применяется vLLM. Выбор обоснован в docs/architecture.md
и опирается на четыре возможности: кэширование префикса подсказки, ограничение
вывода грамматикой, страничное управление памятью под контекст и выдача метрик
для системы мониторинга.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

# Признак отказа. Модели разрешено отвечать этой строкой, если ответить
# на вопрос по доступным витринам нельзя. Отказ считается правильным ответом
# и учитывается в наборе бизнес-вопросов отдельной величиной.
REFUSAL_MARKER = "NO_ANSWER"

# Обрамление разметкой, которое модели добавляют к коду даже при явном запрете.
_FENCE_RE = re.compile(r"^\s*```(?:sql|tsql)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Completion:
    """Ответ модели.

    Атрибуты:
        text: текст ответа без обрамления разметкой.
        prompt_tokens: размер поданной подсказки.
        completion_tokens: размер ответа.
        cached_tokens: сколько токенов подсказки взято из кэша префикса.
            Величина показывает, работает ли кэширование. Падение этого
            значения означает, что постоянная часть подсказки перестала
            совпадать между запросами.
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def is_refusal(self) -> bool:
        return self.text.strip().upper().startswith(REFUSAL_MARKER)

    @property
    def refusal_reason(self) -> str:
        """Причина отказа без служебного признака."""
        text = self.text.strip()
        if not self.is_refusal:
            return ""
        _, _, reason = text.partition(":")
        return reason.strip() or "Ответить по доступным витринам нельзя."


class LlmClient(Protocol):
    """Интерфейс сервера вывода."""

    def complete(
        self, system_prompt: str, messages: list[tuple[str, str]], temperature: float
    ) -> Completion:
        """Выполняет обращение к модели.

        Аргументы:
            system_prompt: постоянная часть подсказки.
            messages: последовательность пар "роль и текст". Роль принимает
                значения user либо assistant. Последовательность нужна
                для повторных попыток исправления запроса: предыдущий ответ
                модели и текст ошибки передаются вместе с новым заданием.
            temperature: степень случайности при выборе следующего слова.
        """
        ...


class OpenAiCompatibleClient:
    """Клиент сервера вывода с интерфейсом OpenAI."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "not-required",
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(
        self, system_prompt: str, messages: list[tuple[str, str]], temperature: float
    ) -> Completion:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *({"role": role, "content": text} for role, text in messages),
            ],
            "temperature": temperature,
            # Запас на длинный запрос с оконными функциями.
            "max_tokens": 1024,
        }
        response = self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        usage = data.get("usage", {})
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)

        return Completion(
            text=strip_code_fence(data["choices"][0]["message"]["content"]),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cached_tokens=cached,
        )

    def close(self) -> None:
        self._client.close()


class ScriptedClient:
    """Заглушка сервера вывода для проверок.

    Возвращает заранее заданные ответы по порядку обращений. Нужна, чтобы
    проверять работу конвейера обработки, повторные попытки исправления
    запроса и обработку отказов без запуска модели и графического ускорителя.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._calls: list[tuple[str, list[tuple[str, str]], float]] = []

    def complete(
        self, system_prompt: str, messages: list[tuple[str, str]], temperature: float
    ) -> Completion:
        self._calls.append((system_prompt, messages, temperature))
        if not self._responses:
            raise AssertionError("Заглушка исчерпала список заданных ответов")
        return Completion(text=strip_code_fence(self._responses.pop(0)))

    @property
    def calls(self) -> list[tuple[str, list[tuple[str, str]], float]]:
        """Журнал обращений. Используется в проверках."""
        return self._calls


def strip_code_fence(text: str) -> str:
    """Убирает обрамление разметкой вокруг ответа модели.

    Модели добавляют тройные кавычки к коду даже при явном запрете в правилах.
    Без этой обработки разбор запроса завершается отказом, и система тратит
    попытку исправления на ошибку, которая не является ошибкой модели.
    """
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()
