# Jackery Energy (Home Assistant custom integration)

An extended version of [theak/jackery-homeassistant](https://github.com/theak/jackery-homeassistant)
built for a home setup of **Explorer 5000 Plus + Explorer 300 Plus + Smart Transfer Switch + SolarSaga 500W**.
It uses the same Jackery cloud account login. It installs under its own domain (`jackery_energy`),
so it can run alongside the original while you compare them.

## What it adds over the original

| Area | Original | Jackery Energy |
|---|---|---|
| Devices | Owned devices | Owned **and shared-with-you** devices, plus Smart Transfer Switch and expansion battery packs |
| Power sources | Input/output watts | **Solar power**, **grid input**, **battery charge/discharge**, and a **Power source** sensor (Solar / Grid / Grid + solar / Battery / Idle) |
| Whole system | — | A virtual **Jackery System** device: combined battery %, stored kWh, total solar/grid/load |
| History | Live values only | **kWh energy counters** (Energy-dashboard ready, survive restarts) for solar, grid, load and battery in/out |
| Connectivity | — | **Online** sensor per device, with last-error attribute |
| Unknown fields | Ignored | **Every** field the cloud reports becomes a `Raw …` sensor. That's how Smart Transfer Switch data shows up. |
| Diagnostics | — | One-click diagnostics download with serials and credentials redacted |
| Polling | Fixed 60 s | Adjustable (default 30 s) |
| Entity ids | Random-ish | Predictable: `sensor.jackery_e5000_battery`, `sensor.jackery_system_solar_power`… |

The panel itself (SolarSaga 500W) has no Wi-Fi. Its output is read from the power station
it's plugged into: **Solar power = total input − AC input**.

## Install

**Option A – manual copy**
1. Copy the `custom_components/jackery_energy` folder into your Home Assistant `config/custom_components/`.
   The *File editor* or *Studio Code Server* add-on, Samba share, or SSH all work.
2. Restart Home Assistant.
3. Settings → Devices & services → **Add integration** → search **Jackery Energy** → sign in with your Jackery app email/password.

**Option B – HACS custom repository**
Push this folder to your own GitHub repo, then HACS → ⋮ → Custom repositories → add it as an *Integration*.

## Dashboard

v0.3 dashboard highlights: device pictures (tap to open each device's page), a live **Estimated runtime**
card (home backup = 5000 Plus stored energy ÷ (circuits load − solar); 300 Plus = stored ÷ its own drain),
a solar section with 3-day graph and daily bars, and a 30-day energy history. It needs `mini-graph-card`
and `apexcharts-card` from Marketplace.

1. Settings → Dashboards → **Add dashboard** → *New dashboard from scratch* → name it "Jackery".
2. Open it → ✏️ Edit → ⋮ → **Raw configuration editor** → paste `dashboards/jackery_dashboard.yaml` → Save.

It uses only built-in cards. It has two views:
- **Overview:** where power is coming from, solar/grid/load/battery tiles, battery gauges (including solar vs 500 W),
  runtime estimates, per-device status (online, charging, AC/USB/12V outputs, UPS, voltages, temperature, error codes),
  and a live table of every field the Smart Transfer Switch reports.
- **History:** a 24 h power-flow graph, a power-source timeline, 48 h battery levels, a 30-day daily-energy bar chart, and 7-day hourly power.

For an animated flow diagram, install *Power Flow Card Plus* from HACS and add `dashboards/optional_power_flow_card.yaml`.

**Device naming.** Devices are detected as `e5000`, `e300` and `sts`. If one was detected differently,
the log line `Jackery device … -> … (slug …)` at startup says how. Find-and-replace that prefix in the dashboard YAML.

### Energy dashboard (optional)
Settings → Dashboards → Energy:
- *Solar production:* `sensor.jackery_system_solar_energy`
- *Battery storage:* in = `sensor.jackery_system_battery_charge_energy`, out = `sensor.jackery_system_battery_discharge_energy`
- *Grid consumption:* `sensor.jackery_system_grid_input_energy`. This counts only grid power the Jackerys draw,
  not loads the transfer switch passes straight through from the grid.

## Smart Transfer Switch

The switch reports its own `ip`/`op` (shown as **Source power** / **Circuits load**), a UPS flag
(**Backup ready**), and the backup battery % and runtime mirrored from the 5000 Plus. The **Jackery System**
device uses this. Whatever the circuits draw beyond what the 5000 Plus supplies is counted as **grid
pass-through** (`sensor.jackery_system_grid_to_circuits_power`). It's added to system grid power and
total load, so the power source reads *Grid* when your house circuits run on the grid.

### Getting friendly names for the remaining fields

The other fields (`dg`, `dh`, `ds`, `de`, `dt`, …) aren't documented. `dg`/`dh`/`ds` look like lifetime energy counters.
This integration doesn't guess. It exposes every field as a sensor named `Raw <code>` on the transfer-switch device,
and the dashboard lists them all live. To get proper names (for example grid voltage, per-circuit load, grid/backup mode):

1. Settings → Devices & services → Jackery Energy → ⋮ → **Download diagnostics**.
2. Ideally grab two downloads: one with the grid on, and one with a load running on a backed-up circuit.
3. Share the file(s). Serial numbers, email and password are redacted. The codes can then be mapped in
   `sensor.py` (`PROP_SENSORS`) and `logic.py` (`FRIENDLY_KEYS`).

The same goes for any 5000 Plus fields the integration doesn't know yet. They're created as disabled
`Raw …` sensors under the device's diagnostic section.

## Entity reference

Replace `{d}` with `e5000`, `e300` (or `system` where noted).

| Entity | Notes |
|---|---|
| `sensor.jackery_{d}_battery` | % |
| `sensor.jackery_{d}_solar_power` / `grid_input_power` / `output_power` | W |
| `sensor.jackery_{d}_battery_net_power` | W, + charging / − discharging |
| `sensor.jackery_{d}_power_source`, `battery_state` | enum |
| `sensor.jackery_{d}_time_remaining`, `time_to_full` | h (blank when not applicable) |
| `sensor.jackery_{d}_stored_energy` | Wh = % × rated capacity (5040 Wh / 288 Wh, plus packs) |
| `sensor.jackery_{d}_solar_energy` … `battery_discharge_energy` | kWh counters |
| `sensor.jackery_{d}_ac_voltage`, `ac_voltage_l2`, `ac_frequency`, `battery_temperature`, `error_code` | |
| `binary_sensor.jackery_{d}_online`, `charging`, `solar_active`, `grid_input_active` | |
| `binary_sensor.jackery_{d}_ac_output`, `usb_output`, `car_output`, `dc_output`, `ups_mode` | whichever the model reports |
| `sensor.jackery_system_battery_level`, `load_power`, `devices_online` | combined across power stations |
| `binary_sensor.jackery_system_on_battery` | handy for "power outage" automations |
| `sensor.jackery_sts_raw_*` | transfer-switch fields (see above) |

## Notes and limits
- Read-only for now. Toggling AC/USB outputs needs Jackery's MQTT command channel. That could be a follow-up.
- Accounts registered in the EU region may not work, same as the original.
- The cloud updates roughly every 30–60 s. History graphs show that resolution.
- Credits: login/encryption from theak/jackery-homeassistant; property map from jlopez/socketry and
  porcupin26/private_jack (model codes, `acov1` for 240 V split-phase). MIT licensed.
