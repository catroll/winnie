"""HTTP surface for the owner's weekly schedule.

The console edits Markdown files on the host, which is why the whole router is
administrator-only: the tree is not user data in a database, it is a directory
the service process can rewrite. Every mutation answers with the refreshed week,
so the page never has to guess which ids survived an insert or a delete.

Events are addressed by ``(week, id)`` rather than by a path segment: an id looks
like ``202610-05-11#10/05#2`` and contains ``#`` and ``/``, which do not survive
a URL path cleanly.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from winnie_core.auth.dependencies import admin_required
from winnie_schedule.editor import (
    AmbiguousError,
    NotFoundError,
    ReadOnlyError,
    ScheduleError,
    normalize_time,
)
from winnie_schedule.layout import LayoutError
from winnie_schedule.parser import ParseError

from .service import ScheduleService

router = APIRouter(
    prefix="/schedule",
    tags=["schedule"],
    dependencies=[Depends(admin_required)],
)

T = TypeVar("T")


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The day the event belongs to; it also selects the week file.
    date: date
    text: str = Field(min_length=1)
    # "HH:MM" or "HH:MM~HH:MM"; empty or absent means no time tag.
    time: str | None = None
    weekday: bool = False
    every: bool = False
    tags: list[str] = Field(default_factory=list)
    done: bool = False
    # Add to "## 工作事项" instead of the day section.
    week_scope: bool = False
    # Place the event right after this id instead of at the end of the block.
    after: str | None = None


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Any date inside the week that owns the event.
    week: date
    id: str
    text: str | None = None
    # Empty string clears the time tag; absent leaves it alone.
    time: str | None = None
    done: bool | None = None
    tags_add: list[str] = Field(default_factory=list)
    tags_remove: list[str] = Field(default_factory=list)
    # Appended as indented lines under the event.
    note: str | None = None
    # Move to another day of the same week: "MM/DD" or "YYYY-MM-DD".
    move_to: str | None = None


def get_service(request: Request) -> ScheduleService:
    service = getattr(request.app.state, "schedule_service", None)
    if service is None:
        detail = getattr(request.app.state, "schedule_error", "") or "the schedule is disabled"
        raise HTTPException(status_code=503, detail=detail)
    return service


def _translate(function: Callable[..., T]) -> Callable[..., T]:
    """Turn schedule domain errors into the status the console can act on."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return function(*args, **kwargs)
        except AmbiguousError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReadOnlyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LayoutError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ScheduleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return wrapper


@router.get("/week")
@_translate
def week(
    request: Request,
    date: date | None = Query(None, description="any day of the week; defaults to today"),
) -> dict[str, Any]:
    return get_service(request).week(date)


@router.post("/events")
@_translate
def create_event(body: EventCreate, request: Request) -> dict[str, Any]:
    start, end = _split_time(body.time)
    return get_service(request).add(
        day=body.date,
        text=body.text,
        start=start,
        end=end,
        weekday=body.weekday,
        every=body.every,
        tags=body.tags,
        done=body.done,
        week_scope=body.week_scope,
        after=body.after,
    )


@router.patch("/events")
@_translate
def update_event(body: EventUpdate, request: Request) -> dict[str, Any]:
    return get_service(request).update(
        week_date=body.week,
        event_id=body.id,
        text=body.text,
        time=body.time,
        done=body.done,
        tags_add=body.tags_add,
        tags_remove=body.tags_remove,
        note=body.note,
        move_to=body.move_to,
    )


@router.delete("/events")
@_translate
def delete_event(
    request: Request,
    week: date = Query(..., description="any day of the week that owns the event"),
    id: str = Query(..., description="event id, as returned by GET /schedule/week"),
) -> dict[str, Any]:
    return get_service(request).delete(week_date=week, event_id=id)


def _split_time(value: str | None) -> tuple[str | None, str | None]:
    """Validate a time tag on the way in, so a bad one is a 400 not a 500."""
    if not value or not value.strip():
        return None, None
    return normalize_time(value)
