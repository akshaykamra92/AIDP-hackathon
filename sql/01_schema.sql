-- Ask the Warehouse: Oracle AI Database 26ai schema.
-- Run as a script in a FreeSQL worksheet (or SQLcl). Rerunning resets only WH_ tables.

DROP TABLE IF EXISTS WH_ANSWER_LOG PURGE;
DROP TABLE IF EXISTS WH_METRIC_REQUEST PURGE;
DROP TABLE IF EXISTS WH_METRIC PURGE;
DROP TABLE IF EXISTS WH_REQUISITION PURGE;
DROP TABLE IF EXISTS WH_TERMINATION PURGE;
DROP TABLE IF EXISTS WH_HEADCOUNT_MONTH PURGE;
DROP TABLE IF EXISTS WH_EMPLOYEE PURGE;
DROP TABLE IF EXISTS WH_LOCATION PURGE;

CREATE TABLE WH_LOCATION (
    LOCATION_ID NUMBER(5) CONSTRAINT WH_LOCATION_PK PRIMARY KEY,
    NAME        VARCHAR2(120) NOT NULL,
    CITY        VARCHAR2(80) NOT NULL,
    STATE       CHAR(2) NOT NULL
                ANNOTATIONS (DESCRIPTION 'US postal state code', ALIASES 'state, state code'),
    REGION      VARCHAR2(30) NOT NULL,
    CONSTRAINT WH_LOCATION_STATE_CK CHECK (STATE IN ('FL', 'AL', 'TX', 'GA'))
) ANNOTATIONS (
    DESCRIPTION 'One row per synthetic care location. Join state through this table.',
    ALIASES 'facility, hospital, site, location',
    BUSINESS_TERM 'Care location',
    SYNONYMS 'facility, hospital, site'
);

CREATE TABLE WH_EMPLOYEE (
    EMP_ID      NUMBER(10) CONSTRAINT WH_EMPLOYEE_PK PRIMARY KEY,
    LOCATION_ID NUMBER(5) NOT NULL,
    JOB_FAMILY  VARCHAR2(30) NOT NULL
                ANNOTATIONS (DESCRIPTION 'Current broad job category', ALIASES 'role, discipline'),
    HIRE_DATE   DATE NOT NULL,
    STATUS      VARCHAR2(20) NOT NULL,
    CONSTRAINT WH_EMPLOYEE_LOCATION_FK FOREIGN KEY (LOCATION_ID) REFERENCES WH_LOCATION (LOCATION_ID),
    CONSTRAINT WH_EMPLOYEE_JOB_CK CHECK (JOB_FAMILY IN ('NURSING', 'THERAPY', 'CLINICAL_SUPPORT', 'OPERATIONS')),
    CONSTRAINT WH_EMPLOYEE_STATUS_CK CHECK (STATUS IN ('ACTIVE', 'TERMINATED'))
) ANNOTATIONS (
    DESCRIPTION 'Synthetic employee roster. LOCATION_ID and JOB_FAMILY are current attributes, not historical event attribution.',
    ALIASES 'worker, staff, employee roster',
    BUSINESS_TERM 'Employee',
    SYNONYMS 'worker, staff member'
);

CREATE TABLE WH_TERMINATION (
    TERM_ID      NUMBER(10) CONSTRAINT WH_TERMINATION_PK PRIMARY KEY,
    EMP_ID       NUMBER(10) NOT NULL,
    TERM_DATE    DATE NOT NULL,
    TERM_TYPE    VARCHAR2(30) NOT NULL
                 ANNOTATIONS (DESCRIPTION 'Event category: voluntary, involuntary, retirement, or internal transfer', ALIASES 'exit type, termination reason'),
    LOCATION_ID  NUMBER(5) NOT NULL,
    JOB_FAMILY   VARCHAR2(30) NOT NULL,
    CONSTRAINT WH_TERMINATION_EMP_FK FOREIGN KEY (EMP_ID) REFERENCES WH_EMPLOYEE (EMP_ID),
    CONSTRAINT WH_TERMINATION_LOCATION_FK FOREIGN KEY (LOCATION_ID) REFERENCES WH_LOCATION (LOCATION_ID),
    CONSTRAINT WH_TERMINATION_EMP_UK UNIQUE (EMP_ID),
    CONSTRAINT WH_TERMINATION_TYPE_CK CHECK (TERM_TYPE IN ('VOLUNTARY', 'INVOLUNTARY', 'RETIREMENT', 'INTERNAL_TRANSFER')),
    CONSTRAINT WH_TERMINATION_JOB_CK CHECK (JOB_FAMILY IN ('NURSING', 'THERAPY', 'CLINICAL_SUPPORT', 'OPERATIONS'))
) ANNOTATIONS (
    DESCRIPTION 'One synthetic employment event per employee. LOCATION_ID and JOB_FAMILY are event-time snapshots; do not reattribute events through the current employee roster.',
    ALIASES 'separation, exit, transfer event',
    BUSINESS_TERM 'Employment event',
    SYNONYMS 'termination, separation, transfer'
);

CREATE TABLE WH_HEADCOUNT_MONTH (
    MONTH_START DATE NOT NULL
                ANNOTATIONS (DESCRIPTION 'First day of a completed calendar month; HEADCOUNT is measured at that month end', ALIASES 'snapshot month, as-of month'),
    LOCATION_ID NUMBER(5) NOT NULL,
    JOB_FAMILY  VARCHAR2(30) NOT NULL,
    HEADCOUNT   NUMBER(10) NOT NULL,
    CONSTRAINT WH_HEADCOUNT_MONTH_PK PRIMARY KEY (MONTH_START, LOCATION_ID, JOB_FAMILY),
    CONSTRAINT WH_HEADCOUNT_LOCATION_FK FOREIGN KEY (LOCATION_ID) REFERENCES WH_LOCATION (LOCATION_ID),
    CONSTRAINT WH_HEADCOUNT_JOB_CK CHECK (JOB_FAMILY IN ('NURSING', 'THERAPY', 'CLINICAL_SUPPORT', 'OPERATIONS')),
    CONSTRAINT WH_HEADCOUNT_NONNEG_CK CHECK (HEADCOUNT >= 0)
) ANNOTATIONS (
    DESCRIPTION 'Completed calendar month-end headcount by care location and job family. Sum across selected locations and jobs for each month before averaging across months.',
    ALIASES 'staff census, workforce snapshot, monthly headcount',
    BUSINESS_TERM 'Month-end headcount',
    SYNONYMS 'staffing, employee count, workforce size'
);

CREATE TABLE WH_REQUISITION (
    REQ_ID             NUMBER(10) CONSTRAINT WH_REQUISITION_PK PRIMARY KEY,
    LOCATION_ID        NUMBER(5) NOT NULL,
    JOB_FAMILY         VARCHAR2(30) NOT NULL,
    OPEN_DATE          DATE NOT NULL,
    FILL_DATE          DATE,
    FILLED_INTERNALLY  CHAR(1)
                       ANNOTATIONS (DESCRIPTION 'Y means a filled requisition was filled by an internal candidate', ALIASES 'internal hire, internal fill'),
    CONSTRAINT WH_REQUISITION_LOCATION_FK FOREIGN KEY (LOCATION_ID) REFERENCES WH_LOCATION (LOCATION_ID),
    CONSTRAINT WH_REQUISITION_JOB_CK CHECK (JOB_FAMILY IN ('NURSING', 'THERAPY', 'CLINICAL_SUPPORT', 'OPERATIONS')),
    CONSTRAINT WH_REQUISITION_INTERNAL_CK CHECK (FILLED_INTERNALLY IN ('Y', 'N')),
    CONSTRAINT WH_REQUISITION_DATES_CK CHECK (FILL_DATE IS NULL OR FILL_DATE >= OPEN_DATE),
    CONSTRAINT WH_REQUISITION_FILL_CK CHECK (
        (FILL_DATE IS NULL AND FILLED_INTERNALLY IS NULL)
        OR (FILL_DATE IS NOT NULL AND FILLED_INTERNALLY IS NOT NULL)
    )
) ANNOTATIONS (
    DESCRIPTION 'Synthetic staffing requisitions. Filled metrics are attributed to FILL_DATE and to the requisition location and job family.',
    ALIASES 'job opening, hiring requisition, vacancy',
    BUSINESS_TERM 'Requisition',
    SYNONYMS 'open role, vacancy, position'
);

CREATE TABLE WH_METRIC (
    METRIC_ID       VARCHAR2(40) CONSTRAINT WH_METRIC_PK PRIMARY KEY,
    NAME            VARCHAR2(120) NOT NULL,
    VERSION         NUMBER(5) NOT NULL,
    OWNER           VARCHAR2(120) NOT NULL,
    STATUS          VARCHAR2(20) NOT NULL,
    DEFINITION_TEXT VARCHAR2(2000) NOT NULL
                    ANNOTATIONS (DESCRIPTION 'Approved business definition and unit of the metric', ALIASES 'metric definition, calculation rule'),
    SQL_TEMPLATE    CLOB NOT NULL,
    ALLOWED_DIMS    VARCHAR2(1000) NOT NULL,
    ALLOWED_FILTERS VARCHAR2(1000) NOT NULL,
    GRAIN           VARCHAR2(30) NOT NULL,
    CONSTRAINT WH_METRIC_STATUS_CK CHECK (STATUS IN ('CERTIFIED', 'DRAFT')),
    CONSTRAINT WH_METRIC_VERSION_CK CHECK (VERSION >= 1)
) ANNOTATIONS (
    DESCRIPTION 'Governed metric catalog. SQL_TEMPLATE is an approved metric ID marker; the application compiles fixed reviewed SQL for that ID.',
    ALIASES 'KPI catalog, certified measure, metric registry',
    BUSINESS_TERM 'Certified metric',
    SYNONYMS 'KPI, measure, calculation'
);

CREATE TABLE WH_METRIC_REQUEST (
    REQUEST_ID       NUMBER GENERATED ALWAYS AS IDENTITY CONSTRAINT WH_METRIC_REQUEST_PK PRIMARY KEY,
    ASKED_AT         TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    QUESTION         VARCHAR2(4000) NOT NULL,
    EXPLORATORY_SQL  CLOB,
    STATUS           VARCHAR2(20) DEFAULT 'OPEN' NOT NULL
) ANNOTATIONS (
    DESCRIPTION 'User requests to certify a measure that does not yet match an approved metric.',
    ALIASES 'certification request, metric proposal',
    BUSINESS_TERM 'Metric request',
    SYNONYMS 'new metric request, measure request'
);

CREATE TABLE WH_ANSWER_LOG (
    ANSWER_ID      NUMBER GENERATED ALWAYS AS IDENTITY CONSTRAINT WH_ANSWER_LOG_PK PRIMARY KEY,
    ASKED_AT       TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    QUESTION       VARCHAR2(4000) NOT NULL,
    TIER           VARCHAR2(20) NOT NULL,
    METRIC_ID      VARCHAR2(40),
    METRIC_VERSION NUMBER(5),
    COMPILED_SQL   CLOB,
    BIND_JSON      CLOB,
    ROW_COUNT      NUMBER(10),
    CONSTRAINT WH_ANSWER_LOG_METRIC_FK FOREIGN KEY (METRIC_ID) REFERENCES WH_METRIC (METRIC_ID)
) ANNOTATIONS (
    DESCRIPTION 'Provenance record for each answer: question, certification tier, metric version, SQL and bind values.',
    ALIASES 'answer audit, query log, provenance log',
    BUSINESS_TERM 'Answer log',
    SYNONYMS 'audit trail, answer provenance'
);

COMMENT ON TABLE WH_LOCATION IS 'Synthetic care locations; location and state dimension.';
COMMENT ON COLUMN WH_LOCATION.LOCATION_ID IS 'Stable synthetic location key.';
COMMENT ON COLUMN WH_LOCATION.NAME IS 'Display name of the care location.';
COMMENT ON COLUMN WH_LOCATION.CITY IS 'City of the care location.';
COMMENT ON COLUMN WH_LOCATION.STATE IS 'Two-letter US state code used for state filters.';
COMMENT ON COLUMN WH_LOCATION.REGION IS 'Broad geographic region.';

COMMENT ON TABLE WH_EMPLOYEE IS 'Synthetic employee roster; location and job are current values, not historical event values.';
COMMENT ON COLUMN WH_EMPLOYEE.EMP_ID IS 'Stable synthetic employee key.';
COMMENT ON COLUMN WH_EMPLOYEE.LOCATION_ID IS 'Current location; use WH_TERMINATION.LOCATION_ID for event-time attribution.';
COMMENT ON COLUMN WH_EMPLOYEE.JOB_FAMILY IS 'Current broad job category.';
COMMENT ON COLUMN WH_EMPLOYEE.HIRE_DATE IS 'Original hire date.';
COMMENT ON COLUMN WH_EMPLOYEE.STATUS IS 'Current synthetic roster status.';

COMMENT ON TABLE WH_TERMINATION IS 'Synthetic employment events, including internal transfers; event-time dimensions are snapshotted.';
COMMENT ON COLUMN WH_TERMINATION.TERM_ID IS 'Stable synthetic event key.';
COMMENT ON COLUMN WH_TERMINATION.EMP_ID IS 'Employee associated with the event.';
COMMENT ON COLUMN WH_TERMINATION.TERM_DATE IS 'Calendar date of employment event.';
COMMENT ON COLUMN WH_TERMINATION.TERM_TYPE IS 'VOLUNTARY, INVOLUNTARY, RETIREMENT or INTERNAL_TRANSFER.';
COMMENT ON COLUMN WH_TERMINATION.LOCATION_ID IS 'Location at the time of event, used for certified turnover.';
COMMENT ON COLUMN WH_TERMINATION.JOB_FAMILY IS 'Job family at the time of event, used for certified turnover.';

COMMENT ON TABLE WH_HEADCOUNT_MONTH IS 'Completed month-end workforce count by location and job family.';
COMMENT ON COLUMN WH_HEADCOUNT_MONTH.MONTH_START IS 'First day of completed snapshot month; measure is month-end.';
COMMENT ON COLUMN WH_HEADCOUNT_MONTH.LOCATION_ID IS 'Location represented in the snapshot.';
COMMENT ON COLUMN WH_HEADCOUNT_MONTH.JOB_FAMILY IS 'Job family represented in the snapshot.';
COMMENT ON COLUMN WH_HEADCOUNT_MONTH.HEADCOUNT IS 'Number of workers at calendar month end.';

COMMENT ON TABLE WH_REQUISITION IS 'Synthetic staffing requisitions, with fill outcomes and dates.';
COMMENT ON COLUMN WH_REQUISITION.REQ_ID IS 'Stable synthetic requisition key.';
COMMENT ON COLUMN WH_REQUISITION.LOCATION_ID IS 'Location of the requisition.';
COMMENT ON COLUMN WH_REQUISITION.JOB_FAMILY IS 'Job family of the requisition.';
COMMENT ON COLUMN WH_REQUISITION.OPEN_DATE IS 'Date requisition opened.';
COMMENT ON COLUMN WH_REQUISITION.FILL_DATE IS 'Date requisition was filled; null means still open.';
COMMENT ON COLUMN WH_REQUISITION.FILLED_INTERNALLY IS 'Y for an internal fill, N for an external fill, null for open.';

COMMENT ON TABLE WH_METRIC IS 'Governed metric catalog; only CERTIFIED metrics may produce certified answers.';
COMMENT ON COLUMN WH_METRIC.METRIC_ID IS 'Stable identifier used by the fixed SQL compiler.';
COMMENT ON COLUMN WH_METRIC.NAME IS 'Human-readable metric name.';
COMMENT ON COLUMN WH_METRIC.VERSION IS 'Version of approved business definition.';
COMMENT ON COLUMN WH_METRIC.OWNER IS 'Team accountable for the metric definition.';
COMMENT ON COLUMN WH_METRIC.STATUS IS 'CERTIFIED or DRAFT.';
COMMENT ON COLUMN WH_METRIC.DEFINITION_TEXT IS 'Approved plain-English definition and unit.';
COMMENT ON COLUMN WH_METRIC.SQL_TEMPLATE IS 'Non-executable marker equal to METRIC_ID; reviewed SQL lives in application code.';
COMMENT ON COLUMN WH_METRIC.ALLOWED_DIMS IS 'JSON array of permitted result dimensions.';
COMMENT ON COLUMN WH_METRIC.ALLOWED_FILTERS IS 'JSON array of permitted filters.';
COMMENT ON COLUMN WH_METRIC.GRAIN IS 'Natural observation grain of the metric.';

COMMENT ON TABLE WH_METRIC_REQUEST IS 'Requests to certify an unmapped or unsupported measure.';
COMMENT ON COLUMN WH_METRIC_REQUEST.REQUEST_ID IS 'Generated request key.';
COMMENT ON COLUMN WH_METRIC_REQUEST.ASKED_AT IS 'Timestamp when request was filed.';
COMMENT ON COLUMN WH_METRIC_REQUEST.QUESTION IS 'Original user question.';
COMMENT ON COLUMN WH_METRIC_REQUEST.EXPLORATORY_SQL IS 'Optional exploratory SQL shown with the request.';
COMMENT ON COLUMN WH_METRIC_REQUEST.STATUS IS 'Request workflow status; defaults to OPEN.';

COMMENT ON TABLE WH_ANSWER_LOG IS 'Audit trail of returned answers and their compiled SQL provenance.';
COMMENT ON COLUMN WH_ANSWER_LOG.ANSWER_ID IS 'Generated answer key.';
COMMENT ON COLUMN WH_ANSWER_LOG.ASKED_AT IS 'Timestamp when answer was logged.';
COMMENT ON COLUMN WH_ANSWER_LOG.QUESTION IS 'Original user question.';
COMMENT ON COLUMN WH_ANSWER_LOG.TIER IS 'CERTIFIED, EXPLORATORY or REFUSED answer tier.';
COMMENT ON COLUMN WH_ANSWER_LOG.METRIC_ID IS 'Approved metric ID when a certified metric was used.';
COMMENT ON COLUMN WH_ANSWER_LOG.METRIC_VERSION IS 'Metric definition version used for the answer.';
COMMENT ON COLUMN WH_ANSWER_LOG.COMPILED_SQL IS 'SQL issued for the answer, when applicable.';
COMMENT ON COLUMN WH_ANSWER_LOG.BIND_JSON IS 'JSON representation of bind values used with COMPILED_SQL.';
COMMENT ON COLUMN WH_ANSWER_LOG.ROW_COUNT IS 'Number of result rows returned.';
