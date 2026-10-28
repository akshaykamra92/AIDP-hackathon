CREATE OR REPLACE PACKAGE BODY WH_SECURITY_ADMIN AS
    FUNCTION NORMAL_USER_ID (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := LOWER(TRIM(p_value));
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) > 128 OR
           REGEXP_INSTR(v_value, '[[:cntrl:]]') > 0 OR
           REGEXP_INSTR(v_value, '^[a-z0-9][a-z0-9._@+-]*$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20101, 'Invalid application user identifier.');
        END IF;
        RETURN v_value;
    END NORMAL_USER_ID;

    FUNCTION NORMAL_LOCATION_KEY (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := UPPER(TRIM(p_value));
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) > 128 OR
           REGEXP_INSTR(v_value, '[[:cntrl:]]') > 0 OR
           REGEXP_INSTR(v_value, '^[A-Z0-9][A-Z0-9_.:-]*$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20102, 'Invalid location key.');
        END IF;
        RETURN v_value;
    END NORMAL_LOCATION_KEY;

    FUNCTION NORMAL_DISPLAY_NAME (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := TRIM(p_value);
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) > 200 OR
           REGEXP_INSTR(v_value, '[[:cntrl:]]') > 0 THEN
            RAISE_APPLICATION_ERROR(-20103, 'Invalid display name.');
        END IF;
        RETURN v_value;
    END NORMAL_DISPLAY_NAME;

    FUNCTION NORMAL_TOKEN_HASH (p_value IN VARCHAR2) RETURN VARCHAR2 IS
        v_value VARCHAR2(32767) := TRIM(p_value);
    BEGIN
        IF v_value IS NULL OR LENGTH(v_value) <> 64 OR
           REGEXP_INSTR(v_value, '^[0-9a-f]{64}$', 1, 1, 0, 'c') = 0 THEN
            RAISE_APPLICATION_ERROR(-20104, 'Invalid token verifier.');
        END IF;
        RETURN v_value;
    END NORMAL_TOKEN_HASH;

    FUNCTION CHECK_ADMIN (p_actor IN VARCHAR2) RETURN VARCHAR2 IS
        v_actor WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_actor);
        v_found WH_APP_USER.USER_ID%TYPE;
    BEGIN
        SELECT USER_ID INTO v_found
          FROM WH_APP_USER
         WHERE USER_ID = v_actor AND ROLE = 'ADMIN' AND STATUS = 'ACTIVE';
        RETURN v_found;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20105, 'An active administrator is required.');
    END CHECK_ADMIN;

    PROCEDURE LOG_ACCESS (
        p_actor        IN VARCHAR2,
        p_action       IN VARCHAR2,
        p_target_user  IN VARCHAR2 DEFAULT NULL,
        p_location_key IN VARCHAR2 DEFAULT NULL
    ) IS
    BEGIN
        INSERT INTO WH_ACCESS_AUDIT (
            ACTOR_USER_ID, ACTION, TARGET_USER_ID, LOCATION_KEY
        ) VALUES (
            p_actor, p_action, p_target_user, p_location_key
        );
    END LOG_ACCESS;

    PROCEDURE BOOTSTRAP_ADMIN (
        p_user_id      IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_token_hash   IN VARCHAR2
    ) IS
        v_user_id      WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_display_name WH_APP_USER.DISPLAY_NAME%TYPE := NORMAL_DISPLAY_NAME(p_display_name);
        v_token_hash   WH_APP_USER.TOKEN_HASH%TYPE := NORMAL_TOKEN_HASH(p_token_hash);
        v_count        NUMBER;
    BEGIN
        -- Serialize competing first-run attempts. Package EXECUTE must be held
        -- only by the schema owner or a dedicated access administrator.
        LOCK TABLE WH_APP_USER IN EXCLUSIVE MODE;
        SELECT COUNT(*) INTO v_count FROM WH_APP_USER;
        IF v_count <> 0 THEN
            RAISE_APPLICATION_ERROR(-20106, 'The first administrator already exists.');
        END IF;
        INSERT INTO WH_APP_USER (
            USER_ID, DISPLAY_NAME, TOKEN_HASH, ROLE, CREATED_BY
        ) VALUES (
            v_user_id, v_display_name, v_token_hash, 'ADMIN', v_user_id
        );
        LOG_ACCESS(v_user_id, 'BOOTSTRAP_ADMIN', v_user_id);
    END BOOTSTRAP_ADMIN;

    PROCEDURE CREATE_USER (
        p_user_id      IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_token_hash   IN VARCHAR2,
        p_role         IN VARCHAR2,
        p_actor        IN VARCHAR2
    ) IS
        v_actor        WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_user_id      WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_display_name WH_APP_USER.DISPLAY_NAME%TYPE := NORMAL_DISPLAY_NAME(p_display_name);
        v_token_hash   WH_APP_USER.TOKEN_HASH%TYPE := NORMAL_TOKEN_HASH(p_token_hash);
        v_role         VARCHAR2(32767) := UPPER(TRIM(p_role));
    BEGIN
        IF v_role NOT IN ('ADMIN', 'READER') OR v_role IS NULL THEN
            RAISE_APPLICATION_ERROR(-20107, 'Role must be ADMIN or READER.');
        END IF;
        INSERT INTO WH_APP_USER (
            USER_ID, DISPLAY_NAME, TOKEN_HASH, ROLE, CREATED_BY
        ) VALUES (
            v_user_id, v_display_name, v_token_hash, v_role, v_actor
        );
        LOG_ACCESS(v_actor, 'CREATE_USER', v_user_id);
    END CREATE_USER;

    PROCEDURE ROTATE_TOKEN (
        p_user_id        IN VARCHAR2,
        p_new_token_hash IN VARCHAR2,
        p_actor          IN VARCHAR2
    ) IS
        v_actor      WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_user_id    WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_token_hash WH_APP_USER.TOKEN_HASH%TYPE := NORMAL_TOKEN_HASH(p_new_token_hash);
        v_old_hash   WH_APP_USER.TOKEN_HASH%TYPE;
        v_status     WH_APP_USER.STATUS%TYPE;
    BEGIN
        BEGIN
            SELECT TOKEN_HASH, STATUS INTO v_old_hash, v_status
              FROM WH_APP_USER WHERE USER_ID = v_user_id FOR UPDATE;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(-20108, 'Application user does not exist.');
        END;
        IF v_status <> 'ACTIVE' OR v_old_hash = v_token_hash THEN
            RAISE_APPLICATION_ERROR(-20109, 'Token rotation requires an active user and a new token.');
        END IF;
        UPDATE WH_APP_USER
           SET TOKEN_HASH = v_token_hash, TOKEN_ROTATED_AT = SYSTIMESTAMP
         WHERE USER_ID = v_user_id;
        LOG_ACCESS(v_actor, 'ROTATE_TOKEN', v_user_id);
    END ROTATE_TOKEN;

    PROCEDURE REVOKE_USER (
        p_user_id IN VARCHAR2,
        p_actor   IN VARCHAR2
    ) IS
        v_actor   WH_APP_USER.USER_ID%TYPE;
        v_user_id WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_role    WH_APP_USER.ROLE%TYPE;
        v_status  WH_APP_USER.STATUS%TYPE;
        v_count   NUMBER;
    BEGIN
        -- Serialize administrator revocations so two sessions cannot each
        -- observe a different "last remaining" administrator.
        LOCK TABLE WH_APP_USER IN EXCLUSIVE MODE;
        v_actor := CHECK_ADMIN(p_actor);
        BEGIN
            SELECT ROLE, STATUS INTO v_role, v_status
              FROM WH_APP_USER WHERE USER_ID = v_user_id FOR UPDATE;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(-20108, 'Application user does not exist.');
        END;
        IF v_status <> 'ACTIVE' THEN
            RAISE_APPLICATION_ERROR(-20110, 'Application user is already revoked.');
        END IF;
        IF v_role = 'ADMIN' THEN
            SELECT COUNT(*) INTO v_count
              FROM WH_APP_USER WHERE ROLE = 'ADMIN' AND STATUS = 'ACTIVE';
            IF v_count <= 1 THEN
                RAISE_APPLICATION_ERROR(-20111, 'Cannot revoke the last active administrator.');
            END IF;
        END IF;

        INSERT INTO WH_ACCESS_AUDIT (
            ACTOR_USER_ID, ACTION, TARGET_USER_ID, LOCATION_KEY
        )
        SELECT v_actor, 'REVOKE_LOCATION', USER_ID, LOCATION_KEY
          FROM WH_USER_LOCATION
         WHERE USER_ID = v_user_id AND STATUS = 'ACTIVE';
        UPDATE WH_USER_LOCATION
           SET STATUS = 'REVOKED', REVOKED_AT = SYSTIMESTAMP, REVOKED_BY = v_actor
         WHERE USER_ID = v_user_id AND STATUS = 'ACTIVE';
        UPDATE WH_APP_USER
           SET STATUS = 'REVOKED', REVOKED_AT = SYSTIMESTAMP, REVOKED_BY = v_actor
         WHERE USER_ID = v_user_id;
        LOG_ACCESS(v_actor, 'REVOKE_USER', v_user_id);
    END REVOKE_USER;

    PROCEDURE REGISTER_LOCATION (
        p_location_key IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_actor        IN VARCHAR2
    ) IS
        v_actor        WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_location_key WH_SEC_LOCATION.LOCATION_KEY%TYPE := NORMAL_LOCATION_KEY(p_location_key);
        v_display_name WH_SEC_LOCATION.DISPLAY_NAME%TYPE := NORMAL_DISPLAY_NAME(p_display_name);
    BEGIN
        INSERT INTO WH_SEC_LOCATION (
            LOCATION_KEY, DISPLAY_NAME, CREATED_BY, STATUS_BY
        ) VALUES (
            v_location_key, v_display_name, v_actor, v_actor
        );
        LOG_ACCESS(v_actor, 'REGISTER_LOCATION', NULL, v_location_key);
    END REGISTER_LOCATION;

    PROCEDURE SET_LOCATION_STATUS (
        p_location_key IN VARCHAR2,
        p_status       IN VARCHAR2,
        p_actor        IN VARCHAR2
    ) IS
        v_actor        WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_location_key WH_SEC_LOCATION.LOCATION_KEY%TYPE := NORMAL_LOCATION_KEY(p_location_key);
        v_status       VARCHAR2(32767) := UPPER(TRIM(p_status));
        v_current      WH_SEC_LOCATION.STATUS%TYPE;
    BEGIN
        IF v_status NOT IN ('ACTIVE', 'INACTIVE') OR v_status IS NULL THEN
            RAISE_APPLICATION_ERROR(-20112, 'Location status must be ACTIVE or INACTIVE.');
        END IF;
        BEGIN
            SELECT STATUS INTO v_current
              FROM WH_SEC_LOCATION WHERE LOCATION_KEY = v_location_key FOR UPDATE;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(-20113, 'Location key does not exist.');
        END;
        IF v_current = v_status THEN
            RAISE_APPLICATION_ERROR(-20114, 'Location already has the requested status.');
        END IF;
        IF v_status = 'INACTIVE' THEN
            -- Reactivation never silently restores old access. An administrator
            -- must explicitly grant each location again.
            INSERT INTO WH_ACCESS_AUDIT (
                ACTOR_USER_ID, ACTION, TARGET_USER_ID, LOCATION_KEY
            )
            SELECT v_actor, 'REVOKE_LOCATION', USER_ID, LOCATION_KEY
              FROM WH_USER_LOCATION
             WHERE LOCATION_KEY = v_location_key AND STATUS = 'ACTIVE';
            UPDATE WH_USER_LOCATION
               SET STATUS = 'REVOKED', REVOKED_AT = SYSTIMESTAMP, REVOKED_BY = v_actor
             WHERE LOCATION_KEY = v_location_key AND STATUS = 'ACTIVE';
        END IF;
        UPDATE WH_SEC_LOCATION
           SET STATUS = v_status, STATUS_AT = SYSTIMESTAMP, STATUS_BY = v_actor
         WHERE LOCATION_KEY = v_location_key;
        LOG_ACCESS(v_actor, 'SET_LOCATION_STATUS', NULL, v_location_key);
    END SET_LOCATION_STATUS;

    PROCEDURE GRANT_LOCATION (
        p_user_id      IN VARCHAR2,
        p_location_key IN VARCHAR2,
        p_actor        IN VARCHAR2
    ) IS
        v_actor        WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_user_id      WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_location_key WH_SEC_LOCATION.LOCATION_KEY%TYPE := NORMAL_LOCATION_KEY(p_location_key);
        v_user_status  WH_APP_USER.STATUS%TYPE;
        v_loc_status   WH_SEC_LOCATION.STATUS%TYPE;
        v_grant_status WH_USER_LOCATION.STATUS%TYPE;
    BEGIN
        BEGIN
            SELECT STATUS INTO v_user_status FROM WH_APP_USER
             WHERE USER_ID = v_user_id FOR UPDATE;
            SELECT STATUS INTO v_loc_status FROM WH_SEC_LOCATION
             WHERE LOCATION_KEY = v_location_key FOR UPDATE;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RAISE_APPLICATION_ERROR(-20115, 'User or location key does not exist.');
        END;
        IF v_user_status <> 'ACTIVE' OR v_loc_status <> 'ACTIVE' THEN
            RAISE_APPLICATION_ERROR(-20116, 'Both user and location must be active.');
        END IF;

        BEGIN
            SELECT STATUS INTO v_grant_status FROM WH_USER_LOCATION
             WHERE USER_ID = v_user_id AND LOCATION_KEY = v_location_key FOR UPDATE;
            IF v_grant_status = 'ACTIVE' THEN
                RAISE_APPLICATION_ERROR(-20117, 'This location grant is already active.');
            END IF;
            UPDATE WH_USER_LOCATION
               SET STATUS = 'ACTIVE', GRANTED_AT = SYSTIMESTAMP,
                   GRANTED_BY = v_actor, REVOKED_AT = NULL, REVOKED_BY = NULL
             WHERE USER_ID = v_user_id AND LOCATION_KEY = v_location_key;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                INSERT INTO WH_USER_LOCATION (
                    USER_ID, LOCATION_KEY, GRANTED_BY
                ) VALUES (
                    v_user_id, v_location_key, v_actor
                );
        END;
        LOG_ACCESS(v_actor, 'GRANT_LOCATION', v_user_id, v_location_key);
    END GRANT_LOCATION;

    PROCEDURE REVOKE_LOCATION (
        p_user_id      IN VARCHAR2,
        p_location_key IN VARCHAR2,
        p_actor        IN VARCHAR2
    ) IS
        v_actor        WH_APP_USER.USER_ID%TYPE := CHECK_ADMIN(p_actor);
        v_user_id      WH_APP_USER.USER_ID%TYPE := NORMAL_USER_ID(p_user_id);
        v_location_key WH_SEC_LOCATION.LOCATION_KEY%TYPE := NORMAL_LOCATION_KEY(p_location_key);
    BEGIN
        UPDATE WH_USER_LOCATION
           SET STATUS = 'REVOKED', REVOKED_AT = SYSTIMESTAMP, REVOKED_BY = v_actor
         WHERE USER_ID = v_user_id AND LOCATION_KEY = v_location_key
           AND STATUS = 'ACTIVE';
        IF SQL%ROWCOUNT <> 1 THEN
            RAISE_APPLICATION_ERROR(-20118, 'No active location grant exists.');
        END IF;
        LOG_ACCESS(v_actor, 'REVOKE_LOCATION', v_user_id, v_location_key);
    END REVOKE_LOCATION;
END WH_SECURITY_ADMIN;
