"""HTTP access dependencies, independent of account route registration."""
from fastapi import HTTPException, Request


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


def client_ip(request: Request):
    return request.client.host if request.client else "unknown"


def guest_access(action: str, limit: int):
    """Allow signed-out visitors with a per-IP limit; a token that is sent must still be valid."""
    def dependency(request: Request):
        token = bearer_token(request)
        if token is not None:
            return current_user(request)
        request.app.state.auth_limiter.check((f"guest-{action}", client_ip(request)), limit)
        return None
    return dependency


def require_trusted_origin(request: Request):
    if request.headers.get("origin") not in request.app.state.auth_origins:
        raise HTTPException(403, detail="untrusted_origin")


def write_guard(request: Request):
    require_trusted_origin(request)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, detail="json_required")


def credentials_guard(request: Request):
    write_guard(request)
    action = request.url.path.rsplit("/", 1)[-1]
    request.app.state.auth_limiter.check((action, client_ip(request)), 5 if action in ("email-code", "bind-code") else 10)


