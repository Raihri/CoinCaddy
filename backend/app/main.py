"""FastAPI modular monolith entry point. Feature modules register their routers here."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.modules.alerts.router import router as alerts_router
from app.modules.approvals.router import router as approvals_router
from app.modules.audit.router import router as audit_router
from app.modules.business_setup.router import reference_router
from app.modules.business_setup.router import router as business_setup_router
from app.middleware import BodySizeLimit
from app.modules.cash.router import router as cash_router
from app.modules.evidence_queue.router import router as evidence_queue_router
from app.modules.funding.router import router as funding_router
from app.modules.health.router import router as health_router
from app.modules.health_summary.router import router as financial_health_router
from app.modules.obligations.router import router as obligations_router
from app.modules.outlook.router import router as outlook_router
from app.modules.payment_review.router import router as payment_review_router
from app.modules.readiness.router import documents_router as business_documents_router
from app.modules.readiness.router import router as readiness_router
from app.modules.rescue.router import permission_router as deferral_permission_router
from app.modules.rescue.router import router as rescue_router
from app.modules.records.files import MAX_FILE_BYTES
from app.modules.records.router import router as records_router
from app.modules.identity.router import auth_router, business_router


def create_app() -> FastAPI:
    app = FastAPI(title="SME Finance Copilot API", version="0.0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(business_router)
    app.include_router(audit_router)
    app.include_router(business_setup_router)
    app.include_router(reference_router)
    app.include_router(records_router)
    app.include_router(cash_router)
    app.include_router(obligations_router)
    app.include_router(financial_health_router)
    app.include_router(outlook_router)
    app.include_router(alerts_router)
    app.include_router(payment_review_router)
    app.include_router(evidence_queue_router)
    app.include_router(funding_router)
    app.include_router(rescue_router)
    app.include_router(deferral_permission_router)
    app.include_router(readiness_router)
    app.include_router(business_documents_router)
    app.include_router(approvals_router)
    # Refuse oversized uploads before multipart parsing (file limit plus room for form overhead).
    app.add_middleware(BodySizeLimit, path_pattern=r"/api/businesses/[^/]+/imports", max_bytes=MAX_FILE_BYTES + 64 * 1024)
    return app


app = create_app()
