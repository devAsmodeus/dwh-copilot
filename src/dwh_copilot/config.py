"""Настройки приложения.

Значения читаются из переменных окружения либо из файла .env. Образец файла
со всеми параметрами и пояснениями находится в .env.example.

Учётные данные в системе контроля версий не хранятся. Файл .env внесён
в .gitignore.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Параметры работы системы."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DWH_",
        extra="ignore",
    )

    # --- Языковая модель -----------------------------------------------------

    llm_base_url: str = Field(
        default="http://localhost:8000/v1",
        description="Адрес сервера вывода, совместимого с интерфейсом OpenAI",
    )
    llm_model: str = Field(
        default="Qwen/Qwen3-30B-A3B-Instruct-AWQ",
        description="Имя модели на сервере вывода",
    )
    llm_api_key: str = Field(
        default="not-required",
        description="Ключ доступа. Для локального vLLM значение не проверяется",
    )
    llm_timeout_seconds: float = Field(default=60.0)

    # Температура при формировании запроса SQL. Ноль означает отсутствие
    # случайности: на один и тот же вопрос модель выдаёт один и тот же запрос.
    # Это обязательное условие воспроизводимости прогона набора вопросов.
    sql_temperature: float = Field(default=0.0)

    # Температура при формировании текстового вывода. Небольшая случайность
    # делает формулировки живее и на точность не влияет, поскольку цифры
    # берутся из готовой таблицы.
    summary_temperature: float = Field(default=0.3)

    # --- Хранилище данных ----------------------------------------------------

    mssql_host: str = Field(default="localhost")
    mssql_port: int = Field(default=1433)
    mssql_database: str = Field(default="dwh_demo")
    mssql_user: str = Field(
        default="dwh_reader",
        description="Учётная запись только для чтения. Права на базовые таблицы отозваны",
    )
    mssql_password: str = Field(default="")
    mssql_driver: str = Field(default="ODBC Driver 18 for SQL Server")
    mssql_trust_certificate: bool = Field(default=True)

    # --- Ограничения ---------------------------------------------------------

    query_timeout_seconds: int = Field(
        default=30,
        description="Предельное время выполнения запроса в СУБД",
    )
    max_estimated_rows: int = Field(
        default=1_000_000,
        description="Порог оценочного числа строк. Проверяется до выполнения запроса",
    )
    max_estimated_cost: float = Field(
        default=50.0,
        description="Порог оценочной стоимости плана. Проверяется до выполнения запроса",
    )
    max_rows_to_llm: int = Field(
        default=100,
        description="Сколько строк результата передаётся модели для текстового вывода",
    )
    max_rows_to_display: int = Field(
        default=5_000,
        description="Сколько строк показывается в таблице интерфейса",
    )
    max_repair_attempts: int = Field(
        default=2,
        description="Сколько раз модели даётся возможность исправить запрос",
    )

    # --- Каталог витрин ------------------------------------------------------

    views_manifest: str = Field(default="config/views.yaml")
    examples_path: str = Field(default="config/examples.yaml")
    top_k_views: int = Field(default=8)
    top_k_examples: int = Field(default=4)

    # --- Даты ----------------------------------------------------------------

    # Дата актуальности данных. Относительные периоды вида "последние три месяца"
    # отсчитываются от неё, а не от текущей даты. Иначе ответ содержит пустой
    # хвост за дни, которые ещё не загружены, и пользователь теряет доверие
    # к системе. В промышленной установке значение читается из служебной
    # таблицы хранилища.
    data_as_of: str = Field(default="2026-07-28")

    @property
    def odbc_connection_string(self) -> str:
        """Строка подключения к Microsoft SQL Server."""
        trust = "yes" if self.mssql_trust_certificate else "no"
        return (
            f"DRIVER={{{self.mssql_driver}}};"
            f"SERVER={self.mssql_host},{self.mssql_port};"
            f"DATABASE={self.mssql_database};"
            f"UID={self.mssql_user};"
            f"PWD={self.mssql_password};"
            f"TrustServerCertificate={trust};"
            # Приложение никогда не изменяет данные. Признак закреплён
            # на уровне подключения дополнительно к правам учётной записи.
            f"ApplicationIntent=ReadOnly;"
        )


settings = Settings()
