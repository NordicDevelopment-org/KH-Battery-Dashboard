# LiFePO4 30% SoC Discharge Bench - Design

Kendall Howard LLC, Chisago City MN. As of 2026-10-06. Living copy with diagram:
<https://claude.ai/code/artifact/22c9f66f-0afa-4658-a778-ce731e96e5ed>

## Bottom line

The Siglent SDL1020X-E does the job and is the Phase 1 tool: built-in battery discharge mode
with hardware stop conditions (voltage, capacity, time) and full SCPI control over LAN/USB.

- **Method:** charge to full, rest, then remove a fixed number of amp-hours at constant current
  and count coulombs. Do not judge SoC by voltage on LiFePO4; the curve is too flat.
- **How much to remove:** rated Ah x (worst-case capacity factor - target SoC).
  Default 100 Ah x (1.05 - 0.25) = 80 Ah: a nominal pack lands at 20%, a 5%-over pack at 25%.
  Lot qualification replaces the 1.05 guess with measured data.
- **Throughput, one SDL:** the 200 W cap means ~13 A on a 12.8 V pack, so ~6 h per 100 Ah
  battery. One battery per shift per load.
- **Scale path:** same software, one SDL per channel, one config row each. 20 x SDL1020X-E is
  ~$11.5k at $574 list. The engineering problem at 20 channels is not control, it is
  **3.6 kW of continuous heat** (~12,300 BTU/h).

## Regulatory target

The 30% figure is the air-transport limit: SoC must not exceed 30% of the battery's **rated**
capacity, not its actual or indicated capacity.

| Shipment type | UN / IATA PI | 30% SoC status (2026) |
| --- | --- | --- |
| Li-ion batteries alone | UN3480 / PI 965 | Mandatory |
| Packed with equipment | UN3481 / PI 966 | Mandatory since 1 Jan 2026 (Section II: over 2.7 Wh) |
| Contained in equipment | UN3481 / PI 967 | Recommended, not mandatory |
| Vehicles | UN3556 / PI 952 | Mandatory; indicated capacity of 25% also accepted |

- US domestic air: 49 CFR (PHMSA 2019 IFR) requires 30% SoC for Li-ion cells/batteries alone
  on cargo aircraft.
- Rated capacity is defined per UN Manual of Tests and Criteria 38.3.2.3 (IEC 61960 / 62620
  methodology). Use the datasheet rated Ah, not "typical".
- The certificate documents SoC only. DG classification, packing, marking and the shipper's
  declaration stay with whoever signs the DG paperwork.

## Method

Use a known reference point (full) plus coulomb counting. LiFePO4 sits around 3.2-3.3 V/cell
from roughly 20% to 80% SoC, 1% of SoC is only a few mV, and OCV hysteresis can produce SoC
errors up to ~35%.

| Method | Equipment | Accuracy | Time per 100 Ah | Verdict |
| --- | --- | --- | --- | --- |
| A. Full, then remove fixed Ah | Charger + SDL | Good; limited by actual-vs-rated spread | Charge + ~6 h | **Phase 1 default** |
| B. Discharge to empty, charge back 25-28% | SDL + PSU with Ah logging | Best; referenced to empty | ~6-7 h + ~2 h | Phase 2 upgrade |
| C. Read BMS SoC or resting voltage | App or DMM | Poor; uncalibrated | Minutes | Spot check only |

Method A math (what `kh_bench/soc.py` does):

    Ah_remove  = C_rated x (k - SoC_target)
    SoC_worst  = k - Ah_removed / C_rated + u

- C_rated = datasheet rated Ah. k = worst-case actual / rated (default 1.05). SoC_target = 25%.
  u = measurement uncertainty.
- PASS when SoC_worst is at or under 30%. The bench credits the smaller of the load's Ah
  counter and its own integration, so SoC is never overstated.
- u from the Siglent datasheet readback accuracy +/-(0.05% of reading + 0.05% of full scale).
  At 13 A on the 30 A range that is ~0.17% of the Ah removed, about +/-0.13 SoC points on a
  100 Ah pack.
- **Lot qualification sets k.** Run 3+ batteries per lot full to cutoff; set k to the highest
  measured capacity ratio, rounded up. A lot at 1.08 with k left at 1.05 ships at ~28%: legal,
  but the margin is gone.
- Self-discharge between test and shipment only lowers SoC.

## Your batteries

| Item | LiTime 12V 100Ah Group24 | Green Cubes SWIB-2440 |
| --- | --- | --- |
| Spec source | LiTime product page (mfr) | KH battery spec text 2026-07-13; confirm vs GCT PDF |
| Chemistry / config | LiFePO4, 4S | LiFePO4, 8S2P |
| Nominal / rated | 12.8 V, 100 Ah, 1,280 Wh | 25.6 V, 40 Ah, 1,024 Wh |
| Charge | 14.4 V +/-0.2 V | 30.8 V max; 10.0 A at 15-45 C |
| Discharge rating | 100 A continuous, BMS 100 A | 20.0 A continuous, 40.0 A peak (10 s) |
| BMS low-voltage cut-off | ~10.3-10.4 V observed (third party) | Not in spec; ask GCT |
| Measured actual capacity | 103.3 Ah at 30 A (third party) = 1.033 x rated | Unknown; qualify a lot |
| Operating ambient | Discharge -20 to 60 C | 5 to 40 C |
| Bench current (180 W cap) | ~13.4 A | ~6.6 A |
| Ah removed (k 1.05, target 25%) | 80 Ah | 32 Ah |
| Discharge time | ~6 h | ~5 h |
| Final SoC nominal / worst | 20% / 25.2% | 20% / 25.2% |

- Both packs are over 100 Wh: fully regulated Class 9, 30% rule mandatory.
- SWIB-2440 open items for GCT: BMS cut-off, and whether the output is live with only + and -
  connected or needs a CANBus wake. Confirm on the bench before building fixtures.

## Phase 1: single SDL1020X-E bench

SDL1020X-E facts that drive the design (Siglent datasheet / manuals):

| Item | Value | Design impact |
| --- | --- | --- |
| Input rating | 150 V / 30 A / 200 W | Power is the limit: ~13 A at 12.8 V, ~7 A at 25.6 V |
| Hardware OPP | 210 W | Software caps at 180 W |
| Readback current | +/-(0.05% + 0.05% FS), 1 mA res. | Use 5 A range at 5 A or less |
| Battery mode | CC/CP/CR; stops on V, capacity (mAh), time | Load stops itself if PC/LAN drops |
| Battery measurement max | 999 Ah | Fine for any pack |
| Remote sense | 4-wire, rear terminals | Accurate V under load |
| Interfaces | LAN (VXI-11), USB, RS232 | One PC, many loads |
| Operating temp | 0-40 C | Matters at 20 units |

Bench hardware per channel: SDL1020X-E with static IP; 10 AWG leads (Siglent's 30 A lead is
10 AWG, M6 rings); inline fuse at the battery positive sized above discharge current and below
wire ampacity (VERIFY against fuse and wire datasheets); polarity-keyed quick connector
(Anderson SB50 style); twisted sense pair on the battery posts; LiFePO4 charger with the
battery's CC/CV profile; barcode scanner; fire-resistant surface.

Procedure per battery: charge to full and rest 30 min; connect fuse lead + sense; start run
and scan serial; pre-check rejects anything below the full threshold; load runs CC at
min(battery max, 180 W / V) with hardware stops armed; stops at target Ah; rests 30 min;
certificate prints; operator signs, QA countersigns.

First-article validation: run one pack, confirm the load counter and bench Ah agree within 2%,
clamp meter on the lead mid-run, qualify 3 packs from the lot.

## Software and certificate

See README.md. Faults caught: not full at start, reversed or open leads, current collapse (BMS
cut-off), load protection trip, voltage cut-off before target, Ah cross-check mismatch,
watchdog, duplicate serial. Two Siglent items are not documented and are handled defensively:
the units of the discharged-capacity query (assumed mAh, cross-checked every run) and the max
settable timer (left off; software watchdog instead).

## Phase 2: scaling to 20 batteries

Recommendation: **add SDL1020X-Es in blocks of 4-5 on the LAN until you hit 20.** Zero software
changes, every channel calibratable and independent.

| Option | Cost for 20 ch | Software | Heat | Fit |
| --- | --- | --- | --- | --- |
| **A. 20 x SDL1020X-E** | ~$11.5k loads + rack, switch, airflow | Done | 3.6 kW | **Recommended** |
| B. ITECH IT8700P modular (8 per frame, 16 with extender) | Quote; 2 systems | New driver | 3.6-6 kW | Fewer boxes |
| C. DIY INA228 shunt + contactor + resistor bank per channel | Lowest parts | New build + validation; you own the cal story | Same | Only if cost dominates |
| D. Commercial pack cycler with energy recycling | Highest | Vendor | Mostly to grid | High volume, also Method B |

Throughput (100 Ah / 12.8 V, Method A, ~6.1 h + 0.5 h rest + 0.5 h swap): 1 ch = 1 per shift,
5 ch = 5, 20 ch = 20 per shift, ~60 per 24 h. Charging is the hidden bottleneck: plan charger
channels equal to load channels or receive packs full from the supplier.

Architecture: one PC drives every load over LAN (VXI-11); each load's own voltage and capacity
stops plus a hardwired NC E-stop string into each SDL's ExtSwitch input keep it safe if the PC
or LAN drops.

## Heat, power and safety

| Quantity | 1 channel | 20 channels | Assumption |
| --- | --- | --- | --- |
| Continuous dissipation | 180 W | 3.6 kW | Software power cap |
| Heat load | 614 BTU/h | ~12,300 BTU/h | 1 W = 3.412 BTU/h |
| Energy per batch | ~1.05 kWh | ~21 kWh | 80 Ah at ~13.1 V average |

- SDL is rated 0-40 C ambient, OTP at 85 C internal. Front-to-back airflow, exhaust ducted out.
- Mains draw is small; the heat comes from the batteries, not the outlet.
- Fuse every channel at the battery positive.
- Hardwired E-stop (Phase 2): SDL ExtSwitch, low = on, high = off. VERIFY on the bench how
  ExtSwitch interacts with SCPI input control before relying on it.
- Temperature per battery (Phase 2): thermocouple or NTC, abort above the datasheet limit.
- Unattended runs only after first-article validation with temperature monitoring live.
- Add the bench to LOTO / EHS documentation like any other energized test station.

## Open items

- [ ] UN38.3 test summary doc numbers for both packs; SWIB-2440 BMS cut-off and CANBus wake
- [ ] SoC the batteries arrive at from the supplier
- [ ] Customer's required certificate content; 25% or "at or under 30%"
- [ ] Shipping mode: alone (PI 965), packed with (966) or in equipment (967)
- [ ] Volume per week (5 vs 20 channels, cycler payback)
- [ ] Where the bench lives and how 3.6 kW of heat leaves the room
- [ ] SDL calibration cert and recal interval
- [ ] First-article run on real hardware (capacity query units, hardware stop range)
- [ ] Work instruction and FRM number for the certificate

## Sources

- Siglent SDL1000X Programming Guide E02B: <https://int.siglent.com/u_file/download/23_10_09/SDL1000X_programming_guide_E02B.pdf>
- Siglent SDL1000X/X-E User Manual UM0801X-C01A: <https://cse.sc.edu/~adowney2/resources/data_sheets/SDL1000X%20_%20X-E_Series_User_Manual.pdf>
- Siglent SDL1000X Datasheet 2019.10: <https://caltron.sg/wp-content/uploads/2025/12/Siglent-DS-SDL1000X-Series-E-Load.pdf>
- Siglent US store, SDL1020X-E ($574 list): <https://shop-us.siglent.com/?p=2903>
- ICC Compliance Center, stricter SoC limits 2026: <https://www.thecompliancecenter.com/help-center/articles/stricter-soc-limits-for-lithium-ion-batteries/>
- Lion Technology, 30% SoC 2025-2026: <https://www.lion.com/lion-news/october-2024/lithium-battery-30-state-of-charge-recommendations-for-2025>
- PHMSA IFR 2019: <https://www.federalregister.gov/documents/2019/03/06/2019-03812/hazardous-materials-enhanced-safety-provisions-for-lithium-batteries-transported-by-aircraft-faa>
- Analog Devices AN6909, fuel gauging LiFePO4: <https://www.analog.com/cn/resources/technical-articles/considerations-for-fuel-gauging-lithiumironphosphate-batteries.html>
- LiTime 12V 100Ah Group24 product page: <https://litime.com/products/12v-100ah-group-24-lithium-battery>
- techtest.org LiTime Group24 review (103.3 Ah measured, 10.3-10.4 V cut-off): <https://techtest.org/litime-12v-100ah-group24-im-test-der-kompakte-bluetooth-akku-fuer-den-camper/>
- TI INA228 datasheet (Option C): <https://ti.com/document-viewer/ina228/datasheet>
- ITECH IT8700P series (Option B): <https://www.meilhaus.de/en/it8700.htm>
