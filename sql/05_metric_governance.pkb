CREATE OR REPLACE PACKAGE BODY WH_METRIC_GOVERNANCE AS
    FUNCTION NORMAL_NAME (
        p_value      IN VARCHAR2,
        p_require_wh IN BOOLEAN DEFAULT FALSE
    ) RETURN VARCHAR2 IS
        v_name VARCHAR2(128);
    BEGIN
        v_name := UPPER(TRIM(p_value));
        IF v_name IS NULL OR LENGTH(v_name) > 128
           OR REGEXP_INSTR(v_name, '^[A-Z][A-Z0-9_$#]*$') <> 1 THEN
            RAISE_APPLICATION_ERROR(-20001, 'Invalid catalog identifier.');
        END IF;
        IF p_require_wh AND SUBSTR(v_name, 1, 3) <> 'WH_' THEN
            RAISE_APPLICATION_ERROR(-20001, 'Source must be an owned WH_ view.');
        END IF;
        RETURN v_name;
    END NORMAL_NAME;

    PROCEDURE REQUIRE_JSON_ARRAY (p_json IN CLOB) IS
        TYPE NAME_SET IS TABLE OF BOOLEAN INDEX BY VARCHAR2(128);
        v_seen NAME_SET;
        v_array JSON_ARRAY_T;
        v_item JSON_ELEMENT_T;
        v_name VARCHAR2(128);
    BEGIN
        v_array := JSON_ARRAY_T.PARSE(p_json);
        IF v_array.GET_SIZE() > 20 THEN
            RAISE_APPLICATION_ERROR(-20002, 'Allowlist may contain at most 20 columns.');
        END IF;
        FOR item_index IN 0 .. v_array.GET_SIZE() - 1 LOOP
            v_item := v_array.GET(item_index);
            IF NOT v_item.IS_STRING() THEN
                RAISE_APPLICATION_ERROR(-20002, 'Allowlist entries must be column names.');
            END IF;
            v_name := v_array.GET_STRING(item_index);
            IF v_name <> NORMAL_NAME(v_name) THEN
                RAISE_APPLICATION_ERROR(-20002, 'Allowlist column names must be uppercase.');
            END IF;
            IF v_seen.EXISTS(v_name) THEN
                RAISE_APPLICATION_ERROR(-20002, 'Allowlist contains a duplicate column.');
            END IF;
            v_seen(v_name) := TRUE;
        END LOOP;
    EXCEPTION
        WHEN OTHERS THEN
            IF SQLCODE BETWEEN -20999 AND -20000 THEN
                RAISE;
            END IF;
            RAISE_APPLICATION_ERROR(-20002, 'Allowlist must be a JSON array.');
    END REQUIRE_JSON_ARRAY;

    PROCEDURE CHECK_ALLOWLIST (
        p_json        IN CLOB,
        p_source_view IN VARCHAR2
    ) IS
        v_column_name VARCHAR2(128);
        v_data_type VARCHAR2(128);
    BEGIN
        REQUIRE_JSON_ARRAY(p_json);
        FOR item IN (
            SELECT column_name
            FROM JSON_TABLE(
                p_json,
                '$[*]' COLUMNS (column_name VARCHAR2(128) PATH '$')
            )
        ) LOOP
            v_column_name := NORMAL_NAME(item.column_name);
            SELECT DATA_TYPE INTO v_data_type
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = p_source_view
              AND COLUMN_NAME = v_column_name;
            IF v_data_type NOT IN (
                'VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR',
                'NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE'
            ) THEN
                RAISE_APPLICATION_ERROR(-20003, 'Allowlist column must be text or numeric.');
            END IF;
        END LOOP;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20003, 'Allowlist references a missing view column.');
    END CHECK_ALLOWLIST;

    FUNCTION CHECK_SOURCE_VIEW (
        p_source_view IN VARCHAR2
    ) RETURN DATE IS
        v_last_ddl DATE;
    BEGIN
        SELECT LAST_DDL_TIME INTO v_last_ddl
        FROM USER_OBJECTS
        WHERE OBJECT_NAME = p_source_view
          AND OBJECT_TYPE = 'VIEW'
          AND STATUS = 'VALID';
        RETURN v_last_ddl;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20004, 'Source must be a valid owned WH_ view.');
    END CHECK_SOURCE_VIEW;

    PROCEDURE CHECK_SOURCE_COLUMNS (
        p_source_view  IN VARCHAR2,
        p_date_column IN VARCHAR2,
        p_location_column IN VARCHAR2,
        p_value_column IN VARCHAR2,
        p_numerator_column IN VARCHAR2,
        p_denominator_column IN VARCHAR2,
        p_aggregation IN VARCHAR2
    ) IS
        v_data_type VARCHAR2(128);
    BEGIN
        SELECT DATA_TYPE INTO v_data_type
        FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = p_source_view
          AND COLUMN_NAME = p_date_column;
        IF v_data_type <> 'DATE'
           AND (v_data_type NOT LIKE 'TIMESTAMP%' OR v_data_type LIKE '%TIME ZONE%') THEN
            RAISE_APPLICATION_ERROR(-20005, 'Date column must be DATE or TIMESTAMP without time zone.');
        END IF;

        SELECT DATA_TYPE INTO v_data_type
        FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = p_source_view
          AND COLUMN_NAME = p_location_column;
        IF v_data_type NOT IN ('VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR') THEN
            RAISE_APPLICATION_ERROR(-20005, 'Location key column must be text.');
        END IF;

        IF p_aggregation IN ('SUM', 'AVG') THEN
            SELECT DATA_TYPE INTO v_data_type
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = p_source_view
              AND COLUMN_NAME = p_value_column;
            IF v_data_type NOT IN ('NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE') THEN
                RAISE_APPLICATION_ERROR(-20005, 'Value column must be numeric.');
            END IF;
        END IF;
        IF p_aggregation = 'RATIO_SUM' THEN
            SELECT DATA_TYPE INTO v_data_type
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = p_source_view
              AND COLUMN_NAME = p_numerator_column;
            IF v_data_type NOT IN ('NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE') THEN
                RAISE_APPLICATION_ERROR(-20005, 'Numerator column must be numeric.');
            END IF;
            SELECT DATA_TYPE INTO v_data_type
            FROM USER_TAB_COLUMNS
            WHERE TABLE_NAME = p_source_view
              AND COLUMN_NAME = p_denominator_column;
            IF v_data_type NOT IN ('NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE') THEN
                RAISE_APPLICATION_ERROR(-20005, 'Denominator column must be numeric.');
            END IF;
        END IF;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20005, 'Required source-view column is missing.');
    END CHECK_SOURCE_COLUMNS;

    PROCEDURE CREATE_DRAFT (
        p_metric_id       IN VARCHAR2,
        p_name            IN VARCHAR2,
        p_owner           IN VARCHAR2,
        p_definition_text IN CLOB,
        p_unit            IN VARCHAR2,
        p_source_view     IN VARCHAR2,
        p_date_column     IN VARCHAR2,
        p_location_column IN VARCHAR2,
        p_value_column    IN VARCHAR2,
        p_numerator_column IN VARCHAR2,
        p_denominator_column IN VARCHAR2,
        p_aggregation     IN VARCHAR2,
        p_allowed_dims    IN CLOB,
        p_allowed_filters IN CLOB,
        p_version         OUT NUMBER
    ) IS
        v_metric_id VARCHAR2(128);
        v_source_view VARCHAR2(128);
        v_date_column VARCHAR2(128);
        v_location_column VARCHAR2(128);
        v_value_column VARCHAR2(128);
        v_numerator_column VARCHAR2(128);
        v_denominator_column VARCHAR2(128);
        v_aggregation VARCHAR2(128);
    BEGIN
        v_metric_id := NORMAL_NAME(p_metric_id);
        IF LENGTH(v_metric_id) > 64 THEN
            RAISE_APPLICATION_ERROR(-20001, 'Invalid metric identifier.');
        END IF;
        v_source_view := NORMAL_NAME(p_source_view, TRUE);
        v_date_column := NORMAL_NAME(p_date_column);
        v_location_column := NORMAL_NAME(p_location_column);
        v_aggregation := UPPER(TRIM(p_aggregation));
        IF v_aggregation NOT IN ('COUNT', 'SUM', 'AVG', 'RATIO_SUM') OR v_aggregation IS NULL THEN
            RAISE_APPLICATION_ERROR(-20001, 'Unsupported aggregate.');
        END IF;
        IF (v_aggregation = 'COUNT' AND
                (p_value_column IS NOT NULL OR p_numerator_column IS NOT NULL OR p_denominator_column IS NOT NULL))
           OR (v_aggregation IN ('SUM', 'AVG') AND
                (p_value_column IS NULL OR p_numerator_column IS NOT NULL OR p_denominator_column IS NOT NULL))
           OR (v_aggregation = 'RATIO_SUM' AND
                (p_value_column IS NOT NULL OR p_numerator_column IS NULL OR p_denominator_column IS NULL)) THEN
            RAISE_APPLICATION_ERROR(-20001, 'Aggregate columns do not match the approved calculation kind.');
        END IF;
        IF p_value_column IS NOT NULL THEN
            v_value_column := NORMAL_NAME(p_value_column);
        END IF;
        IF p_numerator_column IS NOT NULL THEN
            v_numerator_column := NORMAL_NAME(p_numerator_column);
        END IF;
        IF p_denominator_column IS NOT NULL THEN
            v_denominator_column := NORMAL_NAME(p_denominator_column);
        END IF;
        IF TRIM(p_name) IS NULL OR TRIM(p_owner) IS NULL OR TRIM(p_unit) IS NULL
           OR NVL(DBMS_LOB.GETLENGTH(p_definition_text), 0) NOT BETWEEN 1 AND 16000
           OR TRIM(DBMS_LOB.SUBSTR(p_definition_text, 16000, 1)) IS NULL THEN
            RAISE_APPLICATION_ERROR(-20001, 'Metric meaning, owner, and unit are required.');
        END IF;
        REQUIRE_JSON_ARRAY(p_allowed_dims);
        REQUIRE_JSON_ARRAY(p_allowed_filters);

        SELECT NVL(MAX(VERSION), 0) + 1 INTO p_version
        FROM WH_REGISTERED_METRIC
        WHERE METRIC_ID = v_metric_id;

        INSERT INTO WH_REGISTERED_METRIC (
            METRIC_ID, VERSION, NAME, OWNER, DEFINITION_TEXT, UNIT,
            SOURCE_VIEW, DATE_COLUMN, LOCATION_COLUMN, VALUE_COLUMN,
            NUMERATOR_COLUMN, DENOMINATOR_COLUMN, AGGREGATION,
            ALLOWED_DIMS, ALLOWED_FILTERS, STATUS
        ) VALUES (
            v_metric_id, p_version, TRIM(p_name), TRIM(p_owner), p_definition_text, TRIM(p_unit),
            v_source_view, v_date_column, v_location_column, v_value_column,
            v_numerator_column, v_denominator_column, v_aggregation,
            p_allowed_dims, p_allowed_filters, 'DRAFT'
        );
    EXCEPTION
        WHEN DUP_VAL_ON_INDEX THEN
            RAISE_APPLICATION_ERROR(-20006, 'A concurrent draft used this version; retry.');
    END CREATE_DRAFT;

    PROCEDURE REVIEW_DRAFT (
        p_metric_id         IN VARCHAR2,
        p_version           IN NUMBER,
        p_reviewer          IN VARCHAR2,
        p_review_note       IN CLOB,
        p_source_ddl_sha256 IN VARCHAR2
    ) IS
        v_metric WH_REGISTERED_METRIC%ROWTYPE;
        v_last_ddl DATE;
        v_metric_id VARCHAR2(128);
        v_source_hash VARCHAR2(64);
    BEGIN
        IF TRIM(p_reviewer) IS NULL OR LENGTH(TRIM(p_reviewer)) > 120
           OR NVL(DBMS_LOB.GETLENGTH(p_review_note), 0) NOT BETWEEN 1 AND 16000
           OR TRIM(DBMS_LOB.SUBSTR(p_review_note, 16000, 1)) IS NULL THEN
            RAISE_APPLICATION_ERROR(-20001, 'Reviewer and review note are required.');
        END IF;
        v_source_hash := LOWER(TRIM(p_source_ddl_sha256));
        IF v_source_hash IS NULL OR LENGTH(v_source_hash) <> 64
           OR NOT REGEXP_LIKE(v_source_hash, '^[0-9a-f]{64}$') THEN
            RAISE_APPLICATION_ERROR(-20001, 'A complete source view SHA-256 digest is required.');
        END IF;
        v_metric_id := NORMAL_NAME(p_metric_id);
        SELECT * INTO v_metric
        FROM WH_REGISTERED_METRIC
        WHERE METRIC_ID = v_metric_id
          AND VERSION = p_version
        FOR UPDATE;
        IF v_metric.STATUS <> 'DRAFT' THEN
            RAISE_APPLICATION_ERROR(-20007, 'Metric version is not a draft.');
        END IF;

        v_last_ddl := CHECK_SOURCE_VIEW(v_metric.SOURCE_VIEW);
        CHECK_SOURCE_COLUMNS(
            v_metric.SOURCE_VIEW,
            v_metric.DATE_COLUMN,
            v_metric.LOCATION_COLUMN,
            v_metric.VALUE_COLUMN,
            v_metric.NUMERATOR_COLUMN,
            v_metric.DENOMINATOR_COLUMN,
            v_metric.AGGREGATION
        );
        CHECK_ALLOWLIST(v_metric.ALLOWED_DIMS, v_metric.SOURCE_VIEW);
        CHECK_ALLOWLIST(v_metric.ALLOWED_FILTERS, v_metric.SOURCE_VIEW);

        UPDATE WH_REGISTERED_METRIC
        SET STATUS = 'REVIEWED',
            REVIEWED_AT = SYSTIMESTAMP,
            REVIEWED_BY = TRIM(p_reviewer),
            REVIEW_NOTE = p_review_note,
            SOURCE_DDL_AT = v_last_ddl,
            SOURCE_DDL_SHA256 = v_source_hash
        WHERE METRIC_ID = v_metric.METRIC_ID
          AND VERSION = v_metric.VERSION;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20007, 'Metric draft was not found.');
    END REVIEW_DRAFT;

    PROCEDURE CERTIFY (
        p_metric_id         IN VARCHAR2,
        p_version           IN NUMBER,
        p_approver          IN VARCHAR2,
        p_source_ddl_sha256 IN VARCHAR2
    ) IS
        v_metric WH_REGISTERED_METRIC%ROWTYPE;
        v_last_ddl DATE;
        v_metric_id VARCHAR2(128);
    BEGIN
        IF TRIM(p_approver) IS NULL OR LENGTH(TRIM(p_approver)) > 120 THEN
            RAISE_APPLICATION_ERROR(-20001, 'Approver is required.');
        END IF;
        v_metric_id := NORMAL_NAME(p_metric_id);
        SELECT * INTO v_metric
        FROM WH_REGISTERED_METRIC
        WHERE METRIC_ID = v_metric_id
          AND VERSION = p_version
        FOR UPDATE;
        IF v_metric.STATUS <> 'REVIEWED' THEN
            RAISE_APPLICATION_ERROR(-20008, 'Metric version has not been reviewed.');
        END IF;
        IF v_metric.LOCATION_COLUMN IS NULL THEN
            RAISE_APPLICATION_ERROR(-20008, 'Metric version has no location scope. Create a new scoped version.');
        END IF;
        IF UPPER(TRIM(p_approver)) = UPPER(TRIM(v_metric.REVIEWED_BY)) THEN
            RAISE_APPLICATION_ERROR(-20008, 'Reviewer and approver must differ.');
        END IF;

        v_last_ddl := CHECK_SOURCE_VIEW(v_metric.SOURCE_VIEW);
        IF v_metric.SOURCE_DDL_AT IS NULL OR v_last_ddl <> v_metric.SOURCE_DDL_AT THEN
            RAISE_APPLICATION_ERROR(-20008, 'Source view changed after review.');
        END IF;
        IF v_metric.SOURCE_DDL_SHA256 IS NULL
           OR LENGTH(v_metric.SOURCE_DDL_SHA256) <> 64
           OR v_metric.SOURCE_DDL_SHA256 <> LOWER(TRIM(p_source_ddl_sha256)) THEN
            RAISE_APPLICATION_ERROR(-20008, 'Source view digest is missing or changed after review.');
        END IF;
        CHECK_SOURCE_COLUMNS(
            v_metric.SOURCE_VIEW,
            v_metric.DATE_COLUMN,
            v_metric.LOCATION_COLUMN,
            v_metric.VALUE_COLUMN,
            v_metric.NUMERATOR_COLUMN,
            v_metric.DENOMINATOR_COLUMN,
            v_metric.AGGREGATION
        );
        CHECK_ALLOWLIST(v_metric.ALLOWED_DIMS, v_metric.SOURCE_VIEW);
        CHECK_ALLOWLIST(v_metric.ALLOWED_FILTERS, v_metric.SOURCE_VIEW);

        UPDATE WH_REGISTERED_METRIC
        SET STATUS = 'RETIRED'
        WHERE METRIC_ID = v_metric.METRIC_ID
          AND STATUS = 'CERTIFIED';

        UPDATE WH_REGISTERED_METRIC
        SET STATUS = 'CERTIFIED',
            CERTIFIED_AT = SYSTIMESTAMP,
            CERTIFIED_BY = TRIM(p_approver)
        WHERE METRIC_ID = v_metric.METRIC_ID
          AND VERSION = v_metric.VERSION;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20008, 'Reviewed metric version was not found.');
    END CERTIFY;
END WH_METRIC_GOVERNANCE;
