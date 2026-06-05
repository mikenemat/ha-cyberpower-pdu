"""Active discovery of CyberPower PDUs on the local network.

Strategy: look at the host's ARP/neighbour table to find IPs that are actually
alive on the local subnet(s), SNMP-probe those for the CyberPower enterprise
OID, and return the matches. If the ARP table yields no candidates (cold cache),
fall back to a bounded SNMP sweep of the local subnet(s). Pure UDP SNMP — no
raw sockets, no privileges, no DHCP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import ipaddress
import logging

from homeassistant.components import network
from homeassistant.core import HomeAssistant
from pysnmp.hlapi.v3arch.asyncio import SnmpEngine

from .const import (
    DEFAULT_COMMUNITY,
    DEFAULT_PORT,
    DISCOVERY_PER_ENGINE,
    DISCOVERY_POOL_SIZE,
    DISCOVERY_RETRIES,
    DISCOVERY_TIMEOUT,
    EPDU,
    MAX_DISCOVERY_HOSTS,
    OID_IDENT_MODEL,
    OID_IDENT_SERIAL,
    OID_IF_PHYS_ADDRESS,
    OID_SYS_OBJECT_ID,
    VERSION_V1,
)
from .snmp import CyberPowerSnmp, SnmpCredentials, SnmpError, as_mac, as_str

_LOGGER = logging.getLogger(__name__)

_ARP_TABLE = "/proc/net/arp"


@dataclass(slots=True)
class DiscoveredPdu:
    """A CyberPower PDU found on the network."""

    host: str
    mac: str | None
    model: str
    serial: str


def _read_arp_table() -> dict[str, str]:
    """Return {ip: mac} from the Linux ARP table; empty if unavailable."""
    table: dict[str, str] = {}
    try:
        with open(_ARP_TABLE, encoding="ascii") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return table
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 4:
            ip, mac = parts[0], parts[3].lower()
            if mac and mac != "00:00:00:00:00:00":
                table[ip] = mac
    return table


async def _local_networks(hass: HomeAssistant) -> list[ipaddress.IPv4Network]:
    """Return the enabled, reasonably-sized local IPv4 networks."""
    networks: list[ipaddress.IPv4Network] = []
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ip_info in adapter["ipv4"]:
            try:
                net = ipaddress.ip_network(
                    f"{ip_info['address']}/{ip_info['network_prefix']}", strict=False
                )
            except ValueError:
                continue
            if net.is_loopback:
                continue
            if net.num_addresses > MAX_DISCOVERY_HOSTS + 2:
                _LOGGER.debug("Skipping oversized network %s for discovery", net)
                continue
            networks.append(net)
    return networks


def is_epdu(sys_object_id: str) -> bool:
    """True only for CyberPower ePDU PDUs (subtree …3808.1.1.3).

    Other CyberPower gear (UPS = …3808.1.1.1, ATS, etc.) shares the 3808
    enterprise but a different subtree and must be rejected.
    """
    return sys_object_id == EPDU or sys_object_id.startswith(f"{EPDU}.")


async def _probe_host(
    host: str, community: str, engine: SnmpEngine | None = None
) -> DiscoveredPdu | None:
    """SNMP-probe one host; return a DiscoveredPdu only if it is a PDU."""
    snmp = CyberPowerSnmp(
        host,
        DEFAULT_PORT,
        SnmpCredentials(version=VERSION_V1, community=community),
        timeout=DISCOVERY_TIMEOUT,
        retries=DISCOVERY_RETRIES,
        engine=engine,
    )
    try:
        # sysObjectID alone first: a v1 multi-OID GET fails wholesale when an OID
        # is absent, and identifies the device type before fetching PDU details.
        head = await snmp.get([OID_SYS_OBJECT_ID])
        if not is_epdu(as_str(head.get(OID_SYS_OBJECT_ID)) or ""):
            return None
        details = await snmp.get(
            [OID_IDENT_MODEL, OID_IDENT_SERIAL, OID_IF_PHYS_ADDRESS]
        )
    except SnmpError:
        return None
    finally:
        snmp.close()

    return DiscoveredPdu(
        host=host,
        mac=as_mac(details.get(OID_IF_PHYS_ADDRESS)),
        model=as_str(details.get(OID_IDENT_MODEL)) or "PDU",
        serial=as_str(details.get(OID_IDENT_SERIAL)) or "",
    )


async def async_discover_pdus(
    hass: HomeAssistant,
    community: str = DEFAULT_COMMUNITY,
    progress_cb: Callable[[int, int], None] | None = None,
) -> list[DiscoveredPdu]:
    """Discover CyberPower PDUs on the local network(s).

    ``progress_cb(done, total)`` is invoked as each candidate host is probed, so
    callers (the config flow) can drive a progress bar.
    """
    networks = await _local_networks(hass)
    if not networks:
        return []
    arp = await hass.async_add_executor_job(_read_arp_table)

    # Prefer ARP-live hosts (already proven present) to avoid blind scanning.
    candidates: set[str] = {
        ip for ip in arp if any(_in_network(ip, net) for net in networks)
    }
    # Cold ARP cache: fall back to a bounded sweep of the local subnet(s).
    if not candidates:
        for net in networks:
            candidates.update(str(host) for host in net.hosts())

    hosts = list(candidates)
    total = len(hosts)
    done = 0
    # A pool of shared engines, each capped to a few concurrent probes.
    pool_size = min(DISCOVERY_POOL_SIZE, total) or 1
    engines = [SnmpEngine() for _ in range(pool_size)]
    sems = [asyncio.Semaphore(DISCOVERY_PER_ENGINE) for _ in range(pool_size)]

    async def _bounded(index: int, host: str) -> DiscoveredPdu | None:
        nonlocal done
        slot = index % pool_size
        async with sems[slot]:
            result = await _probe_host(host, community, engines[slot])
        done += 1
        if progress_cb is not None:
            progress_cb(done, total)
        return result

    try:
        found = [
            pdu
            for pdu in await asyncio.gather(
                *(_bounded(i, host) for i, host in enumerate(hosts))
            )
            if pdu is not None
        ]
    finally:
        for engine in engines:
            engine.close_dispatcher()

    # Backfill MAC from ARP when SNMP did not provide one.
    for pdu in found:
        if not pdu.mac and pdu.host in arp:
            pdu.mac = arp[pdu.host]
    _LOGGER.debug(
        "Discovery probed %d candidate(s), found %d PDU(s)", len(candidates), len(found)
    )
    return found


async def async_find_host_for_mac(
    hass: HomeAssistant, mac: str, community: str = DEFAULT_COMMUNITY
) -> str | None:
    """Return the current IP of the PDU with the given MAC, if found."""
    return await async_find_host_for_device(hass, None, mac, community)


async def async_find_host_for_device(
    hass: HomeAssistant,
    serial: str | None,
    mac: str | None,
    community: str = DEFAULT_COMMUNITY,
) -> str | None:
    """Return the current IP of the PDU identified by serial or MAC, if found."""
    mac_target = mac.lower() if mac else None
    for pdu in await async_discover_pdus(hass, community):
        if serial and pdu.serial == serial:
            return pdu.host
        if mac_target and pdu.mac and pdu.mac.lower() == mac_target:
            return pdu.host
    return None


def _in_network(ip: str, net: ipaddress.IPv4Network) -> bool:
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False
