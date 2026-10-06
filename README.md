# KH-Battery-Dashboard
Track SOC, serial number and date/time, and print a certificate, all from one tool.

**KH Battery Bench** discharges LiFePO4 batteries to **30% SOC** on many channels in parallel,
logs every run, prints certificates per unit or per customer batch, and pushes events to
Node-RED (or any webhook) so people get alerted.

![dashboard](docs/samples/dashboard.png)

---

## How it works

| Step | What happens | Pass/fail gate |
|---|---|---|
| 1. Pre-check | Reads the rested battery voltage with the load OFF | V/cell >= `min_start_cell_v` (3.32). Below that the battery isn't full, so the run is rejected |
| 2. Discharge | Constant-current load ON; Ah are integrated from *measured* current every second | Stops when `Ah removed = (start SOC - 30%) x rated Ah`. Safety stops: cutoff voltage, low current, over-temp, timeout |
| 3. Rest | Load OFF for `rest_minutes` (30); voltage is logged | - |
| 4. Verdict | Records rested OCV | Final SOC between 20% and 30%, **and** OCV within 3.18-3.30 V/cell |
| 5. Certificate | PASS gets a unique cert no. `KH-YYYYMMDD-####` | - |

**Why coulomb counting and not voltage:** LiFePO4 voltage is nearly flat from 20-80% SOC
(+/-0.05 V/cell), so voltage alone can't tell 30% from 50%. The tool counts amp-hours out of
a **fully charged** battery, then uses rested voltage only as a cross-check.
**Every battery must come off a full charge.** The pre-check enforces this.

---

## Quick start (demo, no hardware)

```powershell
python -m pip install -r requirements.txt
python -m kh_bench
```
Open **http://localhost:8000** in Chrome/Edge. The example config runs 8 simulated channels at
120x speed, so a full cycle takes about 2 minutes.

| Page | URL | Used for |
|---|---|---|
| Dashboard | `http://localhost:8000/` | Running batteries, printing certs |
| Setup | `http://localhost:8000/setup` | Loads, battery profiles, cert wording, notifications |

The console only shows real events (run started/passed/failed, webhook errors). Per-request
logging is off.

## Operating the bench (daily workflow)

1. **New job**: click **+ New** and enter customer, PO and lot. Every battery you run goes on that job's batch cert.
2. **Operator**: type your name once. It's remembered and printed on the cert.
3. **Profile**: pick the battery model in *Default profile*.
4. **Scan serials**: click CH1's serial box and scan. The scanner's Enter jumps to the next empty channel, so scan straight down the line.
5. **Hook up** each battery to its channel's load.
6. **Start all staged**. Each card shows live SOC, V, A, Ah out, ETA and a voltage trace.
7. When a card turns **green (PASS)**: disconnect the battery, click **Next battery**, scan the next one and press **Start**.
8. **Red (FAIL)**: the reason is on the card (not full, hit cutoff, no current...). Fix the problem, recharge and rerun. Failed units never appear on a cert.
9. **Print batch certificate** prints one PDF for the job with every PASS serial. Reprints keep the same cert number.
10. **Export CSV** gives the job summary. **Data** on any row gives that run's full V/I/Ah log.

Re-running a serial that already passed asks for confirmation first, so duplicates don't slip through.

---

## Setup page

Everything below is edited at **/setup** and saved to `bench_config.json`. The example file is
never overwritten. Nothing needs hand-edited JSON.

| Section | What you set | When it applies |
|---|---|---|
| Company & Certificate | Name, address, cert title and statement, footer. **Preview certificate** shows a sample PDF | Immediately on Save |
| Channels / Loads | One row per load channel: driver, VISA address, command set, max amps. **Find instruments** scans USB/serial. **Test** talks to a load before you save | On Save. Reconnects all loads, so the bench must be idle |
| Battery Profiles | One per battery model. The header shows Ah removed and time per battery | Immediately (new runs) |
| Notifications | Station name, webhook URLs, which events each one gets. **Send test** | Immediately |
| Advanced | Poll/log intervals, DB file, listen address/port | Interval: immediately. DB/host/port: restart |

Invalid settings (duplicate channel IDs, SCPI channel without an address, etc.) are rejected
with a list of what to fix. Nothing half-saves.

---

## Notifications (Node-RED)

The bench POSTs a JSON event to every webhook you add on the Setup page. Delivery is in the
background with 3 retries, so a down Node-RED never slows or stops a run.

| Event | When | Typical action |
|---|---|---|
| `run.started` | A channel starts discharging | log |
| `run.resting` | Discharge done, resting before OCV | log |
| `run.passed` | Battery PASSED | tell operator to swap |
| `run.failed` | FAIL (not full, cutoff, no current, equipment error...) | **alert** |
| `run.aborted` | Operator pressed Stop | alert |
| `bench.all_done` | Every running channel has finished | tell operator to reload the bench |
| `channel.offline` | A load failed to connect | **alert** |

Every event looks like this. `text` is a ready-to-send one-liner:
```json
{"event": "run.passed", "text": "Bench 1 CH3: KH-0001 PASS - 30.0% SOC, OCV 13.01 V",
 "station": "Bench 1", "time": "2026-10-06T14:07:11", "channel": "CH3", "serial": "KH-0001",
 "result": "PASS", "cert_no": "KH-20261006-0007", "job_id": 1, "run": {"...": "full run record"}}
```

**Hook it up (5 minutes):**
1. In Node-RED: Menu > **Import** > select `node-red/kh-bench-flow.json` > **Deploy**.
2. In the bench: **Setup > Notifications > + Add webhook**. URL: `http://<node-red-pc>:1880/kh-bench`.
3. Click **Send test**. It should show in Node-RED's debug sidebar.
4. **Save**.

The flow routes events three ways: **ALERT** (failed/aborted/offline), **OPERATOR**
(passed/all done) and **info**. It also appends every event to `kh-bench-events.jsonl`. Wire
the ALERT and OPERATOR outputs to whatever you use: email (`node-red-node-email`), Teams,
Slack or Power Automate (`http request` node), or a stack light / andon over MQTT, Modbus or GPIO.

Teams or Power Automate can also take the webhook directly, without Node-RED. Paste their
incoming-webhook URL and use the `text` field.

---

## Hooking up real hardware

1. Install your load's VISA driver (NI-VISA works for all of them) and connect the loads by USB or LAN.
2. Open **/setup > Channels / Loads**.
3. Click **Find instruments**. Found addresses autocomplete in the VISA address box. LAN loads
   may not show; type `TCPIP::<ip>::INSTR`.
4. For each channel: driver `scpi`, VISA address, command set, Max A. Click **Test**: you should
   see the load's ID string and the battery voltage.
5. **Save**. Channels that can't connect show **offline** on the dashboard with the error and a
   **Reconnect** button.

| Command set | Loads |
|---|---|
| `rigol_dl3000` | Rigol DL3021 / DL3031 |
| `siglent_sdl1000` | Siglent SDL1020X / SDL1030X |
| `bk_8600` | B&K Precision 8600 series |
| `chroma_63600` | Chroma 63600 multi-channel mainframe (set *Mainframe ch*) |

To support another load, add its SCPI commands to `PRESETS` in `kh_bench/drivers/scpi.py`.
Nothing else changes.

**Wiring/safety notes**
- Use the load's **remote sense** leads at the battery terminals, or V readings will include lead drop.
- Fuse each battery lead. The tool switches every load OFF on finish, fail, stop and server shutdown, but the hardware fuse is your last line of defense.
- Check each load's power rating: 12.8 V x 25 A = ~320 W per channel.
- First live run: **one battery, one channel, someone watching**.

## Battery profile fields

| Field | Default | Meaning |
|---|---|---|
| `rated_ah` | - | Nameplate capacity. SOC % is relative to this |
| `cells_series` | 4 | 4 = 12.8 V, 8 = 25.6 V, 16 = 51.2 V |
| `discharge_a` | - | Discharge current (capped by channel Max A) |
| `target_soc` | 30 | Stop point |
| `min_final_soc` | 20 | Below this = FAIL (over-discharged) |
| `min_start_cell_v` | 3.32 | Pre-check for "fully charged". Set 0 to disable |
| `cutoff_cell_v` | 2.80 | Safety abort |
| `rest_minutes` | 30 | Rest before the OCV reading |
| `ocv_cell_min/max` | 3.18 / 3.30 | Rested V/cell window at ~30% |

**Have your compliance/shipping contact review the certification statement before using it with customers.**

---

## Project layout

```
kh_bench/
  engine.py        discharge cycle state machine (one async task per channel)
  notify.py        webhook notifications (Node-RED etc.)
  drivers/         simulated.py, scpi.py (Rigol/Siglent/BK/Chroma), base.py
  soc.py           LiFePO4 OCV table + Ah math
  cert.py          PDF certificates (reportlab)
  config.py        settings model, validation, save
  db.py            SQLite: jobs, runs, samples  -> data/bench.db
  api.py           REST API + pages
  static/          dashboard + setup page (vanilla JS, no build step)
node-red/          importable Node-RED flow
tests/             full-cycle tests against the simulator
```

Run the tests with `python -m pytest -q`.

### API (for integrations)

| Area | Endpoints |
|---|---|
| Bench | `GET /api/state`, `POST /api/channels/{id}/start`, `/stop`, `/clear`, `/reconnect` |
| Jobs | `GET/POST /api/jobs`, `GET /api/jobs/{id}/certificate.pdf`, `/report.csv` |
| Runs | `GET /api/runs?serial=`, `GET /api/runs/{id}/certificate.pdf`, `/samples.csv` |
| Setup | `GET/PUT /api/setup`, `POST /api/setup/test-channel`, `/test-webhook`, `GET /api/setup/resources` |
