-- Optional synthetic adapter for the demo WH_REQUISITION and WH_LOCATION tables.
-- Run after loading sql/01_schema.sql and sql/02_seed.sql. This view is not
-- installed by scripts/install_catalog.py, which never touches source data.

CREATE VIEW IF NOT EXISTS WH_EXAMPLE_FILLED_REQUISITIONS AS
SELECT r.FILL_DATE AS METRIC_DATE,
       TRIM(l.STATE) AS STATE,
       r.JOB_FAMILY AS JOB_FAMILY
FROM WH_REQUISITION r
JOIN WH_LOCATION l ON l.LOCATION_ID = r.LOCATION_ID
WHERE r.FILL_DATE IS NOT NULL;
