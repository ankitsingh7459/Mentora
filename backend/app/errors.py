from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException


class Error(BaseModel):
    code: str
    message: str
    requestId: str
    details: list[dict[str, str]]


class ErrorResponse(BaseModel):
    error: Error


class PublicError(Exception):
    """Only static, reviewed public messages/codes belong here; never raw exceptions."""
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


async def public_error(request: Request, exc: PublicError):
    response = error_response(request, exc.status, exc.code, exc.message)
    if exc.status == 401:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


def error_response(request: Request, status: int, code: str, message: str, details=None):
    body = ErrorResponse(error=Error(
        code=code, message=message,
        requestId=getattr(request.state, "request_id", str(uuid4())),
        details=details or [],
    ))
    return JSONResponse(status_code=status, content=body.model_dump())


async def http_error(request: Request, exc: HTTPException):
    # Exception detail can contain private data; use a public message by status.
    codes = {404: ("NOT_FOUND", "Resource not found."), 405: ("METHOD_NOT_ALLOWED", "Method not allowed.")}
    code, message = codes.get(exc.status_code, ("HTTP_ERROR", "Request could not be completed."))
    response = error_response(request, exc.status_code, code, message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def validation_error(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(map(str, e["loc"])), "code": e["type"]} for e in exc.errors()]
    return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed.", details)


async def unexpected_error(request: Request, exc: Exception):
    return error_response(request, 500, "INTERNAL_ERROR", "Internal server error.")
