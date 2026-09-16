import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import config_guard
import help_center
import models
import ops
from account_routes import router as account_router
from admin_console_routes import router as admin_console_router
from ai_access import resolve_ai
from ai_catalog import default_model
from ai_routes import router as ai_router
from ai_tasks import TaskNotReady
from appraisal_routes import router as appraisal_router
from audit_routes import router as audit_router
from auth_routes import get_current_user
from auth_routes import router as auth_router
from billing_routes import router as billing_router
from certainty_routes import router as certainty_router
from collaboration_routes import help_router, invitations_router, notifications_router
from collaboration_routes import me_router as collaboration_me_router
from collaboration_routes import router as collaboration_router
from database import get_db
from documents_routes import router as documents_router
from entities_routes import router as entities_router
from extraction_routes import router as extraction_router
from governance_routes import admin_router
from governance_routes import router as governance_router
from interop_routes import router as interop_router
from jobs_routes import router as jobs_router
from living_routes import router as living_router
from manuscript_routes import router as manuscript_router
from observability import RequestIdMiddleware, configure_logging, configure_sentry
from other_sources_routes import router as other_sources_router
from projects_routes import router as projects_router
from protocol_design_routes import catalog_router as protocol_catalog_router
from protocol_design_routes import router as protocol_design_router
from publication_routes import router as publication_router
from rate_limiting import ai_rate_limit
from records_routes import router as records_router
from registration_routes import router as registration_router
from review_routes import router as review_router
from screening_routes import router as screening_router
from search_quality_routes import router as search_quality_router
from search_quality_routes import vocabulary_router
from security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from services.ai_help import answer_help
from services.errors import LLMError
from studies_routes import router as studies_router
from synthesis_routes import router as synthesis_router
from tokens_routes import router as tokens_router
from topic_routes import router as topic_router
from webhook_routes import router as webhook_router
from workflow import WorkflowError
from workflow_routes import router as workflow_router

configure_logging()
configure_sentry()
config_guard.enforce()
logger = logging.getLogger(__name__)

API_DESCRIPTION = """\
The OmniReview API. Everything the web app does goes through these routes.

**Authentication.** Send a bearer token in the `Authorization` header. A personal access token (created under
Settings, and starting `omr_`) is meant for scripts and other systems: it carries a read or write scope, can be
limited to particular projects and given an expiry, and can never create tokens or reach the administration routes.
Signing in through `/api/auth/login` returns a session token for the web app instead.

**Permissions.** Every project route checks the caller's role on that project, so a token can only do what its owner
can do. Projects the caller doesn't belong to answer 404 rather than 403, so project ids don't reveal which projects
exist.

**Human decisions.** AI output is stored as suggestions with their provenance; inclusion, extraction values, risk of
bias judgments, GRADE ratings, and export approvals are always recorded against a person.

**Webhooks.** Projects can subscribe to audit events. Each delivery carries `X-OmniReview-Event`,
`X-OmniReview-Delivery`, and `X-OmniReview-Signature: sha256=<hex>`, an HMAC-SHA256 of the exact request body using
the subscription's secret. Verify the signature before trusting a delivery.
"""

app = FastAPI(title="OmniReview API", description=API_DESCRIPTION)

cors_origins = [
    origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
# Added last so it wraps everything, including rejected and CORS responses.
app.add_middleware(RequestIdMiddleware)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    """Whether this instance can serve requests: database, schema version, Redis (when configured), and storage."""
    checks = ops.readiness(db)
    summary = {check.name: check.status for check in checks}
    failed = [check for check in checks if check.status == "fail"]
    if failed:
        logger.error("Readiness check failed: %s", "; ".join(f"{c.name}: {c.detail}" for c in failed))
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": summary})
    return {"status": "ready", "checks": summary}


app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(review_router)
app.include_router(records_router)
app.include_router(screening_router)
app.include_router(audit_router)
app.include_router(workflow_router)
app.include_router(ai_router)
app.include_router(jobs_router)
app.include_router(protocol_design_router)
app.include_router(protocol_catalog_router)
app.include_router(topic_router)
app.include_router(registration_router)
app.include_router(search_quality_router)
app.include_router(vocabulary_router)
app.include_router(other_sources_router)
app.include_router(documents_router)
app.include_router(studies_router)
app.include_router(extraction_router)
app.include_router(entities_router)
app.include_router(appraisal_router)
app.include_router(synthesis_router)
app.include_router(certainty_router)
app.include_router(manuscript_router)
app.include_router(publication_router)
app.include_router(living_router)
app.include_router(collaboration_router)
app.include_router(invitations_router)
app.include_router(notifications_router)
app.include_router(collaboration_me_router)
app.include_router(help_router)
app.include_router(governance_router)
app.include_router(admin_router)
app.include_router(interop_router)
app.include_router(webhook_router)
app.include_router(tokens_router)
app.include_router(account_router)
app.include_router(billing_router)
app.include_router(admin_console_router)


@app.exception_handler(TaskNotReady)
async def task_not_ready_handler(request: Request, exc: TaskNotReady) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request: Request, exc: WorkflowError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


# Routes that aren't tied to a project. Every route here requires a valid login token.
api = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(ai_rate_limit)])


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)


@api.post("/chat")
async def api_chat_faq(req: ChatRequest, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The support assistant answers from the product documentation and cites the sections it used.

    It uses the catalog's default model, with the caller's own key when they've saved one.
    """
    sections = help_center.search(req.query)
    if not sections:
        return {
            "answer": (
                "The documentation doesn't cover that. Try asking about screening, extraction, meta-analysis, "
                "certainty of evidence, the manuscript, or collaboration."
            ),
            "citations": [],
        }
    model = default_model(db)
    if model is None:
        raise HTTPException(status_code=503, detail="The assistant is unavailable because no default AI model is set")
    ai = resolve_ai(db, model, user)
    try:
        result = await answer_help(ai, req.query, sections)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    cited = {section_id for section_id in result.value.citations}
    used = [section for section in sections if section.id in cited] or sections[:1]
    return {"answer": result.value.answer, "citations": [help_center.section_out(section) for section in used]}


# Included after the routes are declared; FastAPI copies a router's routes at include time.
app.include_router(api)


def custom_openapi() -> dict[str, Any]:
    """Document the bearer token, which is checked in a dependency rather than declared per route."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": "A personal access token (omr_…) or a session token from /api/auth/login.",
        }
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
