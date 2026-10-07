"""Simulated LiFePO4 battery + electronic load, for demos and tests."""

import random
import time

from ..soc import ocv_from_soc
from .base import LoadDriver, Measurement


class SimulatedLoad(LoadDriver):
    def __init__(self, time_scale: float = 1.0, cells: int = 4, capacity_ah: float = 100.0,
                 start_soc: float | None = None, r_pack: float = 0.02):
        self.time_scale = time_scale
        self.cells = cells
        self.r_pack = r_pack
        self.capacity_ah = capacity_ah
        self._fixed_soc = start_soc
        self.new_battery()
        self.setpoint = 0.0
        self.on = False
        self._t = time.monotonic()

    def new_battery(self) -> None:
        """Simulate a fresh, fully charged battery being connected."""
        self.soc = self._fixed_soc if self._fixed_soc is not None else random.uniform(99.0, 100.0)
        # real LFP packs are usually delivered a few % over nameplate
        self.cap = self.capacity_ah * random.uniform(0.99, 1.04)
        self.temp = random.uniform(22, 25)

    def connect(self) -> str:
        return "SIMULATED,LFP-LOAD,0,1.0"

    def set_cc(self, amps: float) -> None:
        self._advance()
        self.setpoint = amps

    def enable(self, on: bool) -> None:
        self._advance()
        self.on = on

    def _advance(self) -> None:
        now = time.monotonic()
        dt_h = (now - self._t) * self.time_scale / 3600.0
        self._t = now
        if self.on:
            self.soc -= self.setpoint * dt_h / self.cap * 100.0
            self.temp = min(self.temp + 0.02 * self.setpoint * dt_h * 60, 40)
        else:
            self.temp = max(self.temp - 0.5 * dt_h * 60, 23)
        self.soc = max(self.soc, 0.0)

    def measure(self) -> Measurement:
        self._advance()
        i = self.setpoint * random.uniform(0.998, 1.002) if self.on else 0.0
        v = self.cells * ocv_from_soc(self.soc) - i * self.r_pack + random.uniform(-0.003, 0.003)
        return Measurement(voltage=round(v, 4), current=round(i, 4), temp_c=round(self.temp, 1))
