from ..config import ChannelConfig
from .base import LoadDriver, Measurement


def make_driver(ch: ChannelConfig) -> LoadDriver:
    if ch.driver == "simulated":
        from .simulated import SimulatedLoad
        return SimulatedLoad(time_scale=ch.time_scale)
    if ch.driver == "scpi":
        from .scpi import ScpiLoad
        if not ch.resource:
            raise ValueError(f"Channel {ch.name}: scpi driver needs 'resource'")
        return ScpiLoad(ch.resource, preset=ch.preset, load_channel=ch.load_channel)
    raise ValueError(f"Unknown driver {ch.driver}")


def close_shared_sessions() -> None:
    """Close pooled VISA sessions (used when the Setup page changes hardware)."""
    import sys
    scpi = sys.modules.get(__name__ + ".scpi")
    if scpi:
        scpi.close_all()


__all__ = ["LoadDriver", "Measurement", "make_driver", "close_shared_sessions"]
