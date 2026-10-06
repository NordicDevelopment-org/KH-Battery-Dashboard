"""Bench configuration: company info, channels (hardware), battery profiles.

Loaded from bench_config.json (repo root) if present, otherwise from
bench_config.example.json. Copy the example and edit it for your bench.
"""

import json
import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent


class Company(BaseModel):
    name: str = "Kendall Howard"
    address: str = ""
    phone: str = ""
    cert_title: str = "Certificate of State of Charge"
    cert_subtitle: str = "Lithium Iron Phosphate (LiFePO4) Batteries"
    cert_statement: str = (
        "We certify that each lithium iron phosphate (LiFePO4) battery listed on this "
        "certificate was discharged under controlled conditions and verified to be at a "
        "state of charge (SOC) not exceeding {target_soc}% of its rated capacity at the "
        "time of test. SOC was determined by coulomb counting (amp-hours removed from a "
        "fully charged state) and confirmed by a rested open-circuit voltage check."
    )
    cert_footer: str = ""


class BenchSettings(BaseModel):
    db_path: str = "data/bench.db"
    sample_interval_s: float = Field(1.0, gt=0)  # how often loads are polled
    log_interval_s: float = 5.0      # how often samples are written to the DB
    host: str = "0.0.0.0"
    port: int = 8000


class ChannelConfig(BaseModel):
    id: int
    name: str
    driver: Literal["simulated", "scpi"] = "simulated"
    # scpi
    resource: Optional[str] = None       # e.g. "TCPIP::192.168.1.50::INSTR" or "ASRL/dev/ttyUSB0::INSTR"
    preset: str = "rigol_dl3000"         # command set, see drivers/scpi.py
    load_channel: Optional[int] = None   # for multi-channel mainframes
    max_current_a: float = Field(30.0, gt=0)  # hard limit for this load
    # simulated
    time_scale: float = Field(1.0, gt=0)  # >1 speeds up simulated runs (demo/test only)


class BatteryProfile(BaseModel):
    name: str
    model: str = ""
    cells_series: int = Field(4, ge=1, le=32)  # 4S = 12.8 V nominal
    rated_ah: float = Field(gt=0)
    discharge_a: float = Field(gt=0)     # constant-current discharge setpoint
    target_soc: float = Field(30.0, gt=0, lt=100)  # stop when coulomb-counted SOC hits this
    min_final_soc: float = 20.0          # below this = FAIL (over-discharged)
    cutoff_cell_v: float = 2.80          # safety: abort if loaded V/cell drops below
    min_start_cell_v: float = 3.32       # pre-check: rested V/cell must be >= (battery full?)
    rest_minutes: float = 30.0           # rest after discharge before OCV reading
    ocv_check: bool = True
    ocv_cell_min: float = 3.18           # rested V/cell window expected at ~30%
    ocv_cell_max: float = 3.30
    max_minutes: Optional[float] = None  # timeout; default = 1.5x expected time
    max_temp_c: float = 55.0             # abort if the load reports a higher temp

    @property
    def nominal_v(self) -> float:
        return round(self.cells_series * 3.2, 1)


EVENTS = {
    "run.started": "Discharge started",
    "run.resting": "Discharge done, resting before OCV",
    "run.passed": "Battery PASSED - ready to unload",
    "run.failed": "Battery FAILED",
    "run.aborted": "Run stopped by operator",
    "bench.all_done": "Every running channel has finished",
    "channel.offline": "Load lost / failed to connect",
}


class Webhook(BaseModel):
    name: str = "Node-RED"
    url: str
    enabled: bool = True
    events: list[str] = Field(default_factory=lambda: ["*"])  # "*" = all events


class Notifications(BaseModel):
    station: str = "Bench 1"             # identifies this PC/bench in messages
    webhooks: list[Webhook] = Field(default_factory=list)


class BenchConfig(BaseModel):
    company: Company = Field(default_factory=Company)
    bench: BenchSettings = Field(default_factory=BenchSettings)
    notifications: Notifications = Field(default_factory=Notifications)
    channels: list[ChannelConfig]
    profiles: list[BatteryProfile]

    @model_validator(mode="after")
    def _check(self):
        ids = [c.id for c in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError("Channel ids must be unique")
        names = [p.name for p in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("Profile names must be unique")
        if not self.profiles:
            raise ValueError("At least one battery profile is required")
        for c in self.channels:
            if c.driver == "scpi" and not (c.resource or "").strip():
                raise ValueError(f"{c.name}: SCPI channel needs a VISA resource address")
        return self

    def profile(self, name: str) -> BatteryProfile:
        for p in self.profiles:
            if p.name == name:
                return p
        raise KeyError(f"Unknown battery profile: {name}")


def config_path() -> Path:
    env = os.environ.get("KH_BENCH_CONFIG")
    if env:
        return Path(env)
    p = ROOT / "bench_config.json"
    return p if p.exists() else ROOT / "bench_config.example.json"


def load_config(path: Optional[Path] = None) -> BenchConfig:
    path = path or config_path()
    with open(path, encoding="utf-8") as f:
        return BenchConfig.model_validate(json.load(f))


def writable_config_path() -> Path:
    """Where the Setup page saves. Never overwrites the example file."""
    p = config_path()
    return ROOT / "bench_config.json" if p.name == "bench_config.example.json" else p


def save_config(cfg: BenchConfig, path: Optional[Path] = None) -> Path:
    path = path or writable_config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash mid-save can't leave a half-written config
    return path
