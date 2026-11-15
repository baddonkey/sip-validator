"""In-memory job queue. KOST-Val shares work/log directories, so jobs run strictly one at a time."""

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .config import Settings
from .kostval import KostValRunner, summarize_report

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    valid = "valid"
    invalid = "invalid"
    error = "error"


FINISHED = {JobStatus.valid, JobStatus.invalid, JobStatus.error}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    id: str
    sip_name: str
    language: str
    log_type: str
    status: JobStatus = JobStatus.queued
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    message: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    report_path: Path | None = None


class JobManager:
    def __init__(self, settings: Settings, runner: KostValRunner):
        self.settings = settings
        self.runner = runner
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    def job_dir(self, job_id: str) -> Path:
        return self.settings.data_dir / "jobs" / job_id

    def submit(self, job: Job) -> None:
        self.jobs[job.id] = job
        self.queue.put_nowait(job.id)

    def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    async def run_forever(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            try:
                if job is not None:  # may have been deleted while queued
                    await self._process(job)
            except Exception as exc:
                log.exception("Job %s failed", job_id)
                job.status = JobStatus.error
                job.message = str(exc)
            finally:
                if job is not None:
                    job.finished_at = _now()
                self.queue.task_done()

    async def _process(self, job: Job) -> None:
        job.status = JobStatus.running
        job.started_at = _now()
        job_dir = self.job_dir(job.id)
        sip_dir = job_dir / "sip"

        try:
            result = await self.runner.validate_sip(sip_dir / job.sip_name, job.language, job.log_type)
        finally:
            shutil.rmtree(sip_dir, ignore_errors=True)

        job.exit_code = result.exit_code
        (job_dir / "kostval-output.txt").write_text(result.output, encoding="utf-8")

        if result.report is None:
            job.status = JobStatus.error
            job.message = "KOST-Val produced no report. Output tail: " + result.output[-2000:]
            return

        report = job_dir / "report.xml"
        shutil.move(str(result.report), report)
        job.report_path = report

        status, errors = summarize_report(report)
        job.errors = errors
        if status == "valid":
            job.status = JobStatus.valid
        elif status == "invalid":
            job.status = JobStatus.invalid
        else:
            job.status = JobStatus.error
            job.message = "Could not determine the SIP validation result from the report."
