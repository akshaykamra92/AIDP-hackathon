CREATE OR REPLACE PACKAGE BODY WH_CACHE_ADMIN AS
    FUNCTION ADMIN_ACTOR (p_actor IN VARCHAR2) RETURN VARCHAR2 IS
        v_actor WH_APP_USER.USER_ID%TYPE := LOWER(TRIM(p_actor));
        v_found WH_APP_USER.USER_ID%TYPE;
    BEGIN
        IF v_actor IS NULL OR LENGTH(v_actor) > 128 OR
           REGEXP_INSTR(v_actor, '^[a-z0-9][a-z0-9._@+-]*$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20201, 'Invalid administrator identity.');
        END IF;
        SELECT USER_ID INTO v_found
          FROM WH_APP_USER
         WHERE USER_ID = v_actor AND ROLE = 'ADMIN' AND STATUS = 'ACTIVE';
        RETURN v_found;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20202, 'An active administrator is required.');
    END ADMIN_ACTOR;

    FUNCTION METRIC_ID (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := UPPER(TRIM(p_value));
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) > 64 OR
           REGEXP_INSTR(v_value, '^[A-Z][A-Z0-9_$#]*$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20203, 'Invalid metric identifier.');
        END IF;
        RETURN v_value;
    END METRIC_ID;

    FUNCTION SOURCE_VIEW (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := UPPER(TRIM(p_value));
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) > 128 OR
           REGEXP_INSTR(v_value, '^WH_[A-Z0-9_$#]+$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20204, 'Invalid WH_ source view.');
        END IF;
        RETURN v_value;
    END SOURCE_VIEW;

    PROCEDURE SET_POLICY (
        p_metric_id      IN VARCHAR2,
        p_metric_version IN NUMBER,
        p_enabled        IN VARCHAR2,
        p_ttl_seconds    IN NUMBER,
        p_actor          IN VARCHAR2
    ) IS
        v_actor       WH_APP_USER.USER_ID%TYPE := ADMIN_ACTOR(p_actor);
        v_metric_id   WH_REGISTERED_METRIC.METRIC_ID%TYPE := METRIC_ID(p_metric_id);
        v_enabled     VARCHAR2(1) := UPPER(TRIM(p_enabled));
        v_source_view WH_REGISTERED_METRIC.SOURCE_VIEW%TYPE;
    BEGIN
        IF p_metric_version IS NULL OR p_metric_version < 1 OR
           p_metric_version <> TRUNC(p_metric_version) THEN
            RAISE_APPLICATION_ERROR(-20205, 'Metric version must be a positive integer.');
        END IF;
        IF v_enabled IS NULL OR v_enabled NOT IN ('Y', 'N') THEN
            RAISE_APPLICATION_ERROR(-20206, 'Enabled must be Y or N.');
        END IF;
        IF p_ttl_seconds IS NULL OR p_ttl_seconds < 1 OR
           p_ttl_seconds > 86400 OR p_ttl_seconds <> TRUNC(p_ttl_seconds) THEN
            RAISE_APPLICATION_ERROR(-20207, 'TTL must be an integer from 1 to 86400 seconds.');
        END IF;

        BEGIN
            SELECT SOURCE_VIEW INTO v_source_view
              FROM WH_REGISTERED_METRIC
             WHERE METRIC_ID = v_metric_id
               AND VERSION = p_metric_version
               AND STATUS = 'CERTIFIED'
               AND LOCATION_COLUMN IS NOT NULL
               AND SOURCE_DDL_SHA256 IS NOT NULL;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(-20208, 'A scoped, certified metric version is required.');
        END;
        v_source_view := SOURCE_VIEW(v_source_view);

        MERGE INTO WH_CACHE_SOURCE dst
        USING (SELECT v_source_view AS SOURCE_VIEW FROM DUAL) src
           ON (dst.SOURCE_VIEW = src.SOURCE_VIEW)
        WHEN NOT MATCHED THEN INSERT (
            SOURCE_VIEW, DATA_REVISION, UPDATED_BY
        ) VALUES (
            src.SOURCE_VIEW, 1, v_actor
        );

        MERGE INTO WH_CACHE_POLICY dst
        USING (SELECT v_metric_id AS METRIC_ID,
                      p_metric_version AS METRIC_VERSION FROM DUAL) src
           ON (dst.METRIC_ID = src.METRIC_ID AND dst.METRIC_VERSION = src.METRIC_VERSION)
        WHEN MATCHED THEN UPDATE SET
            dst.ENABLED = v_enabled,
            dst.TTL_SECONDS = p_ttl_seconds,
            dst.UPDATED_AT = SYSTIMESTAMP,
            dst.UPDATED_BY = v_actor
        WHEN NOT MATCHED THEN INSERT (
            METRIC_ID, METRIC_VERSION, ENABLED, TTL_SECONDS,
            POLICY_REVISION, UPDATED_BY
        ) VALUES (
            src.METRIC_ID, src.METRIC_VERSION, v_enabled, p_ttl_seconds,
            1, v_actor
        );
    END SET_POLICY;

    PROCEDURE BUMP_SOURCE_REVISION (
        p_source_view IN VARCHAR2,
        p_actor       IN VARCHAR2
    ) IS
        v_actor       WH_APP_USER.USER_ID%TYPE := ADMIN_ACTOR(p_actor);
        v_source_view WH_CACHE_SOURCE.SOURCE_VIEW%TYPE := SOURCE_VIEW(p_source_view);
    BEGIN
        -- WH_CACHE_SOURCE_REV_TRG advances the revision for every update.
        UPDATE WH_CACHE_SOURCE
           SET UPDATED_AT = SYSTIMESTAMP, UPDATED_BY = v_actor
         WHERE SOURCE_VIEW = v_source_view;
        IF SQL%ROWCOUNT <> 1 THEN
            RAISE_APPLICATION_ERROR(-20209, 'Source view is not registered for caching.');
        END IF;
    END BUMP_SOURCE_REVISION;

    PROCEDURE PURGE_EXPIRED (
        p_actor         IN VARCHAR2,
        p_deleted_count OUT NUMBER
    ) IS
        v_actor WH_APP_USER.USER_ID%TYPE := ADMIN_ACTOR(p_actor);
    BEGIN
        DELETE FROM WH_QUERY_CACHE WHERE EXPIRES_AT <= SYSTIMESTAMP;
        p_deleted_count := SQL%ROWCOUNT;
    END PURGE_EXPIRED;
END WH_CACHE_ADMIN;
