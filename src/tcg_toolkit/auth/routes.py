"""Bearer authentication and account HTTP endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from ..persistence.models import User
from .avatar import AVATAR_LIMIT, AVATAR_TYPES, save_avatar
from .dependencies import bearer_token, credentials_guard, current_user, require_trusted_origin, service, write_guard
from .schemas import (BindingCodeRequest, CodeInput, CodeRequest, Credentials, NicknameInput,
                      PasswordChange, PasswordCodeRequest, PasswordReset, RegistrationCredentials)
from .service import DuplicateUsername

router = APIRouter(prefix="/api/auth")


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


@router.post("/password/change/email-code", dependencies=[Depends(credentials_guard)])
def password_change_code(body: PasswordCodeRequest, request: Request, user=Depends(current_user)):
    return service(request).send_password_code(user["id"], body.language)


@router.post("/password/change", status_code=204, dependencies=[Depends(credentials_guard)])
def change_password(body: PasswordChange, request: Request, user=Depends(current_user)):
    service(request).change_password(user["id"], bearer_token(request), body.new_password, body.method,
                                    body.current_password, body.challenge_id, body.code)
    return Response(status_code=204)


@router.post("/password/reset/email-code", dependencies=[Depends(credentials_guard)])
def password_reset_code(body: CodeRequest, request: Request):
    return service(request).verification.send(body.email, body.language, "password_reset")


@router.post("/password/reset", status_code=204, dependencies=[Depends(credentials_guard)])
def reset_password(body: PasswordReset, request: Request):
    service(request).reset_password(body.email, body.challenge_id, body.code, body.new_password)
    return Response(status_code=204)


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


@router.put("/nickname", dependencies=[Depends(write_guard)])
def change_nickname(body: NicknameInput, request: Request, user=Depends(current_user)):
    with service(request).store.session() as db:
        db.get(User, user['id']).nickname = body.nickname
    return {'nickname': body.nickname}


@router.put("/avatar", dependencies=[Depends(require_trusted_origin)])
async def change_avatar(request: Request, user=Depends(current_user)):
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in AVATAR_TYPES:
        raise HTTPException(415, detail="avatar_invalid")
    request.app.state.auth_limiter.check(("avatar", user["id"]), 5)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > AVATAR_LIMIT:
            raise HTTPException(413, detail="avatar_too_large")
        content.extend(chunk)
    return await run_in_threadpool(save_avatar, service(request).store, user["id"], content, media_type)
