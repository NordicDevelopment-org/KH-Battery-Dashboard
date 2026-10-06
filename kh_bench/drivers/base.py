"""Electronic load driver interface.

All methods are blocking; the engine calls them from a worker thread.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Measurement:
    voltage: float            # V at battery terminals
    current: float            # A drawn (positive = discharge)
    temp_c: Optional[float] = None


class LoadDriver(ABC):
    # Simulated loads may run faster than real time; real hardware is always 1.0.
    time_scale: float = 1.0

    @abstractmethod
    def connect(self) -> str:
        """Open the connection. Returns an identification string."""

    @abstractmethod
    def set_cc(self, amps: float) -> None:
        """Put the load in constant-current mode at the given setpoint."""

    @abstractmethod
    def enable(self, on: bool) -> None:
        """Turn the load input on/off."""

    @abstractmethod
    def measure(self) -> Measurement: ...

    def arm_hardware_stops(self, amps: float, v_cutoff: float, ah_cutoff: float) -> list[str]:
        """Optional. Arm the load's own stop conditions (cutoff voltage, capacity)
        so it stops itself even if the PC or network drops. Returns warnings for
        anything that could not be applied. Loads without the feature do nothing."""
        return []

    def load_ah(self) -> Optional[float]:
        """Optional. The load's own discharged-capacity counter in Ah, for a
        cross-check against the bench's integration. None = not available."""
        return None

    def set_remote_sense(self, on: bool) -> None:
        """Optional. Enable 4-wire sense."""

    def close(self) -> None:
        pass
