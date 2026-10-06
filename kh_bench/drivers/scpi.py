"""SCPI programmable DC electronic loads over VISA (USB / LAN / RS-232).

Presets cover common bench loads. If yours isn't listed, add a preset with the
commands from its programming manual; nothing else needs to change.
"""

import threading

from .base import LoadDriver, Measurement

PRESETS: dict[str, dict[str, str | None]] = {
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
    # Siglent SDL1020X / SDL1030X
    "siglent_sdl1000": {
        "init": None,
        "select": None,
        "mode_cc": ":SOUR:FUNC CURR",
        "set_current": ":SOUR:CURR:LEV:IMM {amps:.3f}",
        "on": ":SOUR:INP:STAT ON",
        "off": ":SOUR:INP:STAT OFF",
        "meas_v": "MEAS:VOLT?",
        "meas_i": "MEAS:CURR?",
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
        self.cmd = PRESETS[preset]
        self.load_channel = load_channel
        self.inst = None
        self.lock = None

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

    def connect(self) -> str:
        self.inst, self.lock = _open(self.resource)
        with self.lock:
            if self.cmd["init"]:
                self.inst.write(self.cmd["init"])
            idn = self.inst.query("*IDN?").strip()
        self.enable(False)
        return idn

    def set_cc(self, amps: float) -> None:
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
