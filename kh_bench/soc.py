"""LiFePO4 state-of-charge helpers.

SOC on this bench is determined by coulomb counting (amp-hours removed from a
known starting SOC). LiFePO4 has a very flat voltage curve between ~20-80% SOC,
so open-circuit voltage (OCV) is only used as a sanity check, never as the
primary SOC measurement.
"""

from bisect import bisect_left

# Approximate rested OCV per cell (V) vs SOC (%) at ~25 C for LiFePO4.
# Typical values; real cells vary by manufacturer. Treat as a sanity check only.
LFP_OCV_TABLE: list[tuple[float, float]] = [
    (0, 2.80),
    (5, 3.00),
    (10, 3.13),
    (20, 3.21),
    (30, 3.25),
    (40, 3.27),
    (50, 3.285),
    (60, 3.30),
    (70, 3.315),
    (80, 3.33),
    (90, 3.34),
    (99, 3.40),
    (100, 3.45),
]


def ocv_from_soc(soc: float) -> float:
    """Per-cell rested voltage for a given SOC (linear interpolation)."""
    soc = max(0.0, min(100.0, soc))
    socs = [s for s, _ in LFP_OCV_TABLE]
    i = bisect_left(socs, soc)
    if i == 0:
        return LFP_OCV_TABLE[0][1]
    s0, v0 = LFP_OCV_TABLE[i - 1]
    s1, v1 = LFP_OCV_TABLE[i]
    return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)


def soc_from_ocv(cell_v: float) -> float:
    """Rough SOC estimate from a rested per-cell voltage (inverse of the table)."""
    if cell_v <= LFP_OCV_TABLE[0][1]:
        return 0.0
    if cell_v >= LFP_OCV_TABLE[-1][1]:
        return 100.0
    for (s0, v0), (s1, v1) in zip(LFP_OCV_TABLE, LFP_OCV_TABLE[1:]):
        if v0 <= cell_v <= v1:
            return s0 + (s1 - s0) * (cell_v - v0) / (v1 - v0)
    return 0.0


def ah_to_remove(rated_ah: float, start_soc: float, target_soc: float) -> float:
    """Amp-hours to pull out to go from start_soc to target_soc."""
    return max(0.0, (start_soc - target_soc) / 100.0 * rated_ah)


def soc_after(rated_ah: float, start_soc: float, ah_removed: float) -> float:
    return start_soc - ah_removed / rated_ah * 100.0
