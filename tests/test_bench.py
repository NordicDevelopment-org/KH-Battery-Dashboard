import asyncio

import pytest
from fastapi.testclient import TestClient

from kh_bench.api import create_app
from kh_bench.config import BatteryProfile, BenchConfig, BenchSettings, ChannelConfig
from kh_bench.db import Database
from kh_bench.drivers.simulated import SimulatedLoad
from kh_bench.engine import Bench
from kh_bench.soc import ah_to_remove, ocv_from_soc, soc_from_ocv

FAST = 5000  # simulated seconds per real second


def make_config(n_channels=2, **profile_overrides) -> BenchConfig:
    prof = dict(name="T100", model="Test 100Ah", rated_ah=100, discharge_a=25, rest_minutes=30)
    prof.update(profile_overrides)
    return BenchConfig(
        bench=BenchSettings(sample_interval_s=0.05, log_interval_s=60),
        channels=[ChannelConfig(id=i, name=f"CH{i}", time_scale=FAST) for i in range(1, n_channels + 1)],
        profiles=[BatteryProfile(**prof)],
    )


async def run_one(bench: Bench, ch_id=1, serial="SN1", start_soc=100.0, timeout=20):
    run_id = bench.start(ch_id, serial, "T100", start_soc)
    await asyncio.wait_for(bench.channels[ch_id].task, timeout)
    return bench.db.get_run(run_id)


def test_soc_math():
    assert ah_to_remove(100, 100, 30) == pytest.approx(70)
    assert ah_to_remove(50, 90, 30) == pytest.approx(30)
    for soc in (10, 30, 55, 90):
        assert soc_from_ocv(ocv_from_soc(soc)) == pytest.approx(soc, abs=0.5)


def test_full_cycle_pass():
    async def go():
        bench = Bench(make_config(), Database(":memory:"))
        await bench.startup()
        run = await run_one(bench)
        await bench.shutdown()
        return bench, run

    bench, run = asyncio.run(go())
    assert run["result"] == "PASS", run["fail_reason"]
    assert run["status"] == "complete"
    assert 29.0 <= run["final_soc"] <= 30.5
    assert run["ah_removed"] == pytest.approx(70, abs=1.0)
    assert run["cert_no"].startswith("KH-")
    assert 3.18 * 4 <= run["ocv_v"] <= 3.30 * 4
    samples = bench.db.samples(run["id"])
    assert {s["phase"] for s in samples} >= {"discharge", "rest"}
    assert bench.channels[1].driver.on is False  # load left OFF


def test_parallel_channels():
    async def go():
        bench = Bench(make_config(n_channels=4), Database(":memory:"))
        await bench.startup()
        ids = [bench.start(c, f"SN{c}", "T100") for c in range(1, 5)]
        await asyncio.wait_for(asyncio.gather(*(bench.channels[c].task for c in range(1, 5))), 30)
        await bench.shutdown()
        return [bench.db.get_run(i) for i in ids]

    runs = asyncio.run(go())
    assert all(r["result"] == "PASS" for r in runs)
    assert len({r["cert_no"] for r in runs}) == 4  # unique cert numbers


def test_battery_not_full_fails_precheck():
    async def go():
        bench = Bench(make_config(), Database(":memory:"))
        await bench.startup()
        bench.channels[1].driver._fixed_soc = 50  # half-charged battery connected
        return await run_one(bench)

    run = asyncio.run(go())
    assert run["result"] == "FAIL"
    assert "not fully charged" in run["fail_reason"]
    assert run["ah_removed"] == 0


def test_cutoff_trips_on_low_capacity_battery():
    async def go():
        bench = Bench(make_config(min_start_cell_v=0), Database(":memory:"))
        await bench.startup()
        drv: SimulatedLoad = bench.channels[1].driver
        drv.capacity_ah = 40  # battery much smaller than rated
        return await run_one(bench)

    run = asyncio.run(go())
    assert run["result"] == "FAIL"
    assert "cutoff" in run["fail_reason"].lower()


def test_operator_stop():
    async def go():
        bench = Bench(make_config(), Database(":memory:"))
        await bench.startup()
        run_id = bench.start(1, "SN-STOP", "T100")
        await asyncio.sleep(0.3)
        bench.stop(1)
        await asyncio.wait_for(bench.channels[1].task, 5)
        return bench, bench.db.get_run(run_id)

    bench, run = asyncio.run(go())
    assert run["status"] == "aborted" and run["result"] == "FAIL"
    assert bench.channels[1].driver.on is False


def test_api_end_to_end_with_certs(tmp_path):
    import time

    app = create_app(make_config(), Database(str(tmp_path / "t.db")))
    with TestClient(app) as c:
        job = c.post("/api/jobs", json={"customer": "ACME", "po_number": "PO-1", "lot": "L1"}).json()
        for ch, sn in ((1, "SN-A"), (2, "SN-B")):
            r = c.post(f"/api/channels/{ch}/start",
                       json={"serial": sn, "profile": "T100", "job_id": job["id"], "operator": "Tester"})
            assert r.status_code == 200, r.text
        deadline = time.time() + 30
        while time.time() < deadline:
            if all(ch["status"] == "done" for ch in c.get("/api/state").json()):
                break
            time.sleep(0.2)
        runs = c.get(f"/api/jobs/{job['id']}/runs").json()
        assert [r["result"] for r in runs] == ["PASS", "PASS"]

        # duplicate serial is blocked unless forced
        c.post("/api/channels/1/clear")
        dup = c.post("/api/channels/1/start", json={"serial": "SN-A", "profile": "T100"})
        assert dup.status_code == 409

        pdf = c.get(f"/api/runs/{runs[0]['id']}/certificate.pdf")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        batch = c.get(f"/api/jobs/{job['id']}/certificate.pdf")
        assert batch.status_code == 200 and batch.content[:4] == b"%PDF"
        (tmp_path / "unit.pdf").write_bytes(pdf.content)
        (tmp_path / "batch.pdf").write_bytes(batch.content)
        jobs = c.get("/api/jobs").json()
        assert jobs[0]["cert_no"] and jobs[0]["pass_count"] == 2

        csv = c.get(f"/api/runs/{runs[0]['id']}/samples.csv").text
        assert csv.startswith("run_id,t_s,phase")
        assert c.get("/").status_code == 200
