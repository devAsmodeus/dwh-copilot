# Образ веб-интерфейса.
#
# Сборка выполняется в два этапа. На первом собирается пакет, на втором
# остаётся только готовый дистрибутив и драйвер ODBC. Такое разделение
# убирает из итогового образа инструменты сборки.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /dist


FROM python:3.12-slim

# Драйвер ODBC для Microsoft SQL Server. Устанавливается из репозитория
# Microsoft, ключ проверяется по подписи.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
        https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 \
        unixodbc \
        # Библиотеки времени выполнения драйвера указываются явно.
        # Без этого apt считает их установленными как зависимости curl
        # и удаляет при очистке, после чего драйвер перестаёт загружаться,
        # а unixODBC сообщает об этом как о ненайденном файле самого драйвера.
        libgssapi-krb5-2 \
        libssl3 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
    && python -m pip install --no-cache-dir "pyodbc>=5.1" "streamlit>=1.37" "plotly>=5.22" \
    && rm -rf /tmp/*.whl

COPY config ./config
COPY eval ./eval
COPY web ./web

# Работа выполняется от непривилегированного пользователя.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "web/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
