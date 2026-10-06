# KH-Battery-Dashboard

Kendall Howard bench tool that discharges LiFePO4 batteries to a certified state of charge
(SoC) at or under 30% of RATED capacity for air shipment (IATA DGR PI 965/966, 49 CFR 173.185),
then prints a Certificate of State of Charge per serial and per customer job.

Read `docs/DESIGN.md` before changing the method, the verdict logic or the certificate.
It holds the regulatory basis, the hardware limits and the math every number comes from.

## Stack and layout

- Python 3.11+, FastAPI + uvicorn, pydantic v2 config, SQLite (WAL), reportlab PDFs,
  pyvisa / pyvisa-py for loads. Vanilla JS front end, no build step.
- `kh_bench/engine.py`   one asyncio task per channel: pre-check, discharge, rest, verdict
- `kh_bench/soc.py`      the only place SoC math lives (nominal, worst case, uncertainty)
- `kh_bench/config.py`   pydantic models; `BatteryProfile` is the compliance contract
- `kh_bench/drivers/`    `base.py` interface, `scpi.py` presets, `simulated.py` for tests
- `kh_bench/cert.py`     unit + batch certificates
- `kh_bench/db.py`       schema + `_migrate()` for columns added after first release
- `kh_bench/api.py`, `static/`  REST + dashboard + setup page
- `tests/test_bench.py`  full-cycle tests on the simulator. `python -m pytest -q` must pass.

## Rules that are not negotiable

1. **Never invent a battery spec.** Every `BatteryProfile` value must trace to the manufacturer
   datasheet; record where in `source`. Retailer pages rank below manufacturer docs. Keep the
   exact manufacturer model string. If a value is unknown, leave the profile `verified: false`
   and say so; the engine refuses to start unverified profiles on purpose. Do not weaken that.
2. **SoC is coulomb counting from a full battery, never voltage.** LiFePO4 OCV is flat and
   hysteretic from ~20-80%. `ocv_check` is advisory; it must never decide PASS/FAIL.
3. **Two SoC figures, both as % of rated.** `nominal` assumes actual = rated. `worst` assumes
   actual = `capacity_factor` x rated and adds instrument uncertainty
   (`measurement_uncertainty_pts`). PASS requires `worst <= soc_limit`. Credit the SMALLER of
   bench Ah and the load's own counter. SoC must never be overstated.
4. **Hardware limits are facts, not tunables.** Siglent SDL1020X-E: 150 V / 30 A / 200 W, OPP at
   210 W, readback current +/-(0.05% rdg + 0.05% FS). `max_power_w` caps the CC setpoint against
   the MEASURED start voltage. Do not raise defaults above the datasheet.
5. **Every stop path turns the load OFF**: finish, fail, stop, exception, shutdown. Keep the
   `finally` in `Bench._run`. Where the driver supports it, arm the load's own voltage and
   capacity stops before enabling the input so the load stops itself if the PC or LAN dies.
6. **Electrical calculations show their assumptions** in code comments and on the certificate
   (capacity factor, uncertainty, start SoC assumed, limit). Separate nominal / continuous /
   peak ratings when quoting battery specs.
7. **Schema changes go through `Database._migrate()`**, additive only. Raw samples are never
   edited. A run row is never deleted.
8. **No em dashes** anywhere in code, docs, UI strings or certificates. Use a plain hyphen.

## SCPI presets

Commands in `drivers/scpi.py` are copied from the manufacturer programming guide named in the
preset comment. When adding a preset, cite the document and section, and mark anything not yet
exercised on real hardware `NOT YET VERIFIED ON HARDWARE`. The Siglent `bat_arm` sequence and
`:SOUR:BATT:DISCH:CAP?` units fall in that category until a first-article run confirms them.

## Conventions

- Keep changes small and tested; add a simulator test for every new verdict or fault path.
- `bench_config.example.json` is the reference config and is never overwritten by the app.
  Real benches use `bench_config.json` (gitignored).
- Certificates are the customer-facing output. Any wording change needs the compliance /
  shipping contact's review before release; say so in the PR.
- Dates ISO 8601, units SI with the unit in the column header or label.

## Open items (as of 2026-10-06)

- First-article run on real SDL1020X-E hardware: verify `:BATT:DISCH:CAP?` units, hardware
  capacity-stop range, and that `:SOUR:INP:STAT ON` starts the battery test.
- Green Cubes SWIB-2440: BMS low-voltage cut-off and whether output needs a CANBus wake.
- UN38.3 test summary doc numbers for the LiTime and Green Cubes packs (print on cert).
- Lot qualification mode (discharge to cutoff, report measured capacity / rated) to set
  `capacity_factor` from data instead of the 1.05 default.
- Hardwired E-stop string to each SDL's ExtSwitch input for the 20-channel build.
