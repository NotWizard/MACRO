"""Read-only presentation API; all updates are performed by the local Agent CLI."""
from datetime import date
from fastapi import APIRouter, Query
from backend.app.core import compute_analysis, compute_service

router = APIRouter(prefix="/compute", tags=["compute"])

@router.get("/overview")
def overview(months: int | None = Query(None, ge=1, le=120), as_of: date | None = None):
    return compute_analysis.overview(as_of.isoformat() if as_of else None, months)

@router.get("/contract")
def contract():
    return compute_service.contract()
