from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

NO_STORE = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None, extra: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        self.extra = extra or {}


def not_found() -> AppError:
    """The one 404. Missing, foreign and malformed ids all raise exactly this."""
    return AppError(404, "not_found", "Not found")


def _body(request: Request, code: str, message: str, extra: dict | None = None) -> dict:
    body = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if extra:
        body.update(extra)
    return body


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.extra), exc.status, {**NO_STORE, **exc.headers})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        # Unknown routes and 405s: same body as any other 404, no framework text.
        if exc.status_code == 404:
            return JSONResponse(_body(request, "not_found", "Not found"), 404, NO_STORE)
        return JSONResponse(_body(request, "http_error", "Request not allowed"), exc.status_code, NO_STORE)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Name the offending fields, never echo the submitted values.
        fields = sorted({".".join(str(p) for p in e["loc"] if p != "body") or "body" for e in exc.errors()})
        return JSONResponse(
            _body(request, "validation_error", "Invalid field: " + ", ".join(fields), {"fields": fields}), 422, NO_STORE
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        import logging

        logging.getLogger("tracker").error("unhandled %s request_id=%s", type(exc).__name__, getattr(request.state, "request_id", None))
        return JSONResponse(_body(request, "internal_error", "Something went wrong"), 500, NO_STORE)
