"""HTTP API for the assistant, mounted under /api/v1."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from winnie_core.auth.dependencies import get_user_id
from winnie_assistant.agent import AssistantModelError
from winnie_assistant.conversations import (
    ConversationNotFound,
    ConversationReadOnly,
    InvalidConversationId,
)
from winnie_assistant.index_client import IndexUnavailable
from winnie_assistant.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationListRead,
    ConversationRead,
    ConversationSummaryRead,
    DistillRead,
    DocumentHitRead,
    DocumentSearchRead,
    MemoryFileRead,
    MemoryListRead,
    MessageRead,
    StatusRead,
    TaskListRead,
    TaskRead,
    TaskRunListRead,
    TaskRunRead,
)
from winnie_assistant.service import AssistantService

router = APIRouter(prefix="/assistant", tags=["assistant"])

_UI = Path(__file__).with_name("web") / "chat.html"


def get_service(request: Request) -> AssistantService:
    service = getattr(request.app.state, "assistant_service", None)
    if service is None:
        error = getattr(request.app.state, "assistant_error", "")
        raise HTTPException(
            status_code=503,
            detail=error or "the assistant is disabled",
        )
    return service


def _memory(service: AssistantService):
    if service.memory is None:
        raise HTTPException(status_code=409, detail="memory is disabled")
    return service.memory


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def chat_page() -> str:
    """A phone-sized chat page. Public: it asks for the token itself."""
    return _UI.read_text(encoding="utf-8")


@router.get("/status", response_model=StatusRead)
async def status(
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> StatusRead:
    return StatusRead(**service.status())


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> ChatResponse:
    try:
        reply = await service.chat(
            payload.message.strip(),
            conversation_id=payload.conversation_id,
            channel=payload.channel,
        )
    except (InvalidConversationId, ConversationNotFound) as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    except ConversationReadOnly as exc:
        raise HTTPException(
            status_code=409,
            detail="a scheduled task run is read-only; start a new conversation",
        ) from exc
    except AssistantModelError as exc:
        raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc
    return ChatResponse(
        conversation_id=reply.conversation_id,
        reply=reply.text,
        tools_used=reply.tools_used,
        usage_tokens=reply.usage_tokens,
    )


@router.get("/conversations", response_model=ConversationListRead)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    channel: str | None = Query(default=None, max_length=32),
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> ConversationListRead:
    summaries = service.store.list(limit=limit, offset=offset, channel=channel)
    return ConversationListRead(
        items=[
            ConversationSummaryRead(
                id=summary.id,
                title=summary.title,
                created_at=summary.created_at,
                source=summary.source,
                channel=summary.channel,
                message_count=summary.message_count,
                preview=summary.preview,
            )
            for summary in summaries
        ],
        total=service.store.count(channel=channel),
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: str,
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> ConversationRead:
    try:
        conversation = service.store.get(conversation_id)
    except (InvalidConversationId, ConversationNotFound) as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc
    return ConversationRead(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        source=conversation.source,
        channel=conversation.channel,
        messages=[
            MessageRead(role=message.role, name=message.name, text=message.text, at=message.at)
            for message in conversation.messages
        ],
    )


@router.get("/memory", response_model=MemoryListRead)
async def list_memory(
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> MemoryListRead:
    memory = _memory(service)
    return MemoryListRead(
        files=[
            MemoryFileRead(
                path=file.relpath,
                category=file.category,
                title=file.title,
                entries=file.entries,
                pinned=file.pinned,
                updated=file.updated,
            )
            for file in memory.files()
        ],
        entries=len(memory.entries),
        digest=memory.digest(),
    )


@router.get("/documents/search", response_model=DocumentSearchRead)
async def search_documents(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> DocumentSearchRead:
    """Search the local index, which covers the assistant's own files too."""
    try:
        hits = await service.search_documents(q, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IndexUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DocumentSearchRead(
        query=q,
        hits=[
            DocumentHitRead(
                path=hit.path,
                root=hit.root,
                heading=hit.heading,
                start_line=hit.start_line,
                end_line=hit.end_line,
                snippet=hit.snippet,
                mtime=hit.mtime,
                also_at=list(hit.also_at),
            )
            for hit in hits
        ],
    )


@router.post("/memory/distill", response_model=DistillRead)
async def distill_memory(
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> DistillRead:
    if service.builder is None:
        raise HTTPException(status_code=409, detail="memory is disabled")
    try:
        result = await service.distill()
    except AssistantModelError as exc:
        raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc
    return DistillRead(
        conversations=result.conversations,
        facts=result.facts,
        empty=result.empty,
    )


@router.get("/tasks", response_model=TaskListRead)
async def list_tasks(
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> TaskListRead:
    tasks: list[TaskRead] = []
    for task in service.scheduler.tasks:
        state = service.scheduler.state_for(task.id)
        tasks.append(
            TaskRead(
                id=task.id,
                title=task.title,
                schedule=task.schedule,
                prompt=task.prompt,
                enabled=task.enabled,
                webhook=task.webhook,
                next_run=state.get("next_run"),
                last_run=state.get("last_run"),
                last_status=state.get("last_status"),
                last_error=state.get("last_error"),
            )
        )
    return TaskListRead(tasks=tasks, errors=service.scheduler.errors)


@router.get("/tasks/runs", response_model=TaskRunListRead)
async def list_task_runs(
    task_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=5000),
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> TaskRunListRead:
    """Recorded task executions, newest first, optionally for one task."""
    page = service.task_runs(task_id=task_id or None, limit=limit, offset=offset)
    return TaskRunListRead(
        runs=[
            TaskRunRead(
                task_id=run.task_id,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                conversation_id=run.conversation_id,
                error=run.error,
                delivery_error=run.delivery_error,
            )
            for run in page.runs
        ],
        total=page.total,
        offset=offset,
        limit=limit,
        skipped=page.skipped,
    )


@router.post("/tasks/{task_id}/run", response_model=TaskRunRead)
async def run_task(
    task_id: str,
    service: AssistantService = Depends(get_service),
    _: str = Depends(get_user_id),
) -> TaskRunRead:
    try:
        record = await service.run_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown task: {task_id}") from exc
    return TaskRunRead(
        task_id=record.task_id,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        conversation_id=record.conversation_id,
        error=record.error,
        delivery_error=record.delivery_error,
    )
