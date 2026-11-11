"""FastAPI application. Local demo only; deployment auth/HTTPS is a C concern."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.planning import router
from backend.errors import PlanningError

app = FastAPI(title="ShelterPlan Module B", version="0.1.0", description="Constrained planning service for synthetic fixtures and verified Module A handoffs.")
app.include_router(router)


@app.middleware("http")
async def enforce_json_body_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > 1_048_576:
                return JSONResponse(status_code=413, content={"error_code": "REQUEST_TOO_LARGE", "retryable": False})
        except ValueError:
            return JSONResponse(status_code=400, content={"error_code": "INVALID_CONTENT_LENGTH", "retryable": False})
    return await call_next(request)


@app.exception_handler(PlanningError)
async def planning_error_handler(_request: Request, exc: PlanningError):
    return JSONResponse(status_code=409 if exc.error_code in {"DATA_UNAVAILABLE", "SCENARIO_INVALID"} else 422, content={
        "error_code": exc.error_code, "details": exc.details,
        "message": str(exc), "retryable": exc.retryable,
        "required_user_action": exc.required_user_action,
    })


@app.get("/health")
def health():
    return {"status": "ok", "service": "shelterplan-module-b", "deployed": False}
