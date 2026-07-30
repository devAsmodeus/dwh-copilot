/*
    Права доступа приложения к хранилищу.

    Это последний рубеж защиты. Рубежи с первого по пятый находятся в коде
    приложения и теоретически обходятся ошибкой в этом коде. Права СУБД
    ошибкой в приложении не обходятся.

    Учётной записи dwh_reader выдаётся право чтения только на представления
    схемы mart. На базовые таблицы схемы dbo право явно отозвано инструкцией
    DENY, которая имеет приоритет над любым разрешением, полученным через
    членство в ролях.

    Проверка правильности настройки находится в конце файла. Она выполняет
    заведомо запрещённое обращение и обязана завершиться ошибкой. Если запрос
    отработал успешно, настройка неверна.
*/

USE dwh_demo;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Учётная запись только для чтения
   ───────────────────────────────────────────────────────────────────────────── */

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'dwh_reader')
BEGIN
    -- Пароль в рабочей установке задаётся при развёртывании и хранится
    -- в системе управления секретами, а не в этом файле.
    CREATE LOGIN dwh_reader
        WITH PASSWORD = 'Reader_Str0ng_P@ss!',
             CHECK_POLICY = ON,
             DEFAULT_DATABASE = dwh_demo;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'dwh_reader')
    CREATE USER dwh_reader FOR LOGIN dwh_reader;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Разрешения
   ───────────────────────────────────────────────────────────────────────────── */

-- Чтение всех представлений схемы mart. Добавление новой витрины в схему
-- автоматически делает её доступной, отдельной выдачи прав не требуется.
GRANT SELECT ON SCHEMA::mart TO dwh_reader;

-- Право на просмотр плана выполнения. Нужно для рубежа 5: оценка стоимости
-- запроса выполняется инструкцией SET SHOWPLAN_XML ON от имени того же
-- пользователя, что и сам запрос.
GRANT SHOWPLAN TO dwh_reader;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Запреты
   ───────────────────────────────────────────────────────────────────────────── */

-- Базовые таблицы недоступны. Инструкция DENY имеет приоритет над GRANT,
-- в том числе полученным через роли, поэтому запрет не снимается случайной
-- выдачей членства в db_datareader.
DENY SELECT ON SCHEMA::dbo TO dwh_reader;

-- Изменение данных запрещено на уровне СУБД дополнительно к проверкам
-- приложения. Совпадение двух независимых механизмов является намеренным.
DENY INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::mart TO dwh_reader;
DENY INSERT, UPDATE, DELETE, EXECUTE ON SCHEMA::dbo  TO dwh_reader;
GO

-- Просмотр состава объектов сервера. Без этого запрета пользователь получает
-- перечень всех баз и таблиц, включая закрытые схемы.
--
-- Разрешение относится к области сервера, поэтому выдаётся в базе master
-- и назначается имени входа, а не пользователю базы данных. Попытка выполнить
-- эту инструкцию в пользовательской базе завершается ошибкой 4621.
USE master;
GO
DENY VIEW ANY DEFINITION TO dwh_reader;
GO
USE dwh_demo;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Ограничение потребления ресурсов.

   Отдельный пул ограничивает долю процессорного времени и памяти, доступную
   аналитическим запросам. Без него один тяжёлый запрос замедляет ночную
   загрузку данных.
   ───────────────────────────────────────────────────────────────────────────── */

USE master;
GO

IF NOT EXISTS (SELECT 1 FROM sys.resource_governor_resource_pools WHERE name = 'pool_analytics')
    CREATE RESOURCE POOL pool_analytics
        WITH (MAX_CPU_PERCENT = 40, MAX_MEMORY_PERCENT = 30);
GO

IF NOT EXISTS (SELECT 1 FROM sys.resource_governor_workload_groups WHERE name = 'grp_analytics')
    CREATE WORKLOAD GROUP grp_analytics
        WITH (
            -- Предельное время выполнения на стороне СУБД. Действует
            -- независимо от ограничения времени в приложении.
            REQUEST_MAX_CPU_TIME_SEC = 30,
            MAX_DOP = 2,
            GROUP_MAX_REQUESTS = 10
        )
        USING pool_analytics;
GO

-- Функцию, подключённую к регулятору ресурсов в качестве классификатора,
-- изменить нельзя. Поэтому перед изменением она открепляется. Без этого шага
-- повторный запуск скрипта завершается ошибкой 10920, то есть скрипт
-- перестаёт быть повторяемым.
ALTER RESOURCE GOVERNOR WITH (CLASSIFIER_FUNCTION = NULL);
ALTER RESOURCE GOVERNOR RECONFIGURE;
GO

CREATE OR ALTER FUNCTION dbo.fn_route_analytics()
RETURNS sysname WITH SCHEMABINDING
AS
BEGIN
    IF SUSER_SNAME() = 'dwh_reader'
        RETURN 'grp_analytics';
    RETURN 'default';
END
GO

ALTER RESOURCE GOVERNOR WITH (CLASSIFIER_FUNCTION = dbo.fn_route_analytics);
ALTER RESOURCE GOVERNOR RECONFIGURE;
GO

/* ─────────────────────────────────────────────────────────────────────────────
   Проверка настройки.

   Обращение к базовой таблице обязано завершиться ошибкой. Успешное выполнение
   означает неверную настройку прав и требует остановки развёртывания.
   ───────────────────────────────────────────────────────────────────────────── */

USE dwh_demo;
GO

BEGIN TRY
    EXECUTE AS USER = 'dwh_reader';
    DECLARE @probe int;
    SELECT TOP 1 @probe = 1 FROM dbo.sales;
    REVERT;
    RAISERROR('Настройка прав неверна: базовые таблицы доступны для чтения.', 16, 1);
END TRY
BEGIN CATCH
    IF SESSION_USER = 'dwh_reader' REVERT;
    IF ERROR_NUMBER() = 50000 THROW;
    PRINT 'Проверка пройдена: доступ к базовым таблицам закрыт.';
END CATCH
GO

BEGIN TRY
    EXECUTE AS USER = 'dwh_reader';
    DECLARE @rows int;
    SELECT TOP 1 @rows = 1 FROM mart.v_sales;
    REVERT;
    PRINT 'Проверка пройдена: витрины схемы mart доступны для чтения.';
END TRY
BEGIN CATCH
    IF SESSION_USER = 'dwh_reader' REVERT;
    RAISERROR('Настройка прав неверна: витрины схемы mart недоступны.', 16, 1);
END CATCH
GO
