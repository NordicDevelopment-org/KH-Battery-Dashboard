"""HTTP API + static dashboard."""

import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cert
from .config import BenchConfig, ROOT, load_config
from .db import Database
from .engine import Bench

STATIC = Path(__file__).parent / "static"


class StartReq(BaseModel):
    serial: str
    profile: str
    start_soc: float = 100.0
    job_id: Optional[int] = None
    operator: str = ""
    force: bool = False  # start even if serial already has a PASS on record


class JobReq(BaseModel):
    customer: str
    po_number: str = ""
    lot: str = ""
    notes: str = ""


def _pdf(data: bytes, name: str) -> Response:
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


def _csv(rows: list[dict], name: str) -> Response:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def create_app(config: Optional[BenchConfig] = None, db: Optional[Database] = None) -> FastAPI:
    config = config or load_config()
    if db is None:
        p = Path(config.bench.db_path)
        db = Database(str(p if p.is_absolute() else ROOT / p))
    bench = Bench(config, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await bench.startup()
        yield
        await bench.shutdown()

    app = FastAPI(title="KH Battery Dashboard", lifespan=lifespan)
    app.state.bench = bench

    def ch_or_404(ch_id: int):
        if ch_id not in bench.channels:
            raise HTTPException(404, "No such channel")

    def run_or_404(run_id: int) -> dict:
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "No such run")
        return run

    def job_or_404(job_id: int) -> dict:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "No such job")
        return job

    # ---- bench ----
    @app.get("/api/config")
    def get_config():
        return {"company": config.company.model_dump(),
                "profiles": [p.model_dump() for p in config.profiles],
                "channels": [c.model_dump() for c in config.channels]}

    @app.get("/api/state")
    def get_state():
        return bench.state()

    @app.post("/api/channels/{ch_id}/start")
    async def start(ch_id: int, req: StartReq):
        ch_or_404(ch_id)
        if req.job_id is not None:
            job_or_404(req.job_id)
        if not req.force:
            prior = db.passed_runs_for_serial(req.serial.strip())
            if prior:
                raise HTTPException(409, f"Serial {req.serial} already passed on "
                                         f"{prior[-1]['ended_at']} (cert {prior[-1]['cert_no']})")
        try:
            run_id = bench.start(ch_id, req.serial, req.profile, req.start_soc,
                                 req.job_id, req.operator)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e).strip("'"))
        return {"run_id": run_id}

    @app.post("/api/channels/{ch_id}/stop")
    def stop(ch_id: int):
        ch_or_404(ch_id)
        bench.stop(ch_id)
        return {"ok": True}

    @app.post("/api/channels/{ch_id}/clear")
    def clear(ch_id: int):
        ch_or_404(ch_id)
        bench.clear(ch_id)
        return {"ok": True}

    @app.post("/api/channels/{ch_id}/reconnect")
    async def reconnect(ch_id: int):
        ch_or_404(ch_id)
        await bench.connect(ch_id)
        return bench.channels[ch_id].snapshot()

    # ---- jobs ----
    @app.get("/api/jobs")
    def list_jobs():
        return db.list_jobs()

    @app.post("/api/jobs")
    def create_job(req: JobReq):
        if not req.customer.strip():
            raise HTTPException(400, "Customer required")
        return db.get_job(db.create_job(req.customer.strip(), req.po_number.strip(),
                                        req.lot.strip(), req.notes.strip()))

    @app.get("/api/jobs/{job_id}/runs")
    def job_runs(job_id: int):
        job_or_404(job_id)
        return db.list_runs(job_id=job_id)

    @app.get("/api/jobs/{job_id}/certificate.pdf")
    def job_cert(job_id: int):
        job = job_or_404(job_id)
        runs = db.list_runs(job_id=job_id, limit=100000)
        if not job.get("cert_no"):
            job["cert_no"] = bench.next_cert_no()
            db.set_job_cert(job_id, job["cert_no"])
        pdf = cert.batch_certificate(config, job, runs, job["cert_no"])
        return _pdf(pdf, f"{job['cert_no']}.pdf")

    @app.get("/api/jobs/{job_id}/report.csv")
    def job_csv(job_id: int):
        job = job_or_404(job_id)
        return _csv(db.list_runs(job_id=job_id, limit=100000), f"job{job_id}-{job['lot'] or 'runs'}.csv")

    # ---- runs ----
    @app.get("/api/runs")
    def list_runs(serial: Optional[str] = None, limit: int = 200):
        return db.list_runs(serial=serial, limit=limit)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int):
        return run_or_404(run_id)

    @app.get("/api/runs/{run_id}/samples")
    def run_samples(run_id: int):
        run_or_404(run_id)
        return db.samples(run_id)

    @app.get("/api/runs/{run_id}/samples.csv")
    def run_samples_csv(run_id: int):
        run = run_or_404(run_id)
        return _csv(db.samples(run_id), f"{run['serial']}-run{run_id}.csv")

    @app.get("/api/runs/{run_id}/certificate.pdf")
    def run_cert(run_id: int):
        run = run_or_404(run_id)
        job = db.get_job(run["job_id"]) if run.get("job_id") else None
        pdf = cert.unit_certificate(config, run, db.samples(run_id), job)
        return _pdf(pdf, f"{run.get('cert_no') or 'DRAFT'}-{run['serial']}.pdf")

    # ---- UI ----
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
