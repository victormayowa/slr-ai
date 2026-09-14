import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from auth_routes import get_current_user
from auth_routes import router as auth_router
from database import engine
from observability import RequestIdMiddleware, configure_logging, configure_sentry
from projects_routes import router as projects_router
from rate_limiting import rate_limit
from security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from services.ai_protocol import generate_protocol_elements
from services.ai_screening import (
    ROB_TOOL_DOMAINS,
    answer_faq,
    assess_risk_of_bias,
    evaluate_eligibility,
    extract_data_from_paper,
    generate_meta_analysis,
)
from services.errors import LLMError, SearchError
from services.openalex import search_openalex
from services.pubmed import search_pubmed

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
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
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

# Every route on this router requires a valid login token.
api = APIRouter(
    prefix="/api",
    dependencies=[Depends(get_current_user), Depends(rate_limit("ai", limit=120, window_seconds=60))],
)

Provider = Literal["gemini", "openai", "anthropic"]
Papers = list[dict[str, Any]]
MAX_PAPERS_PER_BATCH = 50
MAX_PAPERS_FOR_SYNTHESIS = 200


class ProtocolRequest(BaseModel):
    research_question: str = Field(min_length=1, max_length=20_000)
    provider: Provider = "gemini"


class SearchRequest(BaseModel):
    database: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(50, ge=1, le=200)


class ScreenRequest(BaseModel):
    papers: Papers = Field(max_length=MAX_PAPERS_PER_BATCH)
    criteria: str = Field(min_length=1, max_length=20_000)
    provider: Provider = "gemini"


class FullTextRequest(BaseModel):
    papers: Papers = Field(max_length=MAX_PAPERS_PER_BATCH)
    columns: list[str] = Field(min_length=1, max_length=50)
    provider: Provider = "gemini"


class RobRequest(BaseModel):
    papers: Papers = Field(max_length=MAX_PAPERS_PER_BATCH)
    tool: str
    provider: Provider = "gemini"


class MetaRequest(BaseModel):
    papers: Papers = Field(min_length=1, max_length=MAX_PAPERS_FOR_SYNTHESIS)
    provider: Provider = "gemini"


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    provider: Provider = "gemini"


def _paper_text(paper: dict[str, Any]) -> str:
    return f"Title: {paper.get('title', '')}\nAbstract: {paper.get('abstract', '')}"


async def _process_papers(
    papers: Papers,
    task: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    error_key: str,
) -> Papers:
    """Run an AI task per paper. A failed paper gets an error message, never a made-up result."""
    outcomes = await asyncio.gather(*(task(paper) for paper in papers), return_exceptions=True)
    results = []
    for paper, outcome in zip(papers, outcomes, strict=True):
        paper_copy = dict(paper)
        paper_copy.pop(error_key, None)
        if isinstance(outcome, LLMError):
            paper_copy[error_key] = str(outcome)
        elif isinstance(outcome, Exception):
            logger.error("Unexpected error while processing a paper", exc_info=outcome)
            paper_copy[error_key] = "Unexpected server error while processing this paper"
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            paper_copy.update(outcome)
        results.append(paper_copy)
    return results


async def _single_ai_call(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        return await call
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.post("/protocol/generate")
async def api_generate_protocol(req: ProtocolRequest):
    return await _single_ai_call(generate_protocol_elements(req.research_question, req.provider))


@api.post("/search")
async def api_search_database(req: SearchRequest):
    try:
        if req.database.strip().lower() == "pubmed":
            results = await asyncio.to_thread(search_pubmed, req.query, req.limit)
            return {"results": results, "source": "PubMed"}
        # No native connector yet for other databases; say so rather than labelling OpenAlex results as that database.
        results = await asyncio.to_thread(search_openalex, req.query, req.limit)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"results": results, "source": f"OpenAlex (no native {req.database} connector yet)"}


@api.post("/screen/abstract")
async def api_screen_abstracts(req: ScreenRequest):
    async def screen(paper: dict[str, Any]) -> dict[str, Any]:
        decision = await evaluate_eligibility(_paper_text(paper), req.criteria, req.provider)
        return {
            "ai_decision": decision["decision"],
            "ai_reasoning": decision.get("reasoning", ""),
            "ai_supporting_quote": decision.get("supporting_quote"),
        }

    return {"results": await _process_papers(req.papers, screen, "ai_error")}


@api.post("/screen/fulltext")
async def api_screen_fulltext(req: FullTextRequest):
    async def extract(paper: dict[str, Any]) -> dict[str, Any]:
        return {"extracted_data": await extract_data_from_paper(_paper_text(paper), req.columns, req.provider)}

    return {"results": await _process_papers(req.papers, extract, "extraction_error")}


@api.post("/screen/rob")
async def api_screen_rob(req: RobRequest):
    if req.tool not in ROB_TOOL_DOMAINS:
        raise HTTPException(status_code=400, detail=f"Unsupported risk of bias tool: {req.tool}")

    async def assess(paper: dict[str, Any]) -> dict[str, Any]:
        return {"rob_data": await assess_risk_of_bias(_paper_text(paper), req.tool, req.provider)}

    return {"results": await _process_papers(req.papers, assess, "rob_error")}


@api.post("/screen/meta")
async def api_screen_meta(req: MetaRequest):
    return await _single_ai_call(generate_meta_analysis(req.papers, req.provider))


@api.post("/chat")
async def api_chat_faq(req: ChatRequest):
    return await _single_ai_call(answer_faq(req.query, req.provider))


# Included after the routes are declared; FastAPI copies a router's routes at include time.
app.include_router(api)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
