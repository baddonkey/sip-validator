import asyncio
import logging
import re
import secrets
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Security, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from .config import load_settings
from .jobs import FINISHED, Job, JobManager, JobStatus
from .kostval import KostValError, KostValRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = load_settings()
runner = KostValRunner(settings)
manager = JobManager(settings, runner)

CHUNK_SIZE = 1024 * 1024
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class Language(str, Enum):
    de = "de"
    fr = "fr"
    it = "it"
    en = "en"


class LogType(str, Enum):
    xml = "xml"
    min = "min"
    max = "max"


class ReportError(BaseModel):
    module: str
    message: str


class JobOut(BaseModel):
    id: str
    status: JobStatus
    sip_name: str
    language: str
    log_type: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    message: str | None
    errors: list[ReportError]
    report_url: str | None

    @classmethod
    def from_job(cls, job: Job) -> "JobOut":
        return cls(
            id=job.id,
            status=job.status,
            sip_name=job.sip_name,
            language=job.language,
            log_type=job.log_type,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            exit_code=job.exit_code,
            message=job.message,
            errors=[ReportError(**e) for e in job.errors],
            report_url=f"/validations/{job.id}/report" if job.report_path else None,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    worker = asyncio.create_task(manager.run_forever())
    yield
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(api_key_header)) -> None:
    if settings.api_key and not (key and secrets.compare_digest(key, settings.api_key)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")


app = FastAPI(
    title="SIP Validator Service",
    description="REST service for validating SIPs with the KOST-Val CLI (KOST-Tools) running under Wine.",
    version="1.0.0",
    lifespan=lifespan,
)
router = APIRouter(prefix="/validations", tags=["validations"], dependencies=[Depends(require_api_key)])


def safe_sip_name(filename: str | None) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    normalized = _UNSAFE_CHARS.sub("_", name).lstrip(".")[-200:]
    if name != normalized:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SIP filename contains unsupported characters; the filename must be preserved exactly",
        )
    if not name.lower().endswith((".zip", ".zip64")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SIP must be a .zip or .zip64 file")
    return name


def get_job(job_id: uuid.UUID) -> Job:
    job = manager.jobs.get(str(job_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Validation job not found")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobOut)
async def create_validation(
    sip: UploadFile = File(..., description="Zipped SIP (eCH-0160)"),
    language: Language = Form(Language.de),
    log_type: LogType = Form(LogType.xml),
) -> JobOut:
    """Uploads a SIP and queues it for validation. Poll the returned job until it is finished."""
    sip_name = safe_sip_name(sip.filename)
    job_id = str(uuid.uuid4())
    job_dir = manager.job_dir(job_id)
    dest = job_dir / "sip" / sip_name
    dest.parent.mkdir(parents=True)

    try:
        size = 0
        with dest.open("wb") as fh:
            while chunk := await sip.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "SIP exceeds upload limit")
                await run_in_threadpool(fh.write, chunk)
        if not await run_in_threadpool(zipfile.is_zipfile, dest):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is not a valid ZIP archive")
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    job = Job(id=job_id, sip_name=sip_name, language=language.value, log_type=log_type.value)
    manager.submit(job)
    return JobOut.from_job(job)


@router.get("", response_model=list[JobOut])
@router.get("/", response_model=list[JobOut], include_in_schema=False)
async def list_validations() -> list[JobOut]:
    """Lists all known validation jobs, newest first."""
    jobs = sorted(manager.jobs.values(), key=lambda j: j.created_at, reverse=True)
    return [JobOut.from_job(j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
async def get_validation(job: Job = Depends(get_job)) -> JobOut:
    return JobOut.from_job(job)


@router.get(
    "/{job_id}/report",
    response_class=FileResponse,
    responses={200: {"content": {"application/xml": {}}, "description": "KOST-Val XML log"}},
)
async def get_report(job: Job = Depends(get_job)) -> FileResponse:
    if job.status not in FINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Validation is still {job.status.value}")
    if job.report_path is None or not job.report_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No report available for this job")
    return FileResponse(
        job.report_path, media_type="application/xml", filename=f"{job.sip_name}.kost-val.log.xml"
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_validation(job: Job = Depends(get_job)) -> None:
    if job.status == JobStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot delete a running validation")
    manager.delete(job.id)


app.include_router(router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    try:
        argv, cwd = runner.command()
    except KostValError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return {"status": "ok", "kostval_command": argv, "kostval_dir": str(cwd), "queued": manager.queue.qsize()}
