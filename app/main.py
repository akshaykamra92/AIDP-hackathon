"""FastAPI entry point for Ask the Warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # The pure-Python tests do not require optional app dependencies.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import DatabaseUnavailable
from app.service import WarehouseService


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CertificationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    nearest_metric_ids: Optional[List[str]] = None


def create_app(service: Optional[WarehouseService] = None) -> FastAPI:
    app = FastAPI(title="Ask the Warehouse", version="0.1.0")
    warehouse = service or WarehouseService.from_environment()
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"name": "Ask the Warehouse", "docs": "/docs"}

    @app.get("/health")
    def health():
        try:
            if not warehouse.repository.health():
                raise DatabaseUnavailable("Oracle health check did not return a result.")
            return {"status": "ok", "database": "connected"}
        except DatabaseUnavailable as exc:
            return {"status": "degraded", "database": "unavailable", "message": str(exc)}

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

    return app


app = create_app()
