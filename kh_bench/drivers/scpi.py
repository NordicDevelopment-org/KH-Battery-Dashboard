"""SCPI programmable DC electronic loads over VISA (USB / LAN / RS-232).

Presets cover common bench loads. If yours isn't listed, add a preset with the
commands from its programming manual; nothing else needs to change.

Optional preset keys (only used when present):
  sense        - "{on}" formatted with ON/OFF: 4-wire remote sense
  bat_arm      - list of commands that put the load in its own battery-test mode
                 with hardware stop conditions. Formatted with {amps}, {irange},
                 {v_cutoff}, {mah}. When present, set_cc is skipped (the setpoint
                 is part of bat_arm) and the load stops itself at the cutoff
                 voltage or capacity even if the PC dies.
  bat_set_check - query that must echo the mAh cutoff, used to verify it took
  load_ah_mah  - query returning the load's discharged-capacity counter in mAh
"""

import math
import threading
from typing import Optional

from .base import LoadDriver, Measurement

PRESETS: dict[str, dict] = {
    # Rigol DL3021 / DL3031
    "rigol_dl3000": {
        "init": None,
        "select": None,
        "mode_cc": ":SOUR:FUNC CURR",
        "set_current": ":SOUR:CURR:LEV {amps:.3f}",
        "on": ":SOUR:INP:STAT ON",
        "off": ":SOUR:INP:STAT OFF",
        "meas_v": ":MEAS:VOLT?",
        "meas_i": ":MEAS:CURR?",
    },
    # Siglent SDL1020X / SDL1020X-E / SDL1030X / SDL1030X-E
    # Commands: Siglent SDL1000X Programming Guide E02B, sections 3.2, 3.3.1,
    # 3.3.7, 3.4. Battery-mode cutoff capacity is in mAh (User Manual, Battery
    # Test Function). NOT YET VERIFIED ON HARDWARE: units of :BATT:DISCH:CAP?
    # are assumed to match C_Stop (mAh); the engine cross-checks every run.
    "siglent_sdl1000": {
        "init": None,
        "select": None,
        "mode_cc": ":SOUR:FUNC CURR",
        "set_current": ":SOUR:CURR:LEV:IMM {amps:.3f}",
        "on": ":SOUR:INP:STAT ON",
        "off": ":SOUR:INP:STAT OFF",
        "meas_v": "MEAS:VOLT?",
        "meas_i": "MEAS:CURR?",
        "sense": "SYST:SENS:STAT {on}",
        "bat_arm": [
            ":SOUR:BATT:FUNC",
            ":SOUR:BATT:MODE CURR",
            ":SOUR:BATT:IRANG {irange}",
            ":SOUR:BATT:LEV {amps:.3f}",
            ":SOUR:BATT:VOLT {v_cutoff:.3f}",
            ":SOUR:BATT:VOLT:STAT ON",
            ":SOUR:BATT:CAP {mah:.0f}",
            ":SOUR:BATT:CAP:STAT ON",
            ":SOUR:BATT:TIM:STAT OFF",
        ],
        "bat_set_check": ":SOUR:BATT:CAP?",
        "load_ah_mah": ":SOUR:BATT:DISCH:CAP?",
    },
    # B&K Precision 8600 series
    "bk_8600": {
        "init": "SYST:REM",
        "select": None,
        "mode_cc": "FUNC CURR",
        "set_current": "CURR {amps:.3f}",
        "on": "INP ON",
        "off": "INP OFF",
        "meas_v": "MEAS:VOLT?",
        "meas_i": "MEAS:CURR?",
    },
    # Chroma 63600 mainframe (multi-channel; set load_channel per bench channel)
    "chroma_63600": {
        "init": None,
        "select": "CHAN {ch}",
        "mode_cc": "MODE CCH",
        "set_current": "CURR:STAT:L1 {amps:.3f}",
        "on": "LOAD ON",
        "off": "LOAD OFF",
        "meas_v": "MEAS:VOLT?",
        "meas_i": "MEAS:CURR?",
    },
}

# One VISA session per physical instrument, shared by all bench channels on it.
_sessions: dict[str, tuple[object, threading.Lock]] = {}
_sessions_lock = threading.Lock()


def resource_manager():
    """System VISA (NI-VISA / Keysight / vendor) if installed, else pure-Python pyvisa-py."""
    import pyvisa  # imported lazily so simulated benches need no VISA install

    try:
        return pyvisa.ResourceManager()
    except (OSError, ValueError):
        return pyvisa.ResourceManager("@py")


def list_resources() -> list[str]:
    return list(resource_manager().list_resources())


def close_all() -> None:
    with _sessions_lock:
        for inst, _ in _sessions.values():
            try:
                inst.close()
            except Exception:
                pass
        _sessions.clear()


def _open(resource: str):
    with _sessions_lock:
        if resource not in _sessions:
            rm = resource_manager()
            inst = rm.open_resource(resource)
            inst.timeout = 3000
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            _sessions[resource] = (inst, threading.Lock())
        return _sessions[resource]


class ScpiLoad(LoadDriver):
    def __init__(self, resource: str, preset: str = "rigol_dl3000", load_channel: int | None = None):
        if preset not in PRESETS:
            raise ValueError(f"Unknown SCPI preset '{preset}'. Options: {', '.join(PRESETS)}")
        self.resource = resource
        self.preset = preset
        self.cmd = PRESETS[preset]
        self.load_channel = load_channel
        self.inst = None
        self.lock = None
        self.hardware_stops_armed = False

    def _select(self) -> None:
        if self.cmd["select"] and self.load_channel is not None:
            self.inst.write(self.cmd["select"].format(ch=self.load_channel))

    def _write(self, key: str, **kw) -> None:
        with self.lock:
            self._select()
            self.inst.write(self.cmd[key].format(**kw))

    def _query_float(self, key: str) -> float:
        with self.lock:
            self._select()
            return float(self.inst.query(self.cmd[key]).strip())

    def _raw(self, cmd: str) -> None:
        with self.lock:
            self._select()
            self.inst.write(cmd)

    def _raw_query(self, cmd: str) -> str:
        with self.lock:
            self._select()
            return self.inst.query(cmd).strip()

    def connect(self) -> str:
        self.inst, self.lock = _open(self.resource)
        with self.lock:
            if self.cmd["init"]:
                self.inst.write(self.cmd["init"])
            idn = self.inst.query("*IDN?").strip()
        self.enable(False)
        return idn

    def set_remote_sense(self, on: bool) -> None:
        if self.cmd.get("sense"):
            self._raw(self.cmd["sense"].format(on="ON" if on else "OFF"))

    def arm_hardware_stops(self, amps: float, v_cutoff: float, ah_cutoff: float) -> list[str]:
        self.hardware_stops_armed = False
        seq = self.cmd.get("bat_arm")
        if not seq:
            return []
        self.enable(False)
        mah = ah_cutoff * 1000.0
        # Siglent range rule (prog. guide 2.5): >5 A selects the 30 A range
        irange = 30 if amps > 5 else 5
        try:
            for c in seq:
                self._raw(c.format(amps=amps, irange=irange, v_cutoff=v_cutoff, mah=mah))
            chk = self.cmd.get("bat_set_check")
            if chk:
                got = float(self._raw_query(chk))
                if not math.isclose(got, mah, rel_tol=1e-3, abs_tol=1.0):
                    raise ValueError(f"capacity stop readback {got} != {mah}")
        except Exception as e:
            # Fall back to plain CC under software control; the certificate says so.
            try:
                self._write("mode_cc")
            except Exception:
                pass
            return [f"Hardware stop not armed ({e}); software stop only"]
        self.hardware_stops_armed = True
        return []

    def load_ah(self) -> Optional[float]:
        q = self.cmd.get("load_ah_mah")
        if not q or not self.hardware_stops_armed:
            return None
        try:
            return float(self._raw_query(q)) / 1000.0
        except Exception:
            return None

    def set_cc(self, amps: float) -> None:
        if self.hardware_stops_armed:
            return  # setpoint already applied by arm_hardware_stops in battery mode
        self._write("mode_cc")
        self._write("set_current", amps=amps)

    def enable(self, on: bool) -> None:
        self._write("on" if on else "off")

    def measure(self) -> Measurement:
        return Measurement(voltage=self._query_float("meas_v"), current=self._query_float("meas_i"))

    def close(self) -> None:
        try:
            self.enable(False)
        except Exception:
            pass
