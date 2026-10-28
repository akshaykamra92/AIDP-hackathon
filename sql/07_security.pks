CREATE OR REPLACE PACKAGE WH_SECURITY_ADMIN AUTHID DEFINER AS
    -- One-time setup. Rejected as soon as any WH_APP_USER row exists.
    PROCEDURE BOOTSTRAP_ADMIN (
        p_user_id      IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_token_hash   IN VARCHAR2
    );

    -- All remaining procedures require the caller's database account to have
    -- EXECUTE on this package and p_actor to name an active ADMIN user.
    -- The package never accepts a stored token hash as administrator proof.
    PROCEDURE CREATE_USER (
        p_user_id      IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_token_hash   IN VARCHAR2,
        p_role         IN VARCHAR2,
        p_actor        IN VARCHAR2
    );

    PROCEDURE ROTATE_TOKEN (
        p_user_id        IN VARCHAR2,
        p_new_token_hash IN VARCHAR2,
        p_actor          IN VARCHAR2
    );

    PROCEDURE REVOKE_USER (
        p_user_id IN VARCHAR2,
        p_actor   IN VARCHAR2
    );

    PROCEDURE REGISTER_LOCATION (
        p_location_key IN VARCHAR2,
        p_display_name IN VARCHAR2,
        p_actor        IN VARCHAR2
    );

    PROCEDURE SET_LOCATION_STATUS (
        p_location_key IN VARCHAR2,
        p_status       IN VARCHAR2,
        p_actor        IN VARCHAR2
    );

    PROCEDURE GRANT_LOCATION (
        p_user_id      IN VARCHAR2,
        p_location_key IN VARCHAR2,
        p_actor        IN VARCHAR2
    );

    PROCEDURE REVOKE_LOCATION (
        p_user_id      IN VARCHAR2,
        p_location_key IN VARCHAR2,
        p_actor        IN VARCHAR2
    );
END WH_SECURITY_ADMIN;
