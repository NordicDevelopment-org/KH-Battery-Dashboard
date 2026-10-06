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
from .config import (EVENTS, ROOT, BenchConfig, ChannelConfig, Webhook, load_config,
                     save_config, writable_config_path)
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


def create_app(config: Optional[BenchConfig] = None, db: Optional[Database] = None,
               config_file: Optional[Path] = None) -> FastAPI:
    config = config or load_config()
    config_file = config_file or writable_config_path()
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
        cfg = bench.config
        return {"company": cfg.company.model_dump(),
                "station": cfg.notifications.station,
                "profiles": [p.model_dump() for p in cfg.profiles],
                "channels": [c.model_dump() for c in cfg.channels]}

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
        pdf = cert.batch_certificate(bench.config, job, runs, job["cert_no"])
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
        pdf = cert.unit_certificate(bench.config, run, db.samples(run_id), job)
        return _pdf(pdf, f"{run.get('cert_no') or 'DRAFT'}-{run['serial']}.pdf")

    # ---- setup ----
    @app.get("/api/setup")
    def get_setup():
        from .drivers.scpi import PRESETS
        return {"config": bench.config.model_dump(), "config_file": str(config_file),
                "events": EVENTS, "presets": list(PRESETS),
                "busy": [ch.cfg.name for ch in bench.channels.values() if ch.busy]}

    @app.put("/api/setup")
    async def save_setup(new: BenchConfig):
        restart = [k for k in ("db_path", "host", "port")
                   if getattr(new.bench, k) != getattr(bench.config.bench, k)]
        try:
            await bench.apply_config(new)
        except ValueError as e:
            raise HTTPException(409, str(e))
        path = save_config(new, config_file)
        return {"saved_to": str(path), "restart_needed": restart}

    @app.post("/api/setup/test-channel")
    async def test_channel(ch: ChannelConfig):
        import asyncio

        from .drivers import make_driver
        for c in bench.channels.values():
            if c.busy and (c.cfg.id == ch.id or (ch.resource and c.cfg.resource == ch.resource)):
                raise HTTPException(409, f"{c.cfg.name} is running on this load - test when idle")
        try:
            drv = make_driver(ch)
            idn = await asyncio.to_thread(drv.connect)
            m = await asyncio.to_thread(drv.measure)
        except Exception as e:
            return {"ok": False, "error": str(e) or e.__class__.__name__}
        return {"ok": True, "idn": idn, "voltage": m.voltage, "current": m.current}

    @app.get("/api/setup/resources")
    async def visa_resources():
        import asyncio
        try:
            from .drivers.scpi import list_resources
            return {"ok": True, "resources": await asyncio.to_thread(list_resources)}
        except Exception as e:
            return {"ok": False, "resources": [], "error": str(e) or e.__class__.__name__}

    @app.post("/api/setup/test-webhook")
    async def test_webhook(hook: Webhook):
        return await bench.notifier.send_test(hook)

    @app.get("/api/setup/sample-certificate.pdf")
    def sample_cert():
        """Preview of the cert wording/branding with made-up data."""
        prof = bench.config.profiles[0]
        from .soc import ah_to_remove, soc_after
        ah = ah_to_remove(prof.rated_ah, 100, prof.target_soc, prof.capacity_factor)
        run = {"id": 0, "cert_no": "SAMPLE-0000", "serial": "SAMPLE-SN-0001", "profile": prof.name,
               "model": prof.model, "manufacturer": prof.manufacturer, "channel": "CH1",
               "operator": "Operator Name", "instrument": "(sample)", "rated_ah": prof.rated_ah,
               "cells_series": prof.cells_series, "discharge_a": prof.discharge_a,
               "start_soc": 100, "target_soc": prof.target_soc, "soc_limit": prof.soc_limit,
               "capacity_factor": prof.capacity_factor, "result": "PASS",
               "final_soc": soc_after(prof.rated_ah, 100, ah),
               "worst_soc": prof.target_soc + 0.15, "uncertainty_pts": 0.15,
               "start_v": prof.cells_series * 3.36, "ocv_v": prof.cells_series * 3.25,
               "ah_removed": ah, "wh_removed": ah * prof.cells_series * 3.2,
               "max_temp_c": 30.0, "started_at": "2026-01-01T08:00:00",
               "discharge_end_at": "2026-01-01T10:48:00", "ended_at": "2026-01-01T11:18:00"}
        job = {"customer": "Sample Customer", "po_number": "PO-0000", "lot": "LOT-0000"}
        return _pdf(cert.unit_certificate(bench.config, run, [], job), "sample-certificate.pdf")

    @app.get("/api/setup/notify-log")
    def notify_log():
        return list(bench.notifier.history)

    # ---- UI ----
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/setup")
    def setup_page():
        return FileResponse(STATIC / "setup.html")

    return app
