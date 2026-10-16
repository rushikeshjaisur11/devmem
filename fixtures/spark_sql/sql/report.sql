WITH recent AS (SELECT * FROM mart.sales_daily WHERE day > '2026-01-01')
INSERT INTO mart.sales_report SELECT r.day, c.name FROM recent r JOIN dim.customers c ON r.cid = c.id;
