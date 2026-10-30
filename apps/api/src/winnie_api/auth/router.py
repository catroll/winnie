"""Account and session HTTP API, mounted under /api/v1/auth."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from winnie_api.auth.dependencies import bearer_token, get_current_user
from winnie_api.auth.errors import (
    InvalidCredentials,
    LoginLocked,
    RegistrationClosed,
    UsernameTaken,
)
from winnie_api.auth.models import User
from winnie_api.auth.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    TokenRead,
    UserRead,
)
from winnie_api.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="authentication is disabled")
    return service


def _token_response(
    user: User,
    token: str,
    expires_at: datetime,
) -> TokenRead:
    return TokenRead(
        access_token=token,
        expires_at=expires_at,
        user=UserRead.model_validate(user),
    )


@router.post("/register", response_model=TokenRead, status_code=201)
async def register(
    payload: RegisterRequest,
    service: AuthService = Depends(get_service),
) -> TokenRead:
    try:
        user, token, expires_at = await service.register(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
        )
    except RegistrationClosed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UsernameTaken as exc:
        raise HTTPException(status_code=409, detail="username is already taken") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _token_response(user, token, expires_at)


@router.post("/login", response_model=TokenRead)
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_service),
) -> TokenRead:
    try:
        user, token, expires_at = await service.login(
            username=payload.username,
            password=payload.password,
        )
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=401,
            detail="invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except LoginLocked as exc:
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    return _token_response(user, token, expires_at)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    _user: User = Depends(get_current_user),
    service: AuthService = Depends(get_service),
) -> Response:
    await service.logout(bearer_token(request))
    return Response(status_code=204)


@router.post("/password", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    service: AuthService = Depends(get_service),
) -> Response:
    try:
        await service.change_password(
            user_id=user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=401,
            detail="current password is incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(user)
