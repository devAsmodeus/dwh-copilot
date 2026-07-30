/*
    Витрины схемы mart.

    Это семантический слой системы. Здесь и только здесь закреплено, как
    считаются бизнес-показатели. Языковая модель обращается к готовой колонке
    revenue_net и не решает самостоятельно, вычитать ли налог на добавленную
    стоимость и возвраты.

    Такое размещение бизнес-логики решает три задачи одновременно.

    Первая: определение показателя становится единственным. Расхождение
    "выручка в одном отчёте не сходится с выручкой в другом" невозможно
    по построению.

    Вторая: белый список разрешённых объектов перестаёт быть кодом приложения
    и становится системой прав СУБД. Ошибка в проверке запросов не открывает
    доступ к базовым таблицам, поскольку прав на них нет.

    Третья: запросы, которые формирует модель, становятся проще. Локальная
    модель уверенно пишет группировку по одной витрине и заметно чаще ошибается
    в соединении четырёх таблиц. Сложность перенесена в SQL, написанный
    и проверенный человеком.

    Состав колонок каждой витрины обязан совпадать с манифестом config/views.yaml.
    Расхождение обнаруживается ночной сверкой и приводит к выводу витрины
    из белого списка до ручной вычитки.
*/

USE dwh_demo;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Продажи
   ───────────────────────────────────────────────────────────────────────────── */

CREATE OR ALTER VIEW mart.v_sales AS
SELECT
    s.report_date,
    s.region,
    s.division,
    p.product_group,
    s.client_id,
    -- Единственное принятое определение выручки: без налога на добавленную
    -- стоимость и за вычетом возвратов. Управленческое решение, закреплённое
    -- в коде витрины.
    CAST(s.revenue_gross - s.vat_amount - s.returns_amount AS decimal(18,2)) AS revenue_net,
    s.qty
FROM dbo.sales s
JOIN dbo.products p ON p.product_id = s.product_id;
GO

CREATE OR ALTER VIEW mart.v_sales_plan_fact AS
SELECT
    pf.report_month,
    pf.division,
    pf.revenue_plan,
    CAST(ISNULL(f.revenue_fact, 0) AS decimal(18,2)) AS revenue_fact
FROM dbo.plan_fact pf
OUTER APPLY (
    -- Факт считается тем же способом, что и revenue_net в mart.v_sales.
    -- Сведение плана и факта в одной витрине избавляет модель от соединения
    -- двух источников: второй пример вопроса из технического задания
    -- решается группировкой по одной витрине.
    SELECT SUM(s.revenue_gross - s.vat_amount - s.returns_amount) AS revenue_fact
    FROM dbo.sales s
    WHERE s.division = pf.division
      AND s.report_date >= pf.report_month
      AND s.report_date < DATEADD(month, 1, pf.report_month)
) f;
GO

CREATE OR ALTER VIEW mart.v_margin AS
SELECT
    s.report_date,
    p.product_group,
    s.client_id,
    CAST(s.revenue_gross - s.vat_amount - s.returns_amount AS decimal(18,2)) AS revenue_net,
    s.cost_amount,
    CAST(s.revenue_gross - s.vat_amount - s.returns_amount - s.cost_amount
         AS decimal(18,2)) AS margin_amount
FROM dbo.sales s
JOIN dbo.products p ON p.product_id = s.product_id;
GO

CREATE OR ALTER VIEW mart.v_returns AS
SELECT
    r.return_date,
    r.client_id,
    p.product_group,
    r.reason,
    r.return_amount,
    r.qty
FROM dbo.returns r
JOIN dbo.products p ON p.product_id = r.product_id;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Клиенты, номенклатура, цены
   ───────────────────────────────────────────────────────────────────────────── */

CREATE OR ALTER VIEW mart.v_clients AS
SELECT
    client_id,
    client_name,
    customer_region,
    segment,
    is_active
FROM dbo.clients;
GO

CREATE OR ALTER VIEW mart.v_products AS
SELECT
    product_id,
    product_name,
    product_group,
    brand,
    is_discontinued
FROM dbo.products;
GO

CREATE OR ALTER VIEW mart.v_price_list AS
SELECT
    product_id,
    price_category,
    price_net,
    valid_from
FROM dbo.prices;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Закупки
   ───────────────────────────────────────────────────────────────────────────── */

CREATE OR ALTER VIEW mart.v_purchases AS
SELECT
    pu.order_date,
    pu.receipt_date,
    pu.supplier_id,
    p.product_group,
    pu.purchase_amount,
    pu.qty
FROM dbo.purchases pu
JOIN dbo.products p ON p.product_id = pu.product_id;
GO

CREATE OR ALTER VIEW mart.v_suppliers AS
SELECT
    supplier_id,
    supplier_name,
    country,
    payment_terms_days
FROM dbo.suppliers;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Склад и логистика
   ───────────────────────────────────────────────────────────────────────────── */

CREATE OR ALTER VIEW mart.v_stock_balance AS
SELECT
    sd.balance_date,
    sd.warehouse,
    p.product_group,
    sd.qty_on_hand,
    sd.stock_value
FROM dbo.stock_daily sd
JOIN dbo.products p ON p.product_id = sd.product_id;
GO

CREATE OR ALTER VIEW mart.v_stock_movements AS
SELECT
    sm.movement_date,
    sm.warehouse,
    sm.movement_type,
    p.product_group,
    sm.qty
FROM dbo.stock_moves sm
JOIN dbo.products p ON p.product_id = sm.product_id;
GO

CREATE OR ALTER VIEW mart.v_shipments AS
SELECT
    shipment_date,
    delivery_date,
    client_id,
    carrier,
    is_late,
    shipment_cost
FROM dbo.shipments;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Финансы
   ───────────────────────────────────────────────────────────────────────────── */

CREATE OR ALTER VIEW mart.v_receivables AS
SELECT
    r.report_date,
    r.client_id,
    r.debt_amount,
    r.days_overdue,
    -- Интервалы просрочки заданы здесь, а не в запросе модели. Иначе разные
    -- ответы на один вопрос делили бы задолженность по разным границам.
    CASE
        WHEN r.days_overdue = 0  THEN N'Без просрочки'
        WHEN r.days_overdue <= 30 THEN N'1-30 дней'
        WHEN r.days_overdue <= 90 THEN N'31-90 дней'
        ELSE N'Более 90 дней'
    END AS overdue_bucket
FROM dbo.receivables r;
GO

CREATE OR ALTER VIEW mart.v_payments AS
SELECT
    payment_date,
    client_id,
    payment_amount,
    payment_method
FROM dbo.payments;
GO

PRINT 'Витрины схемы mart созданы.';
GO
