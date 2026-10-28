"""FastAPI entry point for Ask the Warehouse."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # The pure-Python tests do not require optional app dependencies.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import DatabaseUnavailable, OracleRepository
from app.metrics import CompileError
from app.registered_repo import CatalogError, RegisteredMetricRepository
from app.security import Principal, SecurityRepository
from app.select_ai import SelectAIError, SelectAIExplorer
from app.service import WarehouseService


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CertificationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    nearest_metric_ids: Optional[List[str]] = None


class MetricQueryRequest(BaseModel):
    metric_id: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    dimensions: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)


class SelectAIPreviewRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AppStaticFiles(StaticFiles):
    """Keep demo assets out of the portable application's public routes."""

    def __init__(self, *args, allowed_paths: Optional[set[str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_paths = allowed_paths

    async def get_response(self, path, scope):
        if self.allowed_paths is not None and path.strip("/") not in self.allowed_paths:
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def create_app(
    service: Optional[WarehouseService] = None,
    registered_repository: Optional[RegisteredMetricRepository] = None,
    security_repository: Optional[SecurityRepository] = None,
    select_ai_explorer: Optional[SelectAIExplorer] = None,
    app_mode: Optional[str] = None,
    select_ai_enabled: Optional[bool] = None,
) -> FastAPI:
    app = FastAPI(title="Ask the Warehouse", version="0.1.0")
    mode = (app_mode or os.getenv("APP_MODE", "demo")).strip().lower()
    if mode not in {"demo", "portable"}:
        raise ValueError("APP_MODE must be demo or portable.")
    warehouse = (service or WarehouseService.from_environment()) if mode == "demo" else service
    registry = registered_repository or RegisteredMetricRepository()
    security = security_repository or SecurityRepository(getattr(registry, "database", None))
    ai_enabled = (
        select_ai_enabled if select_ai_enabled is not None
        else os.getenv("SELECT_AI_ENABLED", "false").strip().lower() == "true"
    )
    ai_explorer = select_ai_explorer or SelectAIExplorer(
        getattr(registry, "database", None) or OracleRepository(), enabled=ai_enabled
    )
    app.state.mode = mode
    app.state.location_auth = mode == "portable"
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        allowed = {"catalog.css", "catalog.js"} if mode == "portable" else None
        app.mount("/static", AppStaticFiles(directory=str(static_dir), allowed_paths=allowed), name="static")

    @app.get("/")
    def index():
        index_file = static_dir / ("catalog.html" if mode == "portable" else "index.html")
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"name": "Ask the Warehouse", "docs": "/docs"}

    @app.get("/health")
    def health():
        try:
            if mode == "portable":
                if not registry.catalog_ready():
                    return {
                        "status": "degraded",
                        "database": "connected",
                        "catalog": "missing",
                        "security": "unknown",
                        "auth": "bearer",
                        "message": "Run scripts/install_catalog.py --apply before serving portable metrics.",
                    }
                if not security.security_ready():
                    return {
                        "status": "degraded",
                        "database": "connected",
                        "catalog": "ready",
                        "security": "missing",
                        "auth": "bearer",
                        "message": "Run scripts/install_security.py --apply before serving portable metrics.",
                    }
                return {"status": "ok", "database": "connected", "catalog": "ready", "security": "ready", "auth": "bearer"}
            assert warehouse is not None
            if not warehouse.repository.health():
                raise DatabaseUnavailable("Oracle health check did not return a result.")
            return {"status": "ok", "database": "connected"}
        except DatabaseUnavailable as exc:
            return {"status": "degraded", "database": "unavailable", "message": str(exc)}

    if mode == "demo":
        assert warehouse is not None

        @app.post("/ask")
        def ask(payload: AskRequest):
            try:
                return warehouse.ask(payload.question)
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @app.post("/certification-request")
        @app.post("/certification-requests")
        def certification_request(payload: CertificationRequest):
            try:
                return warehouse.certification_request(payload.question)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @app.get("/demo/turnover-comparison")
        def turnover_comparison():
            try:
                return warehouse.demo_turnover_comparison()
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

    if mode == "portable":
        @app.get("/catalog")
        def catalog_page():
            page = static_dir / "catalog.html"
            if page.exists():
                return FileResponse(str(page))
            raise HTTPException(status_code=404, detail="Metric explorer page is unavailable.")

        def require_principal(request: Request) -> Principal:
            authorization = request.headers.getlist("authorization")
            if len(authorization) != 1:
                raise HTTPException(status_code=401, detail="Bearer token required.", headers={"WWW-Authenticate": "Bearer"})
            parts = authorization[0].split()
            if len(parts) != 2 or parts[0].lower() != "bearer":
                raise HTTPException(status_code=401, detail="Bearer token required.", headers={"WWW-Authenticate": "Bearer"})
            try:
                principal = security.authenticate_token(parts[1])
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail="Application identity store is unavailable.") from exc
            if principal is None:
                raise HTTPException(status_code=401, detail="Invalid or revoked bearer token.", headers={"WWW-Authenticate": "Bearer"})
            return principal

        def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
            if principal.role != "ADMIN":
                raise HTTPException(status_code=403, detail="Administrator access is required for Select AI preview.")
            return principal

        @app.get("/me")
        def whoami(principal: Principal = Depends(require_principal)):
            try:
                locations = security.active_locations(principal.user_id)
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail="Location access store is unavailable.") from exc
            return {
                "user_id": principal.user_id,
                "display_name": principal.display_name,
                "role": principal.role,
                "locations": locations,
            }

        @app.get("/metrics")
        def registered_metrics(principal: Principal = Depends(require_principal)):
            try:
                return {"metrics": [metric.public_dict() for metric in registry.list_certified(scoped_only=True)]}
            except CatalogError as exc:
                raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        @app.post("/metric-query")
        def metric_query(payload: MetricQueryRequest, principal: Principal = Depends(require_principal)):
            try:
                if not security.active_locations(principal.user_id):
                    raise HTTPException(status_code=403, detail="No active location grants for this user.")
                return registry.query(
                    payload.metric_id, payload.start_date, payload.end_date,
                    payload.dimensions, payload.filters, user_id=principal.user_id,
                )
            except CatalogError as exc:
                status = (
                    503 if exc.code == "CATALOG_NOT_INSTALLED" else
                    409 if exc.code in {"SOURCE_CHANGED", "LOCATION_SCOPE_REQUIRED"} else
                    404 if exc.code == "NO_DATA" else 422
                )
                raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc
            except CompileError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
            except DatabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        if ai_enabled:
            @app.post("/select-ai/preview")
            def select_ai_preview(
                payload: SelectAIPreviewRequest,
                principal: Principal = Depends(require_admin),
            ):
                try:
                    return ai_explorer.generate_sql(payload.question)
                except SelectAIError as exc:
                    status = 422 if exc.code == "INVALID_PROMPT" else 503
                    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc

    return app


app = create_app()
