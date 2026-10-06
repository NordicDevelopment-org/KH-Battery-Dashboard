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

    def close(self) -> None:
        pass
