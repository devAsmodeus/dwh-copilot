"""Снятие изображений интерфейса для документации.

Запускается при поднятом стенде:

    docker compose up -d
    python scripts/capture_screenshots.py

Результат: docs/screenshots/*.png

Изображения снимаются с работающей системы, а не рисуются. Каждый вопрос
проходит полный путь: отбор витрин, формирование запроса языковой моделью,
проверку безопасности, выполнение в СУБД, построение графика и текстового
вывода.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

APP_URL = "http://localhost:8501"
OUTPUT_DIR = Path(__file__).parent.parent / "docs" / "screenshots"

# Предел ожидания ответа. На процессоре модель отвечает за 30-60 секунд,
# на видеокарте за 3-8 секунд.
ANSWER_TIMEOUT_MS = 300_000

SHOTS = [
    (
        "01-dynamics",
        "Покажи динамику продаж по регионам за последние три месяца",
        "Первый пример вопроса из технического задания",
    ),
    (
        "02-plan-fact",
        "В каких подразделениях фактическая выручка ниже плана более чем на 10%?",
        "Второй пример вопроса из технического задания",
    ),
    (
        "03-refusal",
        "Покажи зарплаты сотрудников по подразделениям",
        "Обоснованный отказ: данных нет в разрешённых витринах",
    ),
]

# Вставка значения в поле через собственный установщик свойства. Обычный ввод
# не приводит к обновлению состояния приложения, поскольку интерфейс построен
# на React и отслеживает события, а не изменение свойства напрямую.
SUBMIT_SCRIPT = """
(question) => {
    const field = document.querySelector('input[type=text]');
    if (!field) return false;
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
    setter.call(field, question);
    field.dispatchEvent(new Event('input', {bubbles: true}));
    field.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
    field.blur();
    return true;
}
"""

EXPAND_TRACE = """
() => {
    document.querySelectorAll('summary').forEach(item => {
        if (item.innerText.includes('Как получен')) item.click();
    });
}
"""


def ask(page: Page, question: str) -> None:
    """Задаёт вопрос и дожидается ответа."""
    page.goto(APP_URL, wait_until="networkidle")
    page.wait_for_selector("input[type=text]", timeout=60_000)

    if not page.evaluate(SUBMIT_SCRIPT, question):
        raise RuntimeError("Поле ввода не найдено")

    # Признаком завершения служит исчезновение указателя занятости.
    page.wait_for_selector('[data-testid="stSpinner"]', timeout=30_000)
    page.wait_for_selector(
        '[data-testid="stSpinner"]', state="detached", timeout=ANSWER_TIMEOUT_MS
    )
    page.wait_for_timeout(2_500)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        for name, question, note in SHOTS:
            print(f"{name}: {question}")
            try:
                ask(page, question)
            except Exception as error:
                print(f"  не удалось получить ответ: {error}")
                continue

            page.screenshot(path=str(OUTPUT_DIR / f"{name}.png"), full_page=True)
            print(f"  сохранено: {name}.png  ({note})")

            # Для первого вопроса дополнительно снимается раскрытый ход обработки.
            if name == "01-dynamics":
                page.evaluate(EXPAND_TRACE)
                page.wait_for_timeout(1_200)
                page.screenshot(
                    path=str(OUTPUT_DIR / "04-trace.png"), full_page=True
                )
                print("  сохранено: 04-trace.png  (ход обработки запроса)")

        browser.close()

    print(f"\nИзображения в каталоге {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
