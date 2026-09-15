import logging
import os

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import models
from ai_access import resolve_ai
from ai_catalog import default_model
from ai_routes import router as ai_router
from ai_tasks import TaskNotReady
from audit_routes import router as audit_router
from auth_routes import get_current_user
from auth_routes import router as auth_router
from database import engine, get_db
from jobs_routes import router as jobs_router
from observability import RequestIdMiddleware, configure_logging, configure_sentry
from other_sources_routes import router as other_sources_router
from projects_routes import router as projects_router
from protocol_design_routes import catalog_router as protocol_catalog_router
from protocol_design_routes import router as protocol_design_router
from rate_limiting import ai_rate_limit
from records_routes import router as records_router
from registration_routes import router as registration_router
from review_routes import router as review_router
from screening_routes import router as screening_router
from search_quality_routes import router as search_quality_router
from search_quality_routes import vocabulary_router
from security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from services.ai_screening import answer_faq
from services.errors import LLMError
from topic_routes import router as topic_router
from workflow import WorkflowError
from workflow_routes import router as workflow_router

configure_logging()
configure_sentry()
logger = logging.getLogger(__name__)

app = FastAPI(title="OmniReview API")

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
def readyz():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database readiness check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ready"}


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
    """The support assistant uses the catalog's default model, with the caller's own key when they've saved one."""
    model = default_model(db)
    if model is None:
        raise HTTPException(status_code=503, detail="The assistant is unavailable because no default AI model is set")
    ai = resolve_ai(db, model, user)
    try:
        result = await answer_faq(ai, req.query)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"answer": result.value}


# Included after the routes are declared; FastAPI copies a router's routes at include time.
app.include_router(api)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
