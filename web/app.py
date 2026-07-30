"""Веб-интерфейс аналитики корпоративного хранилища.

Пользователь задаёт вопрос на русском языке и получает три вида вывода,
предусмотренные техническим заданием: текстовый вывод, таблицу и график.

Отдельным блоком показывается ход обработки: какие витрины отобраны, какой
запрос сформирован, сколько потребовалось попыток. Раскрытие хода обработки
является требованием к системе, а не отладочным средством: пользователь,
принимающий решение по цифре, должен иметь возможность проверить, откуда
эта цифра взялась.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dwh_copilot.charts import ChartKind  # noqa: E402
from dwh_copilot.config import settings  # noqa: E402
from dwh_copilot.factory import build_catalog, build_pipeline  # noqa: E402

SAMPLE_QUESTIONS = [
    "Покажи динамику продаж по регионам за последние три месяца",
    "В каких подразделениях фактическая выручка ниже плана более чем на 10%?",
    "Топ-5 подразделений по выручке за последние три месяца",
    "Какая просроченная дебиторская задолженность у клиентов?",
    "Сколько возвратов по причинам за последний квартал",
    "Покажи зарплаты сотрудников по подразделениям",
]

st.set_page_config(
    page_title="Аналитика КХД",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_pipeline():
    """Собирает конвейер один раз на всё время работы приложения.

    Повторная сборка на каждый вопрос означала бы открытие нового соединения
    с СУБД и повторную загрузку манифеста витрин.
    """
    catalog = build_catalog()
    pipeline, _ = build_pipeline(catalog)
    return pipeline, catalog


def render_chart(frame: pd.DataFrame, chart) -> None:
    """Строит график по описанию, полученному от правила выбора."""
    if chart is None or chart.kind is ChartKind.NONE:
        if chart is not None and chart.reason:
            st.caption(f"График не построен. {chart.reason}")
        return

    if chart.kind is ChartKind.VALUE:
        value = frame.iloc[0, 0]
        st.metric(label=frame.columns[0], value=f"{value:,.2f}".replace(",", " "))
        return

    if chart.kind is ChartKind.LINE:
        figure = px.line(
            frame, x=chart.x, y=chart.y, color=chart.series, markers=True
        )
    else:
        figure = px.bar(frame, x=chart.x, y=chart.y)

    figure.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=380,
        xaxis_title=None,
        yaxis_title=None,
    )
    st.plotly_chart(figure, use_container_width=True)
    st.caption(chart.reason)


def render_trace(answer) -> None:
    """Показывает ход обработки вопроса."""
    with st.expander("Как получен ответ", expanded=False):
        st.markdown("**Отобранные витрины**")
        st.code(", ".join(answer.selected_views) or "нет", language=None)

        if answer.sql:
            st.markdown("**Выполненный запрос**")
            st.code(answer.sql, language="sql")

        st.markdown("**Попытки формирования запроса**")
        attempts = pd.DataFrame(
            [
                {
                    "№": index + 1,
                    "Результат": attempt.outcome,
                    "Пояснение": attempt.detail[:160],
                }
                for index, attempt in enumerate(answer.attempts)
            ]
        )
        st.dataframe(attempts, use_container_width=True, hide_index=True)

        st.caption(
            f"Код обращения: {answer.trace_id}  |  "
            f"Время обработки: {answer.elapsed_seconds:.1f} с  |  "
            f"Повторов: {answer.retry_count}"
        )


def main() -> None:
    st.title("Аналитика корпоративного хранилища")
    st.caption(
        "Вопрос на русском языке. Запрос формируется локальной языковой моделью, "
        "проверяется на безопасность и выполняется под учётной записью только для чтения."
    )

    try:
        pipeline, catalog = get_pipeline()
    except Exception as error:
        st.error(f"Не удалось подключиться к хранилищу или серверу модели: {error}")
        st.stop()

    with st.sidebar:
        st.subheader("Состояние")
        st.metric("Витрин в каталоге", len(catalog.allowed_views))
        st.metric("Из них с описанием", len(catalog.implemented_views))
        st.caption(f"Данные загружены по {settings.data_as_of}")
        st.divider()

        st.subheader("Примеры вопросов")
        for question in SAMPLE_QUESTIONS:
            if st.button(question, use_container_width=True, key=question):
                st.session_state["question"] = question

        st.divider()
        st.caption(
            "Последний вопрос в перечне проверяет отказ: данных по оплате труда "
            "в разрешённых витринах нет, и система обязана это сообщить, "
            "а не придумать запрос."
        )

    question = st.text_input(
        "Ваш вопрос",
        value=st.session_state.get("question", ""),
        placeholder="Например: покажи выручку по регионам за июнь",
    )

    if not question:
        st.info("Задайте вопрос или выберите пример в боковой панели.")
        return

    with st.spinner("Формирую запрос и выполняю его..."):
        answer = pipeline.ask(question)

    if answer.refused:
        st.warning(f"**Ответить нельзя.** {answer.message}")
        render_trace(answer)
        return

    if not answer.ok:
        st.error(answer.message)
        st.caption(f"Код обращения для обращения к администратору: {answer.trace_id}")
        render_trace(answer)
        return

    if answer.summary:
        st.success(answer.summary)

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.subheader("Данные")
        st.dataframe(
            answer.frame.head(settings.max_rows_to_display),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"Строк получено: {len(answer.frame)}")

    with right:
        st.subheader("График")
        render_chart(answer.frame, answer.chart)

    render_trace(answer)


if __name__ == "__main__":
    main()
