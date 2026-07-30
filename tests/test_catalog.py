"""Проверки каталога витрин и отбора витрин под вопрос."""

import pytest

from dwh_copilot.catalog import Catalog

MANIFEST = "config/views.yaml"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load(MANIFEST)


def test_manifest_declares_fifty_views(catalog):
    """Объём витрин MVP согласован с заказчиком и зафиксирован в манифесте."""
    assert len(catalog.allowed_views) == 50


def test_view_names_are_unique(catalog):
    names = [view.name for view in catalog.views]
    assert len(names) == len(set(names))


def test_all_views_belong_to_mart_schema(catalog):
    """Права чтения выданы только на схему mart."""
    assert all(name.startswith("mart.") for name in catalog.allowed_views)


def test_index_covers_every_view(catalog):
    """Компактный указатель подаётся в запрос к модели целиком."""
    index = catalog.render_index()
    for name in catalog.allowed_views:
        assert name in index


def test_index_stays_within_token_budget(catalog):
    """Указатель занимает постоянную часть запроса и должен оставаться компактным.

    Ориентир: около 900 токенов. Для русского текста одна лексема занимает
    примерно четыре символа, отсюда предел в 4000 символов.
    """
    assert len(catalog.render_index()) < 4000


def test_details_rendered_only_for_described_views(catalog):
    """Витрины без подробного описания в запрос не подаются."""
    text = catalog.render_details(["mart.v_sales", "mart.v_web_traffic"])
    assert "mart.v_sales" in text
    assert "mart.v_web_traffic" not in text


def test_details_include_sample_values(catalog):
    """Без примеров значений модель формирует запрос, возвращающий ноль строк."""
    text = catalog.render_details(["mart.v_sales"])
    assert "Северо-Запад" in text


def test_details_include_default_interpretations(catalog):
    """Трактовка неоднозначных формулировок закреплена в манифесте."""
    text = catalog.render_details(["mart.v_sales"])
    assert "Регион отгрузки" in text


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Покажи динамику продаж по регионам за последние три месяца", "mart.v_sales"),
        (
            "В каких подразделениях фактическая выручка ниже плана более чем на 10%?",
            "mart.v_sales_plan_fact",
        ),
        ("Какая просроченная дебиторка у корпоративных клиентов?", "mart.v_receivables"),
        ("Сколько ноутбуков на складе в Гомеле?", "mart.v_stock_balance"),
        ("Какая маржа по серверам?", "mart.v_margin"),
        ("Сколько возвратов из-за брака?", "mart.v_returns"),
        ("Какие поставщики срывают сроки поставки?", "mart.v_purchases"),
        ("Сколько платежей прошло картой?", "mart.v_payments"),
    ],
)
def test_relevant_view_is_selected(catalog, question, expected):
    """Полнота отбора: нужная витрина должна попасть в выборку.

    Это отдельная измеряемая величина качества системы. Пропуск витрины
    на этом шаге делает верный ответ невозможным независимо от того,
    насколько хорошо модель формирует запросы.
    """
    assert expected in catalog.select(question, top_k=4)


def test_selection_never_returns_undescribed_views(catalog):
    """Отбирать витрину, по которой нечего подать в запрос, бессмысленно."""
    selected = catalog.select("Посещаемость интернет-магазина по источникам")
    for name in selected:
        assert catalog.get(name).has_full_description


def test_selection_falls_back_when_nothing_matches(catalog):
    """При отсутствии совпадений отбор возвращает витрины, а не пустой список.

    Отсутствие подходящей витрины должно приводить к обоснованному отказу
    модели, а не к пустому запросу.
    """
    assert catalog.select("абракадабра квазимодо") != []
