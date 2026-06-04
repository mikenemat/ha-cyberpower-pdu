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
- It runs on **pure-Python [`pysnmp`]** — the exact library and version Home
  Assistant core already ships (`pysnmp==7.1.22`). No `snmpwalk`, no compiled
  binaries, no static-binary hacks on HAOS.

## Supported devices

| | |
|---|---|
| Verified | CyberPower **PDU41008** (firmware 1.2.4) |
| Should work | CyberPower switched/switched-metered ePDUs exposing `CPS-MIB` outlet control + load tables |
| Discovery | DHCP (CyberPower OUI `00:0C:15`) + manual IP |

> The integration auto-discovers the number of outlets and banks, so single-bank
> and multi-bank models are both handled.

## Before you start: enable SNMP on the PDU

In the PDU web UI (or CLI), under **Network → SNMPv1** (or **SNMPv3**):

1. **Enable SNMPv1** (or SNMPv3).
2. For SNMPv1, make sure there is a community with **read access** (default
   `public`) and, to control outlets, a community with **write access**
   (commonly `private`). Note both — you'll enter them during setup.
3. For SNMPv3, create a user and note the username and any auth/priv settings.

Read-only SNMP is enough for monitoring; **outlet control requires write access**.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/mikenemat/ha-cyberpower-pdu`, category **Integration**.
2. Install **CyberPower PDU**, then restart Home Assistant.

### Manual

Copy `custom_components/cyberpower_pdu` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Add via **Settings → Devices & Services → Add Integration → CyberPower PDU**, or
accept the **discovered** PDU when it appears.

1. **Connection** — host/IP, SNMP port (default `161`), and SNMP version.
2. **Credentials**
   - *v1/v2c*: read community (default `public`) and write community (default `private`).
   - *v3*: username, and optional authentication/privacy protocols + keys.

The integration verifies the device is a CyberPower PDU and identifies it by serial
number / MAC, so re-discovery won't create duplicates.

### Options

- **Poll interval** (seconds, default `15`, min `5`) — Settings → the integration → **Configure**.

## Entities

One Home Assistant **device** per PDU, with:

### Switches
- One `switch` per outlet (named from the outlet's label on the PDU). Each carries
  `outlet_number` and `bank` attributes. Toggling is optimistic and reconciled by a
  follow-up poll.

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

- Built on a `DataUpdateCoordinator`: a network outage or PDU reboot marks entities
  *unavailable* and recovers automatically on the next successful poll.
- If the PDU is unreachable at startup, setup retries (`ConfigEntryNotReady`) instead
  of failing permanently.

## Troubleshooting

- **Outlets show state but won't switch** → the **write community** (v1/v2c) or the
  v3 user lacks write access. Fix it on the PDU and reconfigure the entry.
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

## License

[MIT](LICENSE) © Michael Nemat

[pysnmp]: https://github.com/lextudio/pysnmp
[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[pytest-homeassistant-custom-component]: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
[validate-badge]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/validate.yml/badge.svg
[validate-workflow]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/validate.yml
[test-badge]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/test.yml/badge.svg
[test-workflow]: https://github.com/mikenemat/ha-cyberpower-pdu/actions/workflows/test.yml
