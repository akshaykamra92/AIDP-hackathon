-- Optional synthetic adapter for the demo WH_REQUISITION and WH_LOCATION tables.
-- Run after loading sql/01_schema.sql, sql/02_seed.sql, and installing the
-- security tables. It also checks the Oracle CLIENT_IDENTIFIER set by the
-- authenticated app query; a session without that identity sees no rows.
-- This optional example is not installed by scripts/install_catalog.py.

CREATE OR REPLACE VIEW WH_EXAMPLE_FILLED_REQUISITIONS AS
SELECT r.FILL_DATE AS METRIC_DATE,
       TRIM(TO_CHAR(r.LOCATION_ID)) AS LOCATION_KEY,
       TRIM(l.STATE) AS STATE,
       r.JOB_FAMILY AS JOB_FAMILY
FROM WH_REQUISITION r
JOIN WH_LOCATION l ON l.LOCATION_ID = r.LOCATION_ID
WHERE r.FILL_DATE IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM WH_USER_LOCATION acl
      JOIN WH_SEC_LOCATION loc ON loc.LOCATION_KEY = acl.LOCATION_KEY
      JOIN WH_APP_USER usr ON usr.USER_ID = acl.USER_ID
      WHERE acl.USER_ID = SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')
        AND usr.STATUS = 'ACTIVE'
        AND acl.STATUS = 'ACTIVE'
        AND loc.STATUS = 'ACTIVE'
        AND acl.LOCATION_KEY = TRIM(TO_CHAR(r.LOCATION_ID))
  );
