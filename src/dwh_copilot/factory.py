"""Сборка рабочих объектов системы из настроек.

Модуль вынесен отдельно, чтобы проверка целостности набора бизнес-вопросов
и модульные проверки работали без установки драйвера ODBC и без запущенного
сервера вывода модели.
"""

from __future__ import annotations

from dwh_copilot.catalog import Catalog
from dwh_copilot.config import Settings
from dwh_copilot.config import settings as default_settings
from dwh_copilot.db import MsSqlDatabase
from dwh_copilot.examples import ExampleBank
from dwh_copilot.llm import OpenAiCompatibleClient
from dwh_copilot.pipeline import Pipeline


def build_catalog(config: Settings | None = None) -> Catalog:
    """Загружает каталог витрин."""
    config = config or default_settings
    return Catalog.load(config.views_manifest)


def build_pipeline(
    catalog: Catalog | None = None, config: Settings | None = None
) -> tuple[Pipeline, MsSqlDatabase]:
    """Собирает конвейер обработки и подключение к хранилищу.

    Возвращает пару из конвейера и подключения. Подключение возвращается
    отдельно, поскольку прогон набора бизнес-вопросов использует его напрямую
    для выполнения эталонных запросов.
    """
    config = config or default_settings
    catalog = catalog or build_catalog(config)

    database = MsSqlDatabase(
        connection_string=config.odbc_connection_string,
        query_timeout=config.query_timeout_seconds,
        max_estimated_rows=config.max_estimated_rows,
        max_estimated_cost=config.max_estimated_cost,
    )

    llm = OpenAiCompatibleClient(
        base_url=config.llm_base_url,
        model=config.llm_model,
        api_key=config.llm_api_key,
        timeout=config.llm_timeout_seconds,
    )

    pipeline = Pipeline(
        catalog=catalog,
        examples=ExampleBank.load(config.examples_path),
        llm=llm,
        database=database,
        data_as_of=config.data_as_of,
        max_repair_attempts=config.max_repair_attempts,
        max_rows_to_llm=config.max_rows_to_llm,
        top_k_views=config.top_k_views,
        top_k_examples=config.top_k_examples,
        sql_temperature=config.sql_temperature,
        summary_temperature=config.summary_temperature,
    )

    return pipeline, database
