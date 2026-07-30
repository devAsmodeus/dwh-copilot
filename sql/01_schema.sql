/*
    Демонстрационное хранилище данных.

    Базовые таблицы находятся в схеме dbo. Учётной записи, под которой работает
    приложение, доступ к ним не выдаётся: она видит только представления схемы
    mart. Права описаны в 03_security.sql.

    Данные формируются программно за период с 1 января по 28 июля 2026 года.
    Дата 28 июля выбрана как дата актуальности данных: относительные периоды
    вида "последние три месяца" отсчитываются от неё, а не от текущей даты.

    Объём данных подобран так, чтобы стенд разворачивался за минуту и при этом
    оценка стоимости запроса на рубеже 5 давала осмысленные значения.
*/

SET NOCOUNT ON;
GO

IF DB_ID('dwh_demo') IS NULL
    CREATE DATABASE dwh_demo;
GO

USE dwh_demo;
GO

IF SCHEMA_ID('mart') IS NULL EXEC('CREATE SCHEMA mart');
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Справочники
   ───────────────────────────────────────────────────────────────────────────── */

DROP TABLE IF EXISTS dbo.sales;
DROP TABLE IF EXISTS dbo.plan_fact;
DROP TABLE IF EXISTS dbo.purchases;
DROP TABLE IF EXISTS dbo.stock_daily;
DROP TABLE IF EXISTS dbo.stock_moves;
DROP TABLE IF EXISTS dbo.shipments;
DROP TABLE IF EXISTS dbo.receivables;
DROP TABLE IF EXISTS dbo.payments;
DROP TABLE IF EXISTS dbo.returns;
DROP TABLE IF EXISTS dbo.prices;
DROP TABLE IF EXISTS dbo.products;
DROP TABLE IF EXISTS dbo.clients;
DROP TABLE IF EXISTS dbo.suppliers;
GO

CREATE TABLE dbo.clients (
    client_id       int          NOT NULL PRIMARY KEY,
    client_name     nvarchar(200) NOT NULL,
    customer_region nvarchar(60)  NOT NULL,
    segment         nvarchar(40)  NOT NULL,
    is_active       bit           NOT NULL
);

CREATE TABLE dbo.products (
    product_id      int           NOT NULL PRIMARY KEY,
    product_name    nvarchar(200) NOT NULL,
    product_group   nvarchar(60)  NOT NULL,
    brand           nvarchar(60)  NOT NULL,
    is_discontinued bit           NOT NULL
);

CREATE TABLE dbo.suppliers (
    supplier_id        int           NOT NULL PRIMARY KEY,
    supplier_name      nvarchar(200) NOT NULL,
    country            nvarchar(60)  NOT NULL,
    payment_terms_days int           NOT NULL
);

CREATE TABLE dbo.prices (
    product_id     int           NOT NULL,
    price_category nvarchar(40)  NOT NULL,
    price_net      decimal(18,2) NOT NULL,
    valid_from     date          NOT NULL
);

/* ─────────────────────────────────────────────────────────────────────────────
   Таблицы фактов
   ───────────────────────────────────────────────────────────────────────────── */

CREATE TABLE dbo.sales (
    sale_id       int           NOT NULL IDENTITY PRIMARY KEY,
    report_date   date          NOT NULL,
    region        nvarchar(60)  NOT NULL,
    division      nvarchar(60)  NOT NULL,
    product_id    int           NOT NULL,
    client_id     int           NOT NULL,
    revenue_gross decimal(18,2) NOT NULL,
    vat_amount    decimal(18,2) NOT NULL,
    returns_amount decimal(18,2) NOT NULL,
    cost_amount   decimal(18,2) NOT NULL,
    qty           int           NOT NULL
);

CREATE TABLE dbo.plan_fact (
    report_month date          NOT NULL,
    division     nvarchar(60)  NOT NULL,
    revenue_plan decimal(18,2) NOT NULL
);

CREATE TABLE dbo.purchases (
    purchase_id     int           NOT NULL IDENTITY PRIMARY KEY,
    order_date      date          NOT NULL,
    receipt_date    date          NULL,
    supplier_id     int           NOT NULL,
    product_id      int           NOT NULL,
    purchase_amount decimal(18,2) NOT NULL,
    qty             int           NOT NULL
);

CREATE TABLE dbo.stock_daily (
    balance_date date          NOT NULL,
    warehouse    nvarchar(60)  NOT NULL,
    product_id   int           NOT NULL,
    qty_on_hand  int           NOT NULL,
    stock_value  decimal(18,2) NOT NULL
);

CREATE TABLE dbo.stock_moves (
    movement_date date         NOT NULL,
    warehouse     nvarchar(60) NOT NULL,
    movement_type nvarchar(40) NOT NULL,
    product_id    int          NOT NULL,
    qty           int          NOT NULL
);

CREATE TABLE dbo.shipments (
    shipment_id   int           NOT NULL IDENTITY PRIMARY KEY,
    shipment_date date          NOT NULL,
    delivery_date date          NULL,
    client_id     int           NOT NULL,
    carrier       nvarchar(60)  NOT NULL,
    is_late       bit           NOT NULL,
    shipment_cost decimal(18,2) NOT NULL
);

CREATE TABLE dbo.receivables (
    report_date  date          NOT NULL,
    client_id    int           NOT NULL,
    debt_amount  decimal(18,2) NOT NULL,
    days_overdue int           NOT NULL
);

CREATE TABLE dbo.payments (
    payment_id     int           NOT NULL IDENTITY PRIMARY KEY,
    payment_date   date          NOT NULL,
    client_id      int           NOT NULL,
    payment_amount decimal(18,2) NOT NULL,
    payment_method nvarchar(40)  NOT NULL
);

CREATE TABLE dbo.returns (
    return_id     int           NOT NULL IDENTITY PRIMARY KEY,
    return_date   date          NOT NULL,
    client_id     int           NOT NULL,
    product_id    int           NOT NULL,
    reason        nvarchar(80)  NOT NULL,
    return_amount decimal(18,2) NOT NULL,
    qty           int           NOT NULL
);
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Вспомогательная последовательность чисел.
   Нужна для программного формирования данных.
   ───────────────────────────────────────────────────────────────────────────── */

DROP TABLE IF EXISTS #numbers;
SELECT TOP (30000) n = ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1
INTO #numbers
FROM sys.all_objects a CROSS JOIN sys.all_objects b;
GO

DECLARE @start date = '2026-01-01';
DECLARE @end   date = '2026-07-28';
DECLARE @days  int  = DATEDIFF(day, @start, @end) + 1;

/* --- Справочник клиентов --------------------------------------------------- */

INSERT INTO dbo.clients (client_id, client_name, customer_region, segment, is_active)
SELECT
    n + 1,
    CONCAT(
        CHOOSE(n % 5 + 1, N'ООО', N'ЗАО', N'ИП', N'ОАО', N'УП'), N' ',
        CHOOSE(n % 12 + 1,
            N'Технохолдинг', N'Импульс', N'Вектор', N'Гранит', N'Аврора', N'Меридиан',
            N'Прогресс', N'Синтез', N'Атлант', N'Ориентир', N'Каскад', N'Форвард'),
        N'-', CAST(n + 1 AS nvarchar(10))),
    CHOOSE(n % 5 + 1, N'Москва', N'Северо-Запад', N'Урал', N'Сибирь', N'Юг'),
    CHOOSE(n % 4 + 1, N'Корпоративный', N'Малый бизнес', N'Государственный', N'Розница'),
    CASE WHEN n % 17 = 0 THEN 0 ELSE 1 END
FROM #numbers WHERE n < 60;

/* --- Справочник номенклатуры ----------------------------------------------- */

INSERT INTO dbo.products (product_id, product_name, product_group, brand, is_discontinued)
SELECT
    n + 1,
    CONCAT(
        CHOOSE(n % 4 + 1, N'Ноутбук', N'Сервер', N'Коммутатор', N'Клавиатура'), N' ',
        CHOOSE(n % 5 + 1, N'Lenovo', N'HP', N'Huawei', N'Kingston', N'Logitech'), N' ',
        N'модель ', CAST(1000 + n AS nvarchar(10))),
    CHOOSE(n % 4 + 1, N'Ноутбуки', N'Серверы', N'Сетевое оборудование', N'Периферия'),
    CHOOSE(n % 5 + 1, N'Lenovo', N'HP', N'Huawei', N'Kingston', N'Logitech'),
    CASE WHEN n % 23 = 0 THEN 1 ELSE 0 END
FROM #numbers WHERE n < 40;

/* --- Справочник поставщиков ------------------------------------------------ */

INSERT INTO dbo.suppliers (supplier_id, supplier_name, country, payment_terms_days)
SELECT
    n + 1,
    CONCAT(N'Поставщик ',
        CHOOSE(n % 6 + 1, N'Альфа', N'Бета', N'Гамма', N'Дельта', N'Омега', N'Сигма'),
        N'-', CAST(n + 1 AS nvarchar(10))),
    CHOOSE(n % 4 + 1, N'Беларусь', N'Россия', N'Китай', N'Польша'),
    CHOOSE(n % 4 + 1, 14, 30, 45, 60)
FROM #numbers WHERE n < 12;

/* --- Прайс-лист ------------------------------------------------------------ */

INSERT INTO dbo.prices (product_id, price_category, price_net, valid_from)
SELECT
    p.product_id,
    c.price_category,
    CAST(400 + (p.product_id * 37 % 2600) * c.factor AS decimal(18,2)),
    '2026-01-01'
FROM dbo.products p
CROSS JOIN (VALUES
    (N'Базовая', 1.00), (N'Дилерская', 0.88), (N'Проектная', 0.80)
) AS c(price_category, factor);

/* --- Продажи ---------------------------------------------------------------
   Формируется по 8 продаж на каждый день периода. Сумма зависит от группы
   товара и содержит сезонный рост, чтобы динамика по месяцам была видна.
   ------------------------------------------------------------------------- */

INSERT INTO dbo.sales
    (report_date, region, division, product_id, client_id,
     revenue_gross, vat_amount, returns_amount, cost_amount, qty)
SELECT
    d.report_date,
    CHOOSE((d.day_no * 7 + s.slot) % 5 + 1,
        N'Москва', N'Северо-Запад', N'Урал', N'Сибирь', N'Юг'),
    CHOOSE((d.day_no * 3 + s.slot) % 3 + 1,
        N'Розница', N'Корпоративные продажи', N'Электронная торговля'),
    (d.day_no * 11 + s.slot * 3) % 40 + 1,
    (d.day_no * 13 + s.slot * 7) % 60 + 1,
    CAST(base.amount * (1 + 0.04 * MONTH(d.report_date)) AS decimal(18,2)),
    CAST(base.amount * 0.20 AS decimal(18,2)),
    CASE WHEN (d.day_no + s.slot) % 19 = 0
         THEN CAST(base.amount * 0.10 AS decimal(18,2)) ELSE 0 END,
    CAST(base.amount * 0.72 AS decimal(18,2)),
    (d.day_no + s.slot) % 5 + 1
FROM (
    SELECT n AS day_no, DATEADD(day, n, @start) AS report_date
    FROM #numbers WHERE n < @days
) d
CROSS JOIN (SELECT n AS slot FROM #numbers WHERE n < 8) s
CROSS APPLY (
    SELECT amount = 900.0 + ((d.day_no * 17 + s.slot * 29) % 47) * 130.0
) base;

/* --- План продаж -----------------------------------------------------------
   План задаётся так, чтобы часть подразделений его не выполняла. Это нужно
   для проверки второго примера вопроса из технического задания.
   ------------------------------------------------------------------------- */

INSERT INTO dbo.plan_fact (report_month, division, revenue_plan)
SELECT
    m.report_month,
    dv.division,
    CAST(
        ISNULL((
            SELECT SUM(s.revenue_gross - s.vat_amount - s.returns_amount)
            FROM dbo.sales s
            WHERE s.division = dv.division
              AND s.report_date >= m.report_month
              AND s.report_date < DATEADD(month, 1, m.report_month)
        ), 0) * CASE dv.division
                    WHEN N'Розница'               THEN 1.18
                    WHEN N'Корпоративные продажи' THEN 0.95
                    ELSE 1.05
                END
        AS decimal(18,2))
FROM (
    SELECT DISTINCT DATEFROMPARTS(YEAR(report_date), MONTH(report_date), 1) AS report_month
    FROM dbo.sales
) m
CROSS JOIN (VALUES
    (N'Розница'), (N'Корпоративные продажи'), (N'Электронная торговля')
) AS dv(division);

/* --- Закупки --------------------------------------------------------------- */

INSERT INTO dbo.purchases
    (order_date, receipt_date, supplier_id, product_id, purchase_amount, qty)
SELECT
    DATEADD(day, n, @start),
    DATEADD(day, n + (n % 11) + 3, @start),
    n % 12 + 1,
    n % 40 + 1,
    CAST(4000.0 + (n * 53 % 60) * 420.0 AS decimal(18,2)),
    (n % 15) + 5
FROM #numbers WHERE n < @days;

/* --- Остатки на складах ---------------------------------------------------- */

INSERT INTO dbo.stock_daily (balance_date, warehouse, product_id, qty_on_hand, stock_value)
SELECT
    DATEADD(day, d.n, @start),
    w.warehouse,
    p.product_id,
    50 + (d.n * 7 + p.product_id * 3) % 400,
    CAST((50 + (d.n * 7 + p.product_id * 3) % 400) * 310.0 AS decimal(18,2))
FROM (SELECT n FROM #numbers WHERE n < @days AND n % 7 = 0) d
CROSS JOIN (VALUES
    (N'Центральный'), (N'Минск-1'), (N'Гомель'), (N'Транзитный')
) AS w(warehouse)
CROSS JOIN (SELECT product_id FROM dbo.products WHERE product_id % 4 = 1) p;

/* --- Движение товаров ------------------------------------------------------ */

INSERT INTO dbo.stock_moves (movement_date, warehouse, movement_type, product_id, qty)
SELECT
    DATEADD(day, d.n, @start),
    CHOOSE((d.n + t.slot) % 4 + 1,
        N'Центральный', N'Минск-1', N'Гомель', N'Транзитный'),
    CHOOSE((d.n * 3 + t.slot) % 4 + 1, N'Приход', N'Расход', N'Перемещение', N'Списание'),
    (d.n * 5 + t.slot) % 40 + 1,
    (d.n + t.slot) % 40 + 1
FROM (SELECT n FROM #numbers WHERE n < @days) d
CROSS JOIN (SELECT n AS slot FROM #numbers WHERE n < 3) t;

/* --- Отгрузки -------------------------------------------------------------- */

INSERT INTO dbo.shipments
    (shipment_date, delivery_date, client_id, carrier, is_late, shipment_cost)
SELECT
    DATEADD(day, d.n, @start),
    DATEADD(day, d.n + 1 + (d.n + s.slot) % 4, @start),
    (d.n * 13 + s.slot * 7) % 60 + 1,
    CHOOSE((d.n + s.slot) % 3 + 1,
        N'Собственный транспорт', N'СДЭК', N'Деловые линии'),
    CASE WHEN (d.n * 3 + s.slot) % 9 = 0 THEN 1 ELSE 0 END,
    CAST(60.0 + ((d.n + s.slot) % 23) * 14.0 AS decimal(18,2))
FROM (SELECT n FROM #numbers WHERE n < @days) d
CROSS JOIN (SELECT n AS slot FROM #numbers WHERE n < 4) s;

/* --- Дебиторская задолженность --------------------------------------------- */

INSERT INTO dbo.receivables (report_date, client_id, debt_amount, days_overdue)
SELECT
    @end,
    c.client_id,
    CAST(1500.0 + (c.client_id * 137 % 90) * 460.0 AS decimal(18,2)),
    CASE
        WHEN c.client_id % 5 = 0 THEN (c.client_id * 7) % 120 + 1
        ELSE 0
    END
FROM dbo.clients c;

/* --- Платежи --------------------------------------------------------------- */

INSERT INTO dbo.payments (payment_date, client_id, payment_amount, payment_method)
SELECT
    DATEADD(day, d.n, @start),
    (d.n * 17 + s.slot * 5) % 60 + 1,
    CAST(2200.0 + ((d.n * 31 + s.slot) % 55) * 380.0 AS decimal(18,2)),
    CHOOSE((d.n + s.slot) % 4 + 1,
        N'Банковский перевод', N'Карта', N'Наличные', N'Зачёт')
FROM (SELECT n FROM #numbers WHERE n < @days) d
CROSS JOIN (SELECT n AS slot FROM #numbers WHERE n < 2) s;

/* --- Возвраты -------------------------------------------------------------- */

INSERT INTO dbo.returns
    (return_date, client_id, product_id, reason, return_amount, qty)
SELECT
    DATEADD(day, n, @start),
    (n * 19) % 60 + 1,
    (n * 7) % 40 + 1,
    CHOOSE(n % 4 + 1, N'Брак', N'Пересорт', N'Отказ клиента', N'Нарушение сроков'),
    CAST(500.0 + (n * 23 % 40) * 175.0 AS decimal(18,2)),
    (n % 3) + 1
FROM #numbers WHERE n < @days AND n % 3 = 0;

DROP TABLE #numbers;
GO

PRINT 'Схема и данные созданы.';
GO
