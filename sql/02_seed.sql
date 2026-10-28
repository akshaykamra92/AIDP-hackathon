-- Ask the Warehouse: deterministic synthetic data and certified metric catalog.
-- Run after 01_schema.sql. Rerunning this script replaces WH_ seed rows.
-- Dates cover 18 completed month-end snapshots, April 2025 through September 2026.

DELETE FROM WH_ANSWER_LOG;
DELETE FROM WH_METRIC_REQUEST;
DELETE FROM WH_METRIC;
DELETE FROM WH_REQUISITION;
DELETE FROM WH_TERMINATION;
DELETE FROM WH_HEADCOUNT_MONTH;
DELETE FROM WH_EMPLOYEE;
DELETE FROM WH_LOCATION;

INSERT INTO WH_LOCATION (LOCATION_ID, NAME, CITY, STATE, REGION)
SELECT 1, 'Orlando Rehab Center', 'Orlando', 'FL', 'SOUTHEAST' FROM DUAL
UNION ALL SELECT 2, 'Tampa Rehab Center', 'Tampa', 'FL', 'SOUTHEAST' FROM DUAL
UNION ALL SELECT 3, 'Jacksonville Rehab Center', 'Jacksonville', 'FL', 'SOUTHEAST' FROM DUAL
UNION ALL SELECT 4, 'Birmingham Rehab Center', 'Birmingham', 'AL', 'SOUTHEAST' FROM DUAL
UNION ALL SELECT 5, 'Dallas Rehab Center', 'Dallas', 'TX', 'SOUTH' FROM DUAL
UNION ALL SELECT 6, 'Atlanta Rehab Center', 'Atlanta', 'GA', 'SOUTHEAST' FROM DUAL;

INSERT INTO WH_EMPLOYEE (EMP_ID, LOCATION_ID, JOB_FAMILY, HIRE_DATE, STATUS)
SELECT n.EMP_ID,
       MOD(n.EMP_ID - 1, 6) + 1,
       CASE MOD(TRUNC((n.EMP_ID - 1) / 6), 4)
           WHEN 0 THEN 'NURSING'
           WHEN 1 THEN 'THERAPY'
           WHEN 2 THEN 'CLINICAL_SUPPORT'
           ELSE 'OPERATIONS'
       END,
       DATE '2022-01-01' + MOD(n.EMP_ID * 13, 850),
       'ACTIVE'
FROM (SELECT LEVEL AS EMP_ID FROM DUAL CONNECT BY LEVEL <= 2000) n;

-- Event-time location differs from current location for internal transfers.
-- The Florida transfer predicate gives exactly 24 of 120 Florida events overall.
INSERT INTO WH_TERMINATION (TERM_ID, EMP_ID, TERM_DATE, TERM_TYPE, LOCATION_ID, JOB_FAMILY)
SELECT t.EMP_ID,
       t.EMP_ID,
       ADD_MONTHS(DATE '2025-04-01',
                  MOD(TRUNC((t.EMP_ID - 1) / 6) * 5 + t.CURRENT_LOCATION_ID - 1, 18))
                  + MOD(t.EMP_ID * 7, 27),
       t.TERM_TYPE,
       CASE
           WHEN t.TERM_TYPE = 'INTERNAL_TRANSFER' AND t.CURRENT_LOCATION_ID <= 3
               THEN MOD(t.CURRENT_LOCATION_ID, 3) + 1
           WHEN t.TERM_TYPE = 'INTERNAL_TRANSFER' AND t.CURRENT_LOCATION_ID > 3
               THEN MOD(t.CURRENT_LOCATION_ID - 3, 3) + 4
           ELSE t.CURRENT_LOCATION_ID
       END,
       t.JOB_FAMILY
FROM (
    SELECT e.EMP_ID,
           e.LOCATION_ID AS CURRENT_LOCATION_ID,
           e.JOB_FAMILY,
           CASE
               WHEN e.LOCATION_ID <= 3
                    AND MOD(3 * TRUNC((e.EMP_ID - 1) / 6) + e.LOCATION_ID + 1, 5) = 0
                   THEN 'INTERNAL_TRANSFER'
               WHEN e.LOCATION_ID > 3 AND MOD(e.EMP_ID, 13) = 0
                   THEN 'INTERNAL_TRANSFER'
               WHEN MOD(e.EMP_ID, 7) IN (0, 1, 2, 3)
                   THEN 'VOLUNTARY'
               WHEN MOD(e.EMP_ID, 11) = 0
                   THEN 'RETIREMENT'
               ELSE 'INVOLUNTARY'
           END AS TERM_TYPE
    FROM WH_EMPLOYEE e
    WHERE e.EMP_ID <= 240
) t;

UPDATE WH_EMPLOYEE e
SET STATUS = 'TERMINATED'
WHERE EXISTS (
    SELECT 1
    FROM WH_TERMINATION t
    WHERE t.EMP_ID = e.EMP_ID
      AND t.TERM_TYPE <> 'INTERNAL_TRANSFER'
);

-- Every location/job combination has one completed month-end snapshot per month.
INSERT INTO WH_HEADCOUNT_MONTH (MONTH_START, LOCATION_ID, JOB_FAMILY, HEADCOUNT)
SELECT ADD_MONTHS(DATE '2025-04-01', m.MONTH_INDEX),
       l.LOCATION_ID,
       j.JOB_FAMILY,
       j.BASE_COUNT
           + MOD(l.LOCATION_ID * 7, 13) - 6
           + TRUNC(m.MONTH_INDEX / 6)
           + MOD(m.MONTH_INDEX + l.LOCATION_ID * 3 + j.JOB_INDEX * 3, 5) - 2
FROM (SELECT LEVEL - 1 AS MONTH_INDEX FROM DUAL CONNECT BY LEVEL <= 18) m
CROSS JOIN WH_LOCATION l
CROSS JOIN (
    SELECT 0 AS JOB_INDEX, 'NURSING' AS JOB_FAMILY, 125 AS BASE_COUNT FROM DUAL
    UNION ALL SELECT 1, 'THERAPY', 85 FROM DUAL
    UNION ALL SELECT 2, 'CLINICAL_SUPPORT', 55 FROM DUAL
    UNION ALL SELECT 3, 'OPERATIONS', 30 FROM DUAL
) j;

-- FILL_DATE, rather than OPEN_DATE, determines the period for filled metrics.
INSERT INTO WH_REQUISITION (REQ_ID, LOCATION_ID, JOB_FAMILY, OPEN_DATE, FILL_DATE, FILLED_INTERNALLY)
SELECT r.REQ_ID,
       r.LOCATION_ID,
       r.JOB_FAMILY,
       r.OPEN_DATE,
       CASE WHEN MOD(r.REQ_ID, 5) = 0
            THEN NULL
            ELSE r.OPEN_DATE + 7 + MOD(r.REQ_ID * 11, 30)
       END,
       CASE WHEN MOD(r.REQ_ID, 5) = 0 THEN NULL
            WHEN MOD(r.REQ_ID, 4) = 0 THEN 'Y'
            ELSE 'N'
       END
FROM (
    SELECT n.REQ_ID,
           MOD(n.REQ_ID - 1, 6) + 1 AS LOCATION_ID,
           CASE MOD(TRUNC((n.REQ_ID - 1) / 6), 4)
               WHEN 0 THEN 'NURSING'
               WHEN 1 THEN 'THERAPY'
               WHEN 2 THEN 'CLINICAL_SUPPORT'
               ELSE 'OPERATIONS'
           END AS JOB_FAMILY,
           ADD_MONTHS(DATE '2025-04-01', MOD(TRUNC((n.REQ_ID - 1) / 6), 17))
               + MOD(n.REQ_ID * 3, 15) AS OPEN_DATE
    FROM (SELECT LEVEL AS REQ_ID FROM DUAL CONNECT BY LEVEL <= 360) n
) r;

-- SQL_TEMPLATE is deliberately a marker, not executable SQL. The application
-- maps each approved METRIC_ID to reviewed, bind-based SQL in its compiler.
INSERT INTO WH_METRIC
    (METRIC_ID, NAME, VERSION, OWNER, STATUS, DEFINITION_TEXT,
     SQL_TEMPLATE, ALLOWED_DIMS, ALLOWED_FILTERS, GRAIN)
VALUES
    ('HEADCOUNT', 'Month-end headcount', 1, 'People Analytics', 'CERTIFIED',
     'Number of people at the end of the latest completed month in the selected period. By month or quarter, use each group''s final completed month. Unit: people.',
     'HEADCOUNT',
     '["state","location","job_family","month","quarter"]',
     '["state","location","job_family","month","quarter","period"]',
     'MONTH_END');

INSERT INTO WH_METRIC
    (METRIC_ID, NAME, VERSION, OWNER, STATUS, DEFINITION_TEXT,
     SQL_TEMPLATE, ALLOWED_DIMS, ALLOWED_FILTERS, GRAIN)
VALUES
    ('TURNOVER_RATE', 'Turnover rate', 1, 'People Analytics', 'CERTIFIED',
     '100 times VOLUNTARY, INVOLUNTARY, and RETIREMENT events in the selected period divided by average monthly month-end headcount. INTERNAL_TRANSFER is excluded. Use event-time location and job family. This period rate is not annualized. Unit: percent.',
     'TURNOVER_RATE',
     '["state","location","job_family","month","quarter"]',
     '["state","location","job_family","month","quarter","period"]',
     'PERIOD');

INSERT INTO WH_METRIC
    (METRIC_ID, NAME, VERSION, OWNER, STATUS, DEFINITION_TEXT,
     SQL_TEMPLATE, ALLOWED_DIMS, ALLOWED_FILTERS, GRAIN)
VALUES
    ('VOLUNTARY_TURNOVER_RATE', 'Voluntary turnover rate', 1, 'People Analytics', 'CERTIFIED',
     '100 times VOLUNTARY events in the selected period divided by average monthly month-end headcount. Use event-time location and job family. This period rate is not annualized. Unit: percent.',
     'VOLUNTARY_TURNOVER_RATE',
     '["state","location","job_family","month","quarter"]',
     '["state","location","job_family","month","quarter","period"]',
     'PERIOD');

INSERT INTO WH_METRIC
    (METRIC_ID, NAME, VERSION, OWNER, STATUS, DEFINITION_TEXT,
     SQL_TEMPLATE, ALLOWED_DIMS, ALLOWED_FILTERS, GRAIN)
VALUES
    ('TIME_TO_FILL_DAYS', 'Time to fill', 1, 'Talent Acquisition', 'CERTIFIED',
     'Median calendar days from OPEN_DATE to FILL_DATE for requisitions filled in the selected period. Unit: days.',
     'TIME_TO_FILL_DAYS',
     '["state","location","job_family","month","quarter"]',
     '["state","location","job_family","month","quarter","period"]',
     'FILLED_REQUISITION');

INSERT INTO WH_METRIC
    (METRIC_ID, NAME, VERSION, OWNER, STATUS, DEFINITION_TEXT,
     SQL_TEMPLATE, ALLOWED_DIMS, ALLOWED_FILTERS, GRAIN)
VALUES
    ('INTERNAL_FILL_RATE', 'Internal fill rate', 1, 'Talent Acquisition', 'CERTIFIED',
     '100 times internally filled requisitions (FILLED_INTERNALLY = Y) divided by all requisitions filled in the selected period. Unit: percent.',
     'INTERNAL_FILL_RATE',
     '["state","location","job_family","month","quarter"]',
     '["state","location","job_family","month","quarter","period"]',
     'FILLED_REQUISITION');

COMMIT;
