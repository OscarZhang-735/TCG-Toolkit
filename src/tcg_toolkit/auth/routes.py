"""Bearer browser authentication routes and shared access dependency."""
from collections import deque
import os
from threading import Lock
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from .service import AuthService, DuplicateUsername
from .store import UserStore
from .email_verification import normalize_email

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=18)


class EmailInput(BaseModel):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class CodeRequest(EmailInput):
    language: Literal["en", "zh-CN"] = "en"


class BindingCodeRequest(CodeRequest):
    current_password: str = Field(min_length=8, max_length=18)


class CodeInput(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


class RegistrationCredentials(Credentials, EmailInput, CodeInput):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    confirm_password: str = Field(min_length=8, max_length=18)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class RateLimiter:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def check(self, key, limit):
        now = time.monotonic()
        with self.lock:
            for old_key in list(self.entries):
                queue = self.entries[old_key]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if not queue:
                    del self.entries[old_key]
            queue = self.entries.setdefault(key, deque())
            if len(queue) >= limit:
                raise HTTPException(429, detail="rate_limited", headers={"Retry-After": "60"})
            queue.append(now)


def initialize_auth(app):
    app.state.auth = AuthService(UserStore(os.getenv("TCG_USER_DB", "data/users.sqlite3")))
    app.state.auth_limiter = RateLimiter()
    app.state.auth_origins = {value.strip().rstrip("/") for value in
                              os.getenv("TCG_TRUSTED_ORIGINS", "").split(",") if value.strip()}


def service(request: Request):
    return request.app.state.auth


def bearer_token(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token and " " not in token else None


def current_user(request: Request):
    user = service(request).current_user(bearer_token(request))
    if user is None:
        raise HTTPException(401, detail="authentication_required")
    return user


def write_guard(request: Request):
    if request.headers.get("origin") not in request.app.state.auth_origins:
        raise HTTPException(403, detail="untrusted_origin")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, detail="json_required")


def credentials_guard(request: Request):
    write_guard(request)
    action = request.url.path.rsplit("/", 1)[-1]
    ip = request.client.host if request.client else "unknown"
    request.app.state.auth_limiter.check((action, ip), 5 if action in ("email-code", "bind-code") else 10)


@router.post("/register", status_code=201, dependencies=[Depends(credentials_guard)])
def register(body: RegistrationCredentials, request: Request, response: Response):
    try:
        user, token = service(request).register(body.username, body.password, body.email, body.challenge_id, body.code)
    except DuplicateUsername:
        raise HTTPException(409, detail="username_taken") from None
    response.headers["Cache-Control"] = "no-store"
    return {"user": user, "token": token}


@router.post("/register/email-code", dependencies=[Depends(credentials_guard)])
def registration_code(body: CodeRequest, request: Request):
    return service(request).verification.send(body.email, body.language)


@router.post("/email/bind-code", dependencies=[Depends(credentials_guard)])
def binding_code(body: BindingCodeRequest, request: Request, user=Depends(current_user)):
    return service(request).send_binding_code(user["id"], body.email, body.current_password, body.language)


@router.post("/email/bind", dependencies=[Depends(credentials_guard)])
def bind_email(body: CodeInput, request: Request, user=Depends(current_user)):
    return service(request).bind_email(user["id"], body.challenge_id, body.code)


@router.post("/login", dependencies=[Depends(credentials_guard)])
def login(body: Credentials, request: Request, response: Response):
    result = service(request).login(body.username, body.password)
    if result is None:
        raise HTTPException(401, detail="invalid_credentials")
    user, token = result
    old_token = bearer_token(request)
    if old_token:
        service(request).logout(old_token)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user, "token": token}


@router.post("/logout", status_code=204, dependencies=[Depends(write_guard)])
def logout(request: Request):
    token = bearer_token(request)
    if token:
        service(request).logout(token)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    return response


@router.get("/me")
def me(response: Response, user=Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return user
