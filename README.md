# CyberPower PDU for Home Assistant

[![hacs][hacs-badge]][hacs]
[![Validate][validate-badge]][validate-workflow]
[![Test][test-badge]][test-workflow]

A Home Assistant integration for **CyberPower switched PDUs** over SNMP. Developed
and tested against a **PDU41008**; other CyberPower ePDUs that expose the same
`CPS-MIB` (enterprise `1.3.6.1.4.1.3808`) should work too.

It does exactly three things, by design:

- 🔌 **Control outlets** — turn each outlet on/off
- 👁️ **Show outlet status** — live on/off state, kept in sync with the device
- ⚡ **Show power consumption** — per **bank/breaker** (or whole-PDU total on
  single-bank units): real power, current, voltage, and total energy

## Why SNMP?

The PDU offers telnet, an HTTP web UI, and SNMP. This integration uses **SNMP**:

- It is **connectionless (UDP) and session-less**, so polling never locks you out
  of the web/telnet console. The web UI and CLI share a *single* management
  session — an integration that held one would break your console access.
- It is **fast** and reads everything needed in one batched poll.
- It runs on the same **pure-Python [`pysnmp`]** library Home Assistant core
  already ships (the `pysnmp` 7.x line). No `snmpwalk`, no compiled binaries, no
  static-binary hacks on HAOS.

## Requirements

- **Home Assistant 2025.8 or newer.** That release is when HA core moved to
  `pysnmp` 7.x, whose API this integration uses; the requirement is declared as
  `pysnmp>=7.1.21,<8` so it stays satisfied by whatever 7.x your HA ships (no
  version conflict, no forced reinstall). HACS enforces the minimum.
- SNMP enabled on the PDU — see [below](#before-you-start-enable-snmp-on-the-pdu).

## Supported devices

Only the **PDU41008** has been verified against real hardware. The integration
speaks the CyberPower **CPS ePDU MIB** (`1.3.6.1.4.1.3808.1.1.3`), so any
CyberPower **switched** PDU with a built-in network/management port that exposes
the same outlet-control and load tables should work. Outlet count and bank
layout are auto-detected, so single- and multi-bank units are both handled.

**Expected to be compatible** — CyberPower *Switched* PDUs:

- **Switched** (`PDU41xxx` series) — e.g. PDU41005, **PDU41008 ✅ (verified)**
- **Switched Metered-by-Outlet** (`PDU81xxx` series) — e.g. PDU81005, PDU81008
  (per-outlet metering isn't surfaced; on/off + bank/total power are)

**Not supported** — CyberPower *Metered* or *Monitored* (non-switched) PDUs:
they report power but have no outlet-control table, so on/off isn't possible.

> Everything except the PDU41008 is inferred from the shared MIB and not yet
> tested. If you try another model, please [open an issue][issues] (success or
> failure) so this list can grow.

## Before you start: enable SNMP on the PDU

SNMP must be enabled on the PDU's management card. The steps below are for the
**PDU Remote Management** web UI (ePDU firmware, verified on a PDU41008); exact
menu wording varies a little between firmware versions. The telnet/SSH console
exposes the identical settings via the `snmpv1` / `snmpv3` commands.

1. Browse to `http://<pdu-ip>/` and sign in with an **Administrator** account.
2. Open **System → Network Service → SNMPv1 Service** (some firmware: **Network → SNMP**).
3. Set **SNMPv1 Service** to **Enable**.
4. In the community table, configure at least one community — and, for outlet
   control, a writable one:
   - a **Read** community — e.g. `public` (used for monitoring and discovery)
   - a **Read/Write** community — e.g. `private` (**required to switch outlets**)
   - set the allowed NMS / host to `0.0.0.0` (any) or your Home Assistant IP
5. **Apply** the changes.

Prefer SNMPv3? Open **System → Network Service → SNMPv3 Service**, add a user,
and note its username plus any authentication/privacy protocols and keys.

> 🔒 Monitoring needs only **Read** access. **Outlet control requires a
> Read/Write community (v1/v2c) or a read-write SNMPv3 user.** Auto-discovery
> probes with the `public` read community, so keeping a `public` read community
> enabled lets the network scan find the PDU.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/mikenemat/ha-cyberpower-pdu`, category **Integration**.
2. Install **CyberPower PDU**, then restart Home Assistant.

### Manual

Copy `custom_components/cyberpower_pdu` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Go to **Settings → Devices & Services → Add Integration → CyberPower PDU**.

1. **Pick PDUs** — the scan (see *Discovery*) lists every CyberPower PDU it
   finds; tick the ones to add (all selected by default) and they're configured
   together in one go. Already-added PDUs are filtered out. Or choose **Enter an
   IP address manually**.
2. **Manual entry** (always available) — host/IP, SNMP port (default `161`), and
   SNMP version, for a single PDU.
3. **Credentials** — entered once and applied to every selected PDU:
   - *v1/v2c*: read community (default `public`) and write community (default `private`).
   - *v3*: username, plus optional authentication/privacy protocols and keys.

The integration identifies each PDU by its **serial number** (falling back to
MAC) — **never by IP** — so the same unit is never added twice and a changed IP
never creates a duplicate. The physical topology is read once and cached per
serial, so reloads never re-derive (and never drop) your existing entities.

### Discovery

Discovery is an **active SNMP scan of your local subnet(s)** — no DHCP required —
shown behind a **progress bar** so you can see it working:

1. Home Assistant's network adapters determine the local IPv4 subnet(s).
2. The host's **ARP/neighbour table is checked first**, so only addresses that
   are actually alive get probed — no blind spraying. If the ARP cache is cold,
   it falls back to a full SNMP sweep of the subnet.
3. Each candidate gets one short SNMP query; those answering under the CyberPower
   enterprise OID are offered for setup. Probes run on a pooled set of SNMP
   engines, so even a cold-cache full `/24` sweep finishes in ~10 seconds.

Manual IP entry is always available for PDUs the scan can't reach (static IP on a
different segment, a non-`public` read community, etc.).

### Options

- **Poll interval** (seconds, default `15`, min `5`) — Settings → the integration → **Configure**.

## Entities

One Home Assistant **device** per PDU, with:

### Switches
- One `switch` per outlet, named **`<device name> <outlet label>`** from the PDU
  (e.g. *theater-pdu-3 LED*). Each carries `outlet_number` and `bank` attributes.
  Toggling is optimistic and reconciled by a follow-up poll.
- Outlet labels (and the device name) are read every poll, so renaming an outlet
  in the PDU admin flows straight through to Home Assistant. The entity's internal
  ID is tied to the **immutable outlet index**, so a relabel never recreates the
  entity, breaks history, or disturbs your own customisations.

### Sensors

| Sensor | Scope | Unit | Default |
|---|---|---|---|
| Power | PDU total | W | enabled |
| Current | PDU total | A | enabled |
| Voltage | PDU total | V | enabled |
| Energy | PDU total | kWh (`total_increasing`) | enabled |
| Apparent power | PDU total | VA | disabled |
| Power factor | PDU total | — | disabled |
| Bank _n_ power | per bank* | W | enabled |
| Bank _n_ current | per bank* | A | enabled |

\* Per-bank sensors are created only on multi-bank PDUs; single-bank units expose
the totals only. Energy feeds the Home Assistant **Energy dashboard**.

## Resilience

- Built on a `DataUpdateCoordinator`: a network outage or PDU reboot marks
  entities *unavailable* and recovers automatically on the next good poll.
- **Capped exponential backoff** — after a failed poll the interval doubles
  (15 → 30 → 60 s) up to a 60 s ceiling, then snaps back to normal on the first
  success, so a down PDU isn't hammered.
- **Self-healing IP** — if a PDU stays unreachable at the backoff ceiling, the
  integration rescans the network for its **MAC** and, if it has moved to a new
  IP, updates the entry automatically. This replaces DHCP for IP-change recovery.
- If the PDU is unreachable at startup, setup retries (`ConfigEntryNotReady`).

## Troubleshooting

- **Outlets show state but won't switch** → the **write community** (v1/v2c) or the
  v3 user lacks write access. Fix it on the PDU, then reload the entry (or remove
  and re-add the PDU if you changed the community name).
- **"Could not reach the PDU over SNMP"** → SNMP not enabled, wrong community/version,
  or a firewall blocking UDP/161.
- **Wrong/odd readings on a different model** → please open an issue with an
  `snmpwalk` of `1.3.6.1.4.1.3808.1.1.3`; the load-table layout may differ.

## Brand icon

A wireframe PDU icon lives in [`brands/`](brands/). To make it appear in the Home
Assistant UI it must be submitted to the
[home-assistant/brands](https://github.com/home-assistant/brands) repository under
`custom_integrations/cyberpower_pdu/`. See [`brands/README.md`](brands/README.md).

## Development

```bash
pip install -r requirements_test.txt ruff
ruff check custom_components tests
ruff format --check custom_components tests
pytest
```

Tests use [`pytest-homeassistant-custom-component`] with a fake SNMP backend, so no
hardware is needed for CI.

### Releasing

Bump `version` in `custom_components/cyberpower_pdu/manifest.json` (semver) in the
release commit. On push to `main`, the **Release** workflow tags and publishes a
matching `vX.Y.Z` GitHub release — HACS surfaces that as an available update, and
the manifest version is what Home Assistant shows as installed.

## License

[MIT](LICENSE) © Michael Nemat

[pysnmp]: https://github.com/lextudio/pysnmp
[issues]: https://github.com/mikenemat/ha-cyberpower-pdu/issues
[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[pytest-homeassistant-custom-component]: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
[validate-badge]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/validate.yml/badge.svg
[validate-workflow]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/validate.yml
[test-badge]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/test.yml/badge.svg
[test-workflow]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/test.yml
