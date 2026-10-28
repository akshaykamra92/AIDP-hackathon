CREATE OR REPLACE PACKAGE WH_CACHE_ADMIN AUTHID DEFINER AS
    PROCEDURE SET_POLICY (
        p_metric_id      IN VARCHAR2,
        p_metric_version IN NUMBER,
        p_enabled        IN VARCHAR2,
        p_ttl_seconds    IN NUMBER,
        p_actor          IN VARCHAR2
    );

    -- Call in the same transaction that publishes changed source rows. A bump
    -- after a separate ingestion commit leaves a stale-cache interval.
    PROCEDURE BUMP_SOURCE_REVISION (
        p_source_view IN VARCHAR2,
        p_actor       IN VARCHAR2
    );

    PROCEDURE PURGE_EXPIRED (
        p_actor         IN VARCHAR2,
        p_deleted_count OUT NUMBER
    );
END WH_CACHE_ADMIN;
