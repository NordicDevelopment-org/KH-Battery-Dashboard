"""Bench engine: runs one discharge cycle per channel, in parallel.

Cycle per battery:
  1. Pre-check   - read rested voltage, refuse if it's too low to be a full battery;
                   cap the current so V * I stays under the channel's power limit
  2. Discharge   - constant current, coulomb-count Ah until the WORST-CASE battery
                   (capacity_factor x rated) reaches target_soc; the load's own
                   voltage/capacity stops are armed first where the driver supports it
  3. Rest        - load off, let voltage settle, record open-circuit voltage
  4. Verdict     - PASS when worst-case SOC (+ measurement uncertainty) <= soc_limit
                   and nominal SOC >= min_final_soc; certificate number on PASS.
                   OCV window and Ah cross-check are advisory warnings only.
Any error or stop request turns the load OFF.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Optional

from .config import BatteryProfile, BenchConfig, ChannelConfig
from .db import Database, now_iso
from .drivers import LoadDriver, close_shared_sessions, make_driver
from .notify import Notifier
from .soc import ah_to_remove, measurement_uncertainty_pts, soc_after, soc_from_ocv

log = logging.getLogger("kh_bench")

LOW_CURRENT_SAMPLES = 5  # consecutive samples under 50% setpoint -> fault
AH_CROSSCHECK_PCT = 2.0  # bench Ah vs load's own counter differ more than this -> warning


def power_capped_current(amps: float, voltage: float, max_power_w: Optional[float]) -> float:
    """Reduce the CC setpoint so voltage * current stays under the load's power cap."""
    if max_power_w and voltage > 0:
        amps = min(amps, max_power_w / voltage)
    return round(amps, 3)


class RunFailed(Exception):
    pass


class RunAborted(Exception):
    pass


class Channel:
    def __init__(self, cfg: ChannelConfig):
        self.cfg = cfg
        self.driver: Optional[LoadDriver] = None
        self.status = "offline"   # offline | idle | discharging | resting | done
        self.message = ""
        self.idn = ""
        self.task: Optional[asyncio.Task] = None
        self.stop_requested = False
        self.live: dict = {}
        self.history: deque = deque(maxlen=240)

    @property
    def busy(self) -> bool:
        return self.status in ("discharging", "resting")

    def snapshot(self) -> dict:
        return {
            "id": self.cfg.id, "name": self.cfg.name, "driver": self.cfg.driver,
            "status": self.status, "message": self.message, "idn": self.idn,
            "live": self.live, "history": list(self.history),
        }


class Bench:
    def __init__(self, config: BenchConfig, db: Database):
        self.config = config
        self.db = db
        self.channels: dict[int, Channel] = {c.id: Channel(c) for c in config.channels}
        self.notifier = Notifier(lambda: self.config.notifications)
        self._shutting_down = False

    # ---------- lifecycle ----------
    async def startup(self) -> None:
        await asyncio.gather(*(self.connect(cid) for cid in self.channels))

    async def connect(self, ch_id: int) -> None:
        ch = self.channels[ch_id]
        if ch.busy:
            return
        try:
            ch.driver = make_driver(ch.cfg)
            ch.idn = await asyncio.to_thread(ch.driver.connect)
            await asyncio.to_thread(ch.driver.enable, False)
            ch.status, ch.message = "idle", ""
        except Exception as e:
            ch.status, ch.message = "offline", f"Connect failed: {e}"
            log.warning("%s: %s", ch.cfg.name, ch.message)
            self.notifier.emit("channel.offline", f"{ch.cfg.name}: {ch.message}",
                               channel=ch.cfg.name)

    async def _close_all(self) -> None:
        for ch in self.channels.values():
            if ch.driver:
                try:
                    await asyncio.to_thread(ch.driver.enable, False)
                    await asyncio.to_thread(ch.driver.close)
                except Exception:
                    pass
        await asyncio.to_thread(close_shared_sessions)

    async def apply_config(self, new: BenchConfig) -> None:
        """Swap in a new config from the Setup page. Hardware changes need an idle bench."""
        old_ch = [c.model_dump() for c in self.config.channels]
        new_ch = [c.model_dump() for c in new.channels]
        if old_ch == new_ch:
            self.config = new  # profiles/company/notifications: applies immediately
            return
        busy = [ch.cfg.name for ch in self.channels.values() if ch.busy]
        if busy:
            raise ValueError(f"Channel changes need an idle bench - still running: {', '.join(busy)}")
        await self._close_all()
        self.config = new
        self.channels = {c.id: Channel(c) for c in new.channels}
        await self.startup()

    async def shutdown(self) -> None:
        self._shutting_down = True
        for ch in self.channels.values():
            ch.stop_requested = True
        tasks = [ch.task for ch in self.channels.values() if ch.task]
        if tasks:
            await asyncio.wait(tasks, timeout=10)
        await self._close_all()
        await self.notifier.drain(timeout=5)

    def state(self) -> list[dict]:
        return [ch.snapshot() for ch in self.channels.values()]

    # ---------- operator actions ----------
    def start(self, ch_id: int, serial: str, profile_name: str, start_soc: float = 100.0,
              job_id: Optional[int] = None, operator: str = "") -> int:
        ch = self.channels[ch_id]
        if ch.status != "idle" and ch.status != "done":
            raise ValueError(f"{ch.cfg.name} is {ch.status}, not ready")
        serial = serial.strip()
        if not serial:
            raise ValueError("Serial number required")
        for other in self.channels.values():
            if other.busy and other.live.get("serial") == serial:
                raise ValueError(f"Serial {serial} is already running on {other.cfg.name}")
        prof = self.config.profile(profile_name)
        if not prof.verified:
            raise ValueError(f"Profile '{prof.name}' is not verified against the manufacturer "
                             "datasheet. Check every value on the Setup page, tick Verified, save.")
        if not prof.target_soc < start_soc <= 100:
            raise ValueError(f"Start SOC must be between {prof.target_soc} and 100")
        # Provisional setpoint from nominal voltage; refined against the measured
        # start voltage in the pre-check.
        amps = power_capped_current(min(prof.discharge_a, ch.cfg.max_current_a),
                                    prof.nominal_v, ch.cfg.max_power_w)
        ah_target = ah_to_remove(prof.rated_ah, start_soc, prof.target_soc, prof.capacity_factor)

        run_id = self.db.create_run(
            job_id=job_id, serial=serial, profile=prof.name, model=prof.model,
            manufacturer=prof.manufacturer,
            channel=ch.cfg.name, operator=operator, instrument=ch.idn,
            rated_ah=prof.rated_ah, cells_series=prof.cells_series, discharge_a=amps,
            start_soc=start_soc, target_soc=prof.target_soc, soc_limit=prof.soc_limit,
            capacity_factor=prof.capacity_factor, ah_target=round(ah_target, 3),
            status="running", started_at=now_iso(),
        )
        ch.stop_requested = False
        ch.history.clear()
        ch.status, ch.message = "discharging", "Pre-check"
        ch.live = {"run_id": run_id, "serial": serial, "profile": prof.name, "phase": "precheck",
                   "start_soc": start_soc, "target_soc": prof.target_soc, "setpoint_a": amps}
        ch.task = asyncio.create_task(self._run(ch, run_id, prof, start_soc, amps))
        self._notify_run("run.started", ch, run_id,
                         f"{ch.cfg.name}: {serial} started ({prof.name}, {amps:g} A)")
        return run_id

    def _notify_run(self, event: str, ch: Channel, run_id: int, text: str) -> None:
        run = self.db.get_run(run_id) or {}
        self.notifier.emit(event, text, channel=ch.cfg.name, serial=run.get("serial"),
                           result=run.get("result"), cert_no=run.get("cert_no"),
                           job_id=run.get("job_id"), run=run)

    def stop(self, ch_id: int) -> None:
        self.channels[ch_id].stop_requested = True

    def clear(self, ch_id: int) -> None:
        ch = self.channels[ch_id]
        if ch.status == "done":
            ch.status, ch.message, ch.live = "idle", "", {}
            ch.history.clear()

    # ---------- the cycle ----------
    async def _io(self, ch: Channel, fn, *args):
        return await asyncio.to_thread(fn, *args)

    async def _run(self, ch: Channel, run_id: int, prof: BatteryProfile,
                   start_soc: float, amps: float) -> None:
        drv = ch.driver
        bench = self.config.bench
        scale = drv.time_scale
        cells = prof.cells_series
        k = prof.capacity_factor
        ah_target = ah_to_remove(prof.rated_ah, start_soc, prof.target_soc, k)
        ah = wh = 0.0
        max_temp: Optional[float] = None
        t_bench = 0.0
        last_log = -1e9
        v = i = 0.0
        warnings: list[str] = []

        def nominal(ah_out: float) -> float:
            return soc_after(prof.rated_ah, start_soc, ah_out)

        def worst(ah_out: float) -> float:
            return soc_after(prof.rated_ah, start_soc, ah_out, k)

        def record(phase: str, m, soc: float) -> None:
            nonlocal last_log, max_temp
            if m.temp_c is not None:
                max_temp = m.temp_c if max_temp is None else max(max_temp, m.temp_c)
            ch.live.update(phase=phase, voltage=round(m.voltage, 3), current=round(m.current, 3),
                           ah=round(ah, 3), wh=round(wh, 2), soc=round(soc, 2),
                           temp_c=m.temp_c, elapsed_s=round(t_bench))
            ch.history.append([round(t_bench), round(m.voltage, 3), round(soc, 2)])
            if t_bench - last_log >= bench.log_interval_s:
                self.db.add_sample(run_id, t_bench, phase, m.voltage, m.current,
                                   round(ah, 4), round(soc, 3), m.temp_c)
                if phase == "discharge":
                    self.db.update_run(run_id, ah_removed=round(ah, 4), wh_removed=round(wh, 2))
                last_log = t_bench

        try:
            if hasattr(drv, "new_battery"):
                drv.new_battery()  # simulator: a fresh battery was "connected"

            # 1. pre-check
            m = await self._io(ch, drv.measure)
            start_v = m.voltage
            self.db.update_run(run_id, start_v=round(start_v, 3))
            record("precheck", m, start_soc)
            if prof.min_start_cell_v and start_soc >= 100 and start_v / cells < prof.min_start_cell_v:
                raise RunFailed(
                    f"Start voltage {start_v:.2f} V ({start_v / cells:.3f} V/cell) is below "
                    f"{prof.min_start_cell_v} V/cell - battery is not fully charged")
            if start_v / cells < prof.cutoff_cell_v:
                raise RunFailed(f"Start voltage {start_v:.2f} V below cutoff - check battery/leads")

            # power cap against the REAL start voltage (a full pack sits above nominal)
            amps = power_capped_current(amps, start_v, ch.cfg.max_power_w)
            ch.live["setpoint_a"] = amps
            self.db.update_run(run_id, discharge_a=amps)
            expected_s = ah_target / amps * 3600
            timeout_s = (prof.max_minutes * 60) if prof.max_minutes else expected_s * 1.5 + 600

            # 2. discharge
            await self._io(ch, drv.set_remote_sense, ch.cfg.remote_sense)
            warnings += await self._io(ch, drv.arm_hardware_stops, amps,
                                       prof.cutoff_cell_v * cells, ah_target)
            await self._io(ch, drv.set_cc, amps)
            await self._io(ch, drv.enable, True)
            ch.message = f"Discharging @ {amps:g} A"
            m = await self._io(ch, drv.measure)  # t=0 point for Ah integration
            last = time.monotonic()
            i_prev = m.current
            low_count = 0
            while True:
                remaining = ah_target - ah
                wait = bench.sample_interval_s
                # don't overshoot: wake up right when the target should be hit
                est_i = i_prev if i_prev > 0.1 else amps
                wait = min(wait, max(0.01, remaining / est_i * 3600 / scale))
                await asyncio.sleep(wait)
                if ch.stop_requested:
                    raise RunAborted("Stopped by operator")

                m = await self._io(ch, drv.measure)
                now = time.monotonic()
                dt = (now - last) * scale
                last = now
                t_bench += dt
                v, i = m.voltage, m.current
                ah += (i_prev + i) / 2 * dt / 3600
                wh += v * (i_prev + i) / 2 * dt / 3600
                i_prev = i
                soc = nominal(ah)
                ch.live["eta_s"] = round(max(0.0, (ah_target - ah) / max(i, 0.01) * 3600))
                ch.live["worst_soc"] = round(worst(ah), 2)
                record("discharge", m, soc)

                if ah >= ah_target:
                    break
                if v / cells <= prof.cutoff_cell_v:
                    raise RunFailed(
                        f"Hit cutoff {v:.2f} V after {ah:.2f} Ah of {ah_target:.2f} Ah - "
                        "battery had less charge than assumed (recharge & retest)")
                if i < 0.05 and t_bench > 5 and getattr(drv, "hardware_stops_armed", False):
                    # the load stopped itself (capacity backstop or protection trip)
                    la = await self._io(ch, drv.load_ah)
                    if la is not None and la >= ah_target * 0.995:
                        ah = max(ah, la)
                        break
                    raise RunFailed(f"Load input dropped at {ah:.2f} Ah of {ah_target:.2f} Ah "
                                    "(protection trip?) - check the load display")
                low_count = low_count + 1 if i < 0.5 * amps else 0
                if low_count >= LOW_CURRENT_SAMPLES:
                    raise RunFailed(f"Load not drawing current ({i:.2f} A of {amps:g} A) - check connections")
                if max_temp is not None and max_temp > prof.max_temp_c:
                    raise RunFailed(f"Over-temperature {max_temp:.1f} C")
                if t_bench > timeout_s:
                    raise RunFailed(f"Timeout after {t_bench / 60:.0f} min")

            await self._io(ch, drv.enable, False)

            # Cross-check against the load's own counter; credit the SMALLER figure
            # so SOC is never overstated.
            ah_load = await self._io(ch, drv.load_ah)
            if ah_load is not None and ah_load > 0:
                diff = abs(ah_load - ah) / max(ah, 1e-9) * 100
                if diff > AH_CROSSCHECK_PCT:
                    warnings.append(f"Load counter {ah_load:.2f} Ah vs bench {ah:.2f} Ah "
                                    f"differ {diff:.1f}% (verify driver units)")
                ah = min(ah, ah_load)

            final_soc = nominal(ah)
            self.db.update_run(run_id, status="resting", discharge_end_at=now_iso(),
                               end_v_loaded=round(v, 3), ah_removed=round(ah, 4),
                               ah_load=None if ah_load is None else round(ah_load, 4),
                               wh_removed=round(wh, 2), final_soc=round(final_soc, 2))
            ch.live["eta_s"] = 0
            self._notify_run("run.resting", ch, run_id,
                             f"{ch.cfg.name}: {ch.live['serial']} discharged to {final_soc:.1f}%, "
                             f"resting {prof.rest_minutes:g} min")

            # 3. rest
            ch.status = "resting"
            rest_s = prof.rest_minutes * 60
            rest_start = t_bench
            m = await self._io(ch, drv.measure)
            record("rest", m, final_soc)
            while t_bench - rest_start < rest_s:
                left = rest_s - (t_bench - rest_start)
                ch.message = f"Resting - OCV in {left / 60:.1f} min"
                ch.live["rest_left_s"] = round(left)
                await asyncio.sleep(min(bench.sample_interval_s, max(0.01, left / scale)))
                if ch.stop_requested:
                    raise RunAborted("Stopped by operator during rest")
                m = await self._io(ch, drv.measure)
                now = time.monotonic()
                t_bench += (now - last) * scale
                last = now
                record("rest", m, final_soc)
            last_log = -1e9
            record("rest", m, final_soc)  # always log the final OCV point
            ch.live["rest_left_s"] = 0

            # 4. verdict
            ocv = m.voltage
            ocv_cell = ocv / cells
            ocv_soc = soc_from_ocv(ocv_cell)
            unc = measurement_uncertainty_pts(ah, prof.rated_ah, amps, ch.cfg.err_pct_reading,
                                              ch.cfg.err_pct_fs, ch.cfg.fs_a)
            worst_soc = worst(ah) + unc
            reasons = []
            if worst_soc > prof.soc_limit:
                reasons.append(f"Worst-case SOC {worst_soc:.1f}% (k={k:g}, +{unc:.2f} pts) "
                               f"exceeds limit {prof.soc_limit:g}%")
            if final_soc < prof.min_final_soc:
                reasons.append(f"Final SOC {final_soc:.1f}% below minimum {prof.min_final_soc}%")
            # OCV is advisory on LiFePO4: the curve is flat and hysteretic in this region.
            if prof.ocv_check and not (prof.ocv_cell_min <= ocv_cell <= prof.ocv_cell_max):
                warnings.append(f"Rested OCV {ocv:.2f} V ({ocv_cell:.3f} V/cell) outside "
                                f"{prof.ocv_cell_min}-{prof.ocv_cell_max} V/cell window")
            result = "FAIL" if reasons else "PASS"
            cert_no = self.next_cert_no() if result == "PASS" else None
            self.db.update_run(run_id, status="complete", result=result,
                               fail_reason="; ".join(reasons) or None, ended_at=now_iso(),
                               ocv_v=round(ocv, 3), ocv_soc_est=round(ocv_soc, 1),
                               worst_soc=round(worst_soc, 2), uncertainty_pts=round(unc, 3),
                               warnings="; ".join(warnings) or None,
                               max_temp_c=max_temp, cert_no=cert_no)
            ch.live.update(result=result, fail_reason="; ".join(reasons), cert_no=cert_no,
                           ocv_v=round(ocv, 3), worst_soc=round(worst_soc, 2),
                           warnings="; ".join(warnings), phase="done")
            ch.message = (f"PASS - {final_soc:.1f}% nominal, {worst_soc:.1f}% worst case, "
                          f"OCV {ocv:.2f} V") if result == "PASS" else "FAIL - " + "; ".join(reasons)
            if warnings:
                ch.message += " | " + "; ".join(warnings)
            self._notify_run("run.passed" if result == "PASS" else "run.failed", ch, run_id,
                             f"{ch.cfg.name}: {ch.live['serial']} {ch.message}")

        except (RunFailed, RunAborted) as e:
            aborted = isinstance(e, RunAborted)
            self.db.update_run(run_id, status="aborted" if aborted else "complete", result="FAIL",
                               fail_reason=str(e), ended_at=now_iso(), ah_removed=round(ah, 4),
                               wh_removed=round(wh, 2), max_temp_c=max_temp,
                               warnings="; ".join(warnings) or None,
                               final_soc=round(nominal(ah), 2), worst_soc=round(worst(ah), 2))
            ch.live.update(result="FAIL", fail_reason=str(e), phase="done")
            ch.message = ("ABORTED - " if aborted else "FAIL - ") + str(e)
            self._notify_run("run.aborted" if aborted else "run.failed", ch, run_id,
                             f"{ch.cfg.name}: {ch.live['serial']} {ch.message}")
        except Exception as e:
            log.exception("%s run %s crashed", ch.cfg.name, run_id)
            self.db.update_run(run_id, status="aborted", result="FAIL",
                               fail_reason=f"Equipment error: {e}", ended_at=now_iso(),
                               ah_removed=round(ah, 4), wh_removed=round(wh, 2))
            ch.live.update(result="FAIL", fail_reason=f"Equipment error: {e}", phase="done")
            ch.message = f"EQUIPMENT ERROR - {e}"
            self._notify_run("run.failed", ch, run_id,
                             f"{ch.cfg.name}: {ch.live['serial']} {ch.message}")
        finally:
            try:
                await self._io(ch, drv.enable, False)
            except Exception as e:
                ch.message += f" | WARNING: could not turn load off: {e}"
            ch.status = "done"
            self._check_all_done()

    def _check_all_done(self) -> None:
        if self._shutting_down or any(c.busy for c in self.channels.values()):
            return
        done = [c for c in self.channels.values() if c.status == "done"]
        passed = sum(c.live.get("result") == "PASS" for c in done)
        self.notifier.emit("bench.all_done",
                           f"all channels finished: {passed} PASS, {len(done) - passed} FAIL - "
                           "swap batteries", passed=passed, failed=len(done) - passed)

    def next_cert_no(self) -> str:
        prefix = f"KH-{datetime.now():%Y%m%d}-"
        return f"{prefix}{self.db.next_cert_seq(prefix):04d}"
