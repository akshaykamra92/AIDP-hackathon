CREATE OR REPLACE PACKAGE WH_METRIC_GOVERNANCE AUTHID DEFINER AS
    PROCEDURE CREATE_DRAFT (
        p_metric_id       IN VARCHAR2,
        p_name            IN VARCHAR2,
        p_owner           IN VARCHAR2,
        p_definition_text IN CLOB,
        p_unit            IN VARCHAR2,
        p_source_view     IN VARCHAR2,
        p_date_column     IN VARCHAR2,
        p_value_column    IN VARCHAR2,
        p_aggregation     IN VARCHAR2,
        p_allowed_dims    IN CLOB,
        p_allowed_filters IN CLOB,
        p_version         OUT NUMBER
    );

    PROCEDURE REVIEW_DRAFT (
        p_metric_id   IN VARCHAR2,
        p_version     IN NUMBER,
        p_reviewer    IN VARCHAR2,
        p_review_note IN CLOB
    );

    PROCEDURE CERTIFY (
        p_metric_id IN VARCHAR2,
        p_version   IN NUMBER,
        p_approver  IN VARCHAR2
    );
END WH_METRIC_GOVERNANCE;
