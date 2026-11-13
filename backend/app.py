import asyncio
import ipaddress
import logging
import re
import secrets
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.assistant import (
    MAX_HISTORY_CHARS,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_CHARS,
    MAX_SESSION_MESSAGES,
    AssistantError,
    AzureSelector,
    ChatRequest,
    ChatResponse,
    EduardoAssistant,
    Selector,
)
from backend.config import ROOT, Settings
from backend.knowledge import KnowledgeBase, load_knowledge

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 60
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class RateLimiter:
    def __init__(self) -> None:
        self.requests: dict[str, deque[float]] = {}

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        for key in list(self.requests):
            queue = self.requests[key]
            while queue and queue[0] <= now - RATE_WINDOW_SECONDS:
                queue.popleft()
            if not queue:
                del self.requests[key]
        if identity not in self.requests:
            if len(self.requests) >= 1024:
                return False
            self.requests[identity] = deque()
        queue = self.requests[identity]
        if len(queue) >= RATE_LIMIT:
            return False
        queue.append(now)
        return True


def authenticated_identity(request: Request, settings: Settings) -> str | None:
    if settings.auth_mode == "local":
        if request.client is None:
            return None
        try:
            address = ipaddress.ip_address(request.client.host)
        except ValueError:
            return None
        return "local" if address.is_loopback else None
    if request.headers.get("x-ms-client-principal-idp") != "aad":
        return None
    principal = request.headers.get("x-ms-client-principal-id", "")
    try:
        return str(uuid.UUID(principal))
    except ValueError:
        return None


def create_app(
    settings: Settings | None = None,
    *,
    knowledge: KnowledgeBase | None = None,
    selector: Selector | None = None,
) -> FastAPI:
    if settings is None:
        load_dotenv(ROOT / ".env")
        settings = Settings.from_environment()
    settings.validate_hosting()
    knowledge = knowledge if knowledge is not None else load_knowledge(settings.knowledge_path)
    selector = selector if selector is not None else AzureSelector(settings)
    assistant = EduardoAssistant(knowledge, selector, settings.timeout_seconds)
    limiter = RateLimiter()
    semaphore = asyncio.Semaphore(4)
    cookie_name = "__Host-eduardo-csrf" if settings.auth_mode == "appservice" else "eduardo-csrf"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            await selector.close()

    app = FastAPI(
        title="Pergunte ao Eduardo",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def protect(request: Request, call_next: RequestResponseEndpoint) -> Response:
        identity = authenticated_identity(request, settings)
        if identity is None:
            response = error_response(
                "authentication_required",
                "Acesso restrito. Entre com sua conta corporativa ou use o modo local.",
                401,
            )
        else:
            request.state.identity = identity
            response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.auth_mode == "appservice":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.get("/api/session")
    async def session(request: Request) -> JSONResponse:
        existing = request.cookies.get(cookie_name, "")
        token = existing if re.fullmatch(r"[a-zA-Z0-9_-]{43}", existing) else secrets.token_urlsafe(32)
        response = JSONResponse(
            {
                "csrf_token": token,
                "profile": knowledge.profile.model_dump(mode="json"),
                "approved_entries": len(knowledge.approved_entries),
                "limits": {
                    "message_chars": MAX_MESSAGE_CHARS,
                    "history_messages": MAX_HISTORY_MESSAGES,
                    "history_chars": MAX_HISTORY_CHARS,
                    "session_messages": MAX_SESSION_MESSAGES,
                },
            }
        )
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            secure=settings.auth_mode == "appservice",
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: Request) -> Response:
        scheme = "https" if settings.auth_mode == "appservice" else request.url.scheme
        expected_origin = f"{scheme}://{request.headers['host']}"
        origin = request.headers.get("origin", "")
        cookie = request.cookies.get(cookie_name, "")
        token = request.headers.get("x-csrf-token", "")
        if (
            origin != expected_origin
            or request.headers.get("sec-fetch-site") == "cross-site"
            or not cookie
            or len(cookie) > 128
            or len(token) > 128
            or not cookie.isascii()
            or not token.isascii()
            or not secrets.compare_digest(cookie, token)
        ):
            return error_response(
                "invalid_session", "A sessão expirou. Reconecte para continuar.", 403
            )
        if not limiter.allow(request.state.identity):
            response = error_response(
                "rate_limited", "Muitas perguntas. Aguarde um minuto antes de tentar novamente.", 429
            )
            response.headers["Retry-After"] = str(RATE_WINDOW_SECONDS)
            return response
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return error_response("invalid_content_type", "Envie uma mensagem JSON.", 415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                return error_response("body_too_large", "A mensagem enviada é muito grande.", 413)
            body.extend(chunk)
        try:
            payload = ChatRequest.model_validate_json(body)
        except ValidationError:
            return error_response(
                "invalid_input",
                "Revise a pergunta e o histórico: até 2.000 caracteres por pergunta "
                "e 10 mensagens de histórico, em pares, somando até 12.000 caracteres.",
                422,
            )
        if semaphore.locked():
            return error_response(
                "service_busy", "O assistente está ocupado. Tente novamente em instantes.", 503
            )
        try:
            async with semaphore:
                result = await assistant.answer(payload)
        except AssistantError as error:
            LOGGER.warning("chat_failure code=%s", error.code)
            return error_response(error.code, error.message, error.status)
        return JSONResponse(result.model_dump())

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(ROOT / "frontend" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
    return app


app = create_app()
