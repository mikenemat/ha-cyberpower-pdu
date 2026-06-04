# Security Policy

## Reporting a vulnerability

Please report security issues **privately**. Do not open a public issue.

Use GitHub's private reporting: the repository's **Security → Advisories → Report
a vulnerability** form. You can expect an initial response within a few days.

## Scope and notes

This integration talks to a PDU over **SNMP**. Be aware that:

- **SNMP v1/v2c** community strings are sent in cleartext on your network; treat
  them as low-sensitivity and isolate management traffic (VLAN/management LAN)
  where possible. Use **SNMPv3** with auth/priv if your PDU supports it.
- Credentials are stored in the Home Assistant config entry like any other
  integration and are redacted from diagnostics output.
- The integration performs no outbound internet communication; it only contacts
  the PDU IP you configure (or one discovered via DHCP).

## Supported versions

The latest released version on the default branch receives fixes.
