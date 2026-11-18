import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, TimeoutError
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


def security_headers(request):
    return {"X-Request-ID": request.state.request_id,
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False, field_errors: list | None = None):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable
        self.field_errors = field_errors


def install_errors(app: FastAPI):
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        # Generate locally instead of trusting arbitrary client-controlled log identifiers.
        request.state.request_id = f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers.update(security_headers(request))
        return response

    def response(request, status, code, message, fields=None, retryable=False):
        return JSONResponse(status_code=status, headers=security_headers(request), content={
            "error": {"code": code, "message": message, "field_errors": fields or [],
                      "retryable": retryable}, "request_id": request.state.request_id})

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return response(request, exc.status, exc.code, exc.message, fields=exc.field_errors, retryable=exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        # Do not echo raw input: it can contain passwords, tokens and child content.
        fields = [{"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                  for error in exc.errors()]
        return response(request, 422, "validation_error", "请求参数不符合约定", fields)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return response(request, exc.status_code, "not_found" if exc.status_code == 404 else "http_error", "资源不存在" if exc.status_code == 404 else "请求无法处理")

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return response(request, 409, "conflict", "数据冲突，请刷新后重试")

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    @app.exception_handler(TimeoutError)
    async def database_unavailable(request, exc):
        logger.error("request_id=%s database_failure=%s", request.state.request_id, type(exc).__name__)
        return response(request, 503, "database_unavailable", "数据库暂时不可用，请稍后重试", retryable=True)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        # Exception text, SQL parameters and tracebacks can contain child data/secrets.
        logger.error("request_id=%s unexpected_failure=%s", request.state.request_id, type(exc).__name__)
        return response(request, 500, "internal_error", "服务暂时无法处理请求", retryable=True)


def data(request: Request, value):
    return jsonable_encoder({"data": value, "request_id": request.state.request_id})
