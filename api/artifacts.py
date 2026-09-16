"""Source-grounded artifact generation endpoints.

Thin adapter over the chat tool loop: each artifact type is an instruction
message run through /api/chat, so grounding, citations, structured-table
tools, and the sources list are all reused rather than reimplemented.
"""

import logging
import asyncio

from fastapi import Header, HTTPException, Request

from models.schemas import (
    ChatMessage,
    ChatRequest,
    ArtifactRequest,
    ArtifactResponse,
)

from fastapi import APIRouter
from api.chat import chat_with_documents

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/artifacts/types", tags=["artifacts"])
async def list_artifact_types_endpoint() -> dict:
    """List the available artifact types for the generate UI."""
    from services import artifacts as artifacts_service

    return {"types": artifacts_service.list_artifact_types()}


@router.post("/api/artifacts", response_model=ArtifactResponse, tags=["artifacts"])
async def generate_artifact(
    artifact_request: ArtifactRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> ArtifactResponse:
    """Generate a source-grounded artifact (summary, FAQ, timeline, etc.).

    Reuses the chat tool loop end-to-end: builds a templated instruction for the
    requested artifact type, runs it through `chat_with_documents`, and repackages
    the grounded answer + sources as an artifact.
    """
    from services import artifacts as artifacts_service

    if not artifacts_service.is_valid_type(artifact_request.artifact_type):
        valid = ", ".join(t["type"] for t in artifacts_service.list_artifact_types())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown artifact type '{artifact_request.artifact_type}'. Valid: {valid}",
        )

    instruction = artifacts_service.build_artifact_message(
        artifact_request.artifact_type,
        focus=artifact_request.focus,
        custom_instructions=artifact_request.custom_instructions,
    )

    chat_request = ChatRequest(
        messages=[ChatMessage(role="user", content=instruction)],
        provider=artifact_request.provider,
        mode=artifact_request.mode,
        scope=artifact_request.scope,
        rerank=False,
        top_k=artifact_request.top_k,
        document_ids=artifact_request.document_ids,
    )

    # chat_with_documents is a sync handler now — run it in a worker thread
    chat_response = await asyncio.to_thread(
        chat_with_documents,
        chat_request,
        request,
        collection_id=collection_id,
        x_ai_key=x_ai_key,
        x_ollama_model=x_ollama_model,
        x_anthropic_model=x_anthropic_model,
        x_openai_model=x_openai_model,
        x_ai_model=x_ai_model,
        x_ai_base_url=x_ai_base_url,
    )

    return ArtifactResponse(
        artifact_type=artifact_request.artifact_type,
        title=artifacts_service.artifact_title(
            artifact_request.artifact_type, artifact_request.focus
        ),
        content=chat_response.message.content,
        sources=chat_response.sources,
        ai_usage=chat_response.ai_usage,
    )
