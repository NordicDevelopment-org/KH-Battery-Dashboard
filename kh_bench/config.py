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
        "certificate was discharged under controlled conditions and, based on the "
        "measurements and stated assumptions, its state of charge (SOC) does not exceed "
        "{soc_limit}% of its rated capacity. SOC was determined by coulomb counting "
        "(amp-hours removed from a fully charged state). The worst-case figure assumes the "
        "battery held {capacity_factor}x its rated capacity and includes instrument "
        "measurement uncertainty. Rested open-circuit voltage is recorded for reference only."
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
    # Power cap. Discharge current is reduced so V_start * I <= max_power_w.
    # Siglent SDL1020X-E is 200 W rated (OPP trips at 210 W): use 180.
    max_power_w: Optional[float] = Field(None, gt=0)
    remote_sense: bool = False           # 4-wire sense leads landed on the battery posts
    # Current readback accuracy of this load, used for the certificate's uncertainty
    # figure: +/-(err_pct_reading % of reading + err_pct_fs % of range fs_a).
    # Defaults = Siglent SDL1000X datasheet readback current spec, 30 A range.
    err_pct_reading: float = Field(0.05, ge=0)
    err_pct_fs: float = Field(0.05, ge=0)
    fs_a: float = Field(30.0, gt=0)
    # simulated
    time_scale: float = Field(1.0, gt=0)  # >1 speeds up simulated runs (demo/test only)


class BatteryProfile(BaseModel):
    name: str
    model: str = ""                      # exact manufacturer model number (printed on cert)
    manufacturer: str = ""
    cells_series: int = Field(4, ge=1, le=32)  # 4S = 12.8 V nominal
    rated_ah: float = Field(gt=0)        # RATED capacity from the datasheet, not "typical"
    discharge_a: float = Field(gt=0)     # CC setpoint; capped by channel max A and max W
    # --- compliance -------------------------------------------------------
    # The regulatory ceiling (IATA DGR PI 965/966, 49 CFR 173.185): 30% of rated.
    soc_limit: float = Field(30.0, gt=0, lt=100)
    # Where we aim the WORST-CASE battery. Below soc_limit so measurement
    # uncertainty and capacity spread still pass.
    target_soc: float = Field(25.0, gt=0, lt=100)
    # Worst-case actual capacity as a multiple of rated. LFP packs are routinely
    # shipped 2-5% over nameplate. Replace with your own lot-qualification data.
    capacity_factor: float = Field(1.05, ge=1.0, le=1.5)
    min_final_soc: float = 15.0          # nominal SOC below this = FAIL (over-discharged)
    # Set true only after every value above has been checked against the
    # manufacturer datasheet. Unverified profiles cannot be started.
    verified: bool = False
    source: str = ""                     # where the numbers came from (doc title, URL, date)
    # --- safety -------------------------------------------------------------
    cutoff_cell_v: float = 2.80          # safety: abort if loaded V/cell drops below
    min_start_cell_v: float = 3.32       # pre-check: rested V/cell must be >= (battery full?)
    max_minutes: Optional[float] = None  # timeout; default = 1.5x expected time
    max_temp_c: float = 55.0             # abort if the load reports a higher temp
    # --- OCV sanity check (advisory only, never decides PASS/FAIL) -----------
    rest_minutes: float = 30.0           # rest after discharge before OCV reading
    ocv_check: bool = True               # outside the window -> warning on the record
    ocv_cell_min: float = 3.15           # rested V/cell window expected around 20-30%
    ocv_cell_max: float = 3.30

    @model_validator(mode="after")
    def _check(self):
        if self.target_soc >= self.soc_limit:
            raise ValueError(f"{self.name}: target_soc ({self.target_soc}) must be below "
                             f"soc_limit ({self.soc_limit})")
        if self.min_final_soc >= self.target_soc:
            raise ValueError(f"{self.name}: min_final_soc must be below target_soc")
        return self

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
