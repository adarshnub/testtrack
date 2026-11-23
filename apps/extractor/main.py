import csv
from hmac import compare_digest
import os
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from exporters.excel import generate_excel
from exporters.iif import generate, preview
from parsers.inspection import PDFError
from parsers.parser_registry import extract
from reconciliation import reconcile
from registers import parse_chart, parse_register
from schemas import ExportInput, Extraction, Reconciliation, ReconciliationInput

app = FastAPI(title="BankLedger Pro Extractor", docs_url=None, redoc_url=None, openapi_url=None)


def require_service(authorization: Annotated[str | None, Header()] = None):
    token = os.environ.get("EXTRACTOR_TOKEN", "")
    if len(token) < 24:
        raise HTTPException(503, detail={"code": "SERVICE_CONFIG", "message": "The extraction service token is not configured."})
    if not authorization or not compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "Extraction service authentication failed."})


@app.exception_handler(RequestValidationError)
async def request_validation(_request: Request, _error: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_CONTRACT",
                         "message": "The financial processing service rejected the request fields."}})


async def bounded_body(request: Request, limit: int) -> bytes:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise HTTPException(413, detail={"code": "FILE_SIZE", "message": "The uploaded file exceeds the size limit."})
        content.extend(chunk)
    if not content:
        raise HTTPException(400, detail={"code": "EMPTY_FILE", "message": "Upload a nonempty file."})
    return bytes(content)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/extract", response_model=Extraction, dependencies=[Depends(require_service)])
async def extract_pdf(request: Request, account_last4: Annotated[str | None, Query(pattern=r"^\d{4}$")] = None):
    if request.headers.get("content-type") != "application/pdf":
        raise HTTPException(415, detail={"code": "FILE_TYPE", "message": "Upload a PDF statement."})
    data = await bounded_body(request, int(os.environ.get("MAX_PDF_BYTES", "20971520")))
    try:
        return await run_in_threadpool(extract, data, int(os.environ.get("MAX_PDF_PAGES", "100")), account_last4)
    except PDFError as error:
        raise HTTPException(422, detail={"code": error.code, "message": str(error)}) from None


@app.post("/v1/reconcile", response_model=Reconciliation, dependencies=[Depends(require_service)])
def reconcile_statement(body: ReconciliationInput):
    return reconcile(body)


@app.post("/v1/preview", dependencies=[Depends(require_service)])
def preview_export(body: ExportInput):
    return preview(body)


@app.post("/v1/exports/{format}", dependencies=[Depends(require_service)])
def generate_export(format: Literal["iif", "excel"], body: ExportInput):
    try:
        content = generate(body) if format == "iif" else generate_excel(body)
    except ValueError as error:
        if format == "excel":
            raise HTTPException(422, detail={"code": "EXPORT_INVALID", "message": "The workbook could not be generated from the review fields."}) from None
        raise HTTPException(422, detail={"code": "EXPORT_INVALID", "message": str(error)}) from None
    return Response(content, media_type="application/octet-stream" if format == "iif" else
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/v1/tables/{kind}", dependencies=[Depends(require_service)])
async def normalize_table(request: Request, kind: Literal["chart", "register"], file_type: Literal["csv", "xlsx"],
                          debit_side: Literal["DEBIT_IN", "DEBIT_OUT"] = "DEBIT_IN"):
    data = await bounded_body(request, 8 * 1024 * 1024)
    try:
        return await run_in_threadpool(parse_chart, data, file_type) if kind == "chart" else await run_in_threadpool(
            parse_register, data, file_type, debit_side,
        )
    except (ValueError, UnicodeError, csv.Error) as error:
        raise HTTPException(422, detail={"code": "INVALID_SPREADSHEET", "message": str(error)}) from None
