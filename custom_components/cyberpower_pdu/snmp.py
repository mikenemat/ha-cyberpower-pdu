"""Thin async SNMP client for CyberPower PDUs built on pysnmp (pure-Python).

This module knows nothing about Home Assistant; it only speaks SNMP and exposes
small ``get``/``walk``/``set`` primitives plus value coercion helpers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    Integer32,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    get_cmd,
    set_cmd,
    usmAesCfb128Protocol,
    usmDESPrivProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
    walk_cmd,
)
from pysnmp.proto import rfc1905

from .const import (
    AUTH_MD5,
    AUTH_SHA,
    PRIV_AES,
    PRIV_DES,
    SNMP_MAX_VARBINDS,
    SNMP_RETRIES,
    SNMP_TIMEOUT,
    VERSION_V1,
    VERSION_V2C,
    VERSION_V3,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_PROTOCOLS = {
    AUTH_MD5: usmHMACMD5AuthProtocol,
    AUTH_SHA: usmHMACSHAAuthProtocol,
}
_PRIV_PROTOCOLS = {
    PRIV_DES: usmDESPrivProtocol,
    PRIV_AES: usmAesCfb128Protocol,
}

# Sentinels returned by SNMP for absent rows/columns.
_ABSENT = (rfc1905.NoSuchObject, rfc1905.NoSuchInstance, rfc1905.EndOfMibView)


class SnmpError(Exception):
    """Raised when an SNMP request fails (timeout, auth, or protocol error)."""


@dataclass(slots=True)
class SnmpCredentials:
    """Everything needed to build pysnmp auth data for read and write."""

    version: str
    community: str = "public"
    write_community: str = "private"
    username: str = ""
    auth_protocol: str = "none"
    auth_key: str = ""
    priv_protocol: str = "none"
    priv_key: str = ""

    def _read_auth(self) -> CommunityData | UsmUserData:
        return self._build(self.community)

    def _write_auth(self) -> CommunityData | UsmUserData:
        return self._build(self.write_community)

    def _build(self, community: str) -> CommunityData | UsmUserData:
        if self.version == VERSION_V1:
            return CommunityData(community, mpModel=0)
        if self.version == VERSION_V2C:
            return CommunityData(community, mpModel=1)
        if self.version == VERSION_V3:
            auth_proto = _AUTH_PROTOCOLS.get(self.auth_protocol, usmNoAuthProtocol)
            priv_proto = _PRIV_PROTOCOLS.get(self.priv_protocol, usmNoPrivProtocol)
            return UsmUserData(
                self.username,
                authKey=self.auth_key or None,
                privKey=self.priv_key or None,
                authProtocol=auth_proto,
                privProtocol=priv_proto,
            )
        raise SnmpError(f"Unsupported SNMP version: {self.version}")


class CyberPowerSnmp:
    """Reusable async SNMP session against a single PDU."""

    def __init__(
        self,
        host: str,
        port: int,
        credentials: SnmpCredentials,
        *,
        timeout: float = SNMP_TIMEOUT,
        retries: int = SNMP_RETRIES,
    ) -> None:
        self._host = host
        self._port = port
        self._credentials = credentials
        self._read_auth = credentials._read_auth()
        self._write_auth = credentials._write_auth()
        self._timeout = timeout
        self._retries = retries
        self._engine: SnmpEngine | None = None
        self._target: UdpTransportTarget | None = None
        # The device answers one request at a time; serialize to avoid
        # interleaving a control SET with a coordinator poll on one engine.
        self._lock = asyncio.Lock()

    async def _ensure(self) -> tuple[SnmpEngine, UdpTransportTarget]:
        if self._engine is None:
            self._engine = SnmpEngine()
        if self._target is None:
            self._target = await UdpTransportTarget.create(
                (self._host, self._port),
                timeout=self._timeout,
                retries=self._retries,
            )
        return self._engine, self._target

    async def get(self, oids: list[str]) -> dict[str, Any]:
        """GET a list of OIDs, chunked under the device's varbind limit.

        Returns a mapping of requested-OID-string -> pysnmp value (or ``None``
        when the instance is absent). Raises :class:`SnmpError` on transport or
        protocol failures.
        """
        result: dict[str, Any] = {}
        async with self._lock:
            engine, target = await self._ensure()
            for start in range(0, len(oids), SNMP_MAX_VARBINDS):
                chunk = oids[start : start + SNMP_MAX_VARBINDS]
                objects = [ObjectType(ObjectIdentity(o)) for o in chunk]
                err_ind, err_stat, err_idx, var_binds = await get_cmd(
                    engine, self._read_auth, target, ContextData(), *objects
                )
                if err_ind:
                    raise SnmpError(f"SNMP GET failed: {err_ind}")
                if err_stat:
                    raise SnmpError(
                        f"SNMP GET error: {err_stat.prettyPrint()} (index {err_idx})"
                    )
                for requested, var_bind in zip(chunk, var_binds, strict=False):
                    _, value = var_bind
                    result[requested] = None if isinstance(value, _ABSENT) else value
        return result

    async def walk(self, base_oid: str) -> dict[str, Any]:
        """Walk a subtree, returning {oid_string: value} within ``base_oid``."""
        result: dict[str, Any] = {}
        async with self._lock:
            engine, target = await self._ensure()
            async for err_ind, err_stat, _err_idx, var_binds in walk_cmd(
                engine,
                self._read_auth,
                target,
                ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if err_ind:
                    raise SnmpError(f"SNMP WALK failed: {err_ind}")
                if err_stat:
                    raise SnmpError(f"SNMP WALK error: {err_stat.prettyPrint()}")
                for var_bind in var_binds:
                    oid, value = var_bind
                    if isinstance(value, _ABSENT):
                        continue
                    # str(oid) is the numeric dotted form; prettyPrint() would
                    # resolve to symbolic MIB names and break index lookups.
                    result[str(oid)] = value
        return result

    async def set_int(self, oid: str, value: int) -> None:
        """SET an integer-valued OID using the write credentials."""
        async with self._lock:
            engine, target = await self._ensure()
            err_ind, err_stat, _err_idx, _ = await set_cmd(
                engine,
                self._write_auth,
                target,
                ContextData(),
                ObjectType(ObjectIdentity(oid), Integer32(value)),
            )
            if err_ind:
                raise SnmpError(f"SNMP SET failed: {err_ind}")
            if err_stat:
                raise SnmpError(f"SNMP SET error: {err_stat.prettyPrint()}")

    def close(self) -> None:
        """Tear down the SNMP engine/dispatcher."""
        if self._engine is not None:
            try:
                self._engine.close_dispatcher()
            except Exception:  # best-effort cleanup
                _LOGGER.debug("Error closing SNMP dispatcher", exc_info=True)
            self._engine = None
            self._target = None


# --- value coercion helpers --------------------------------------------------


def as_int(value: Any) -> int | None:
    """Coerce a pysnmp value to int, or None."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def as_str(value: Any) -> str | None:
    """Coerce a pysnmp DisplayString/OctetString to a stripped str, or None."""
    if value is None:
        return None
    try:
        return str(value).strip()
    except (ValueError, TypeError):
        return None


def as_mac(value: Any) -> str | None:
    """Coerce a 6-byte OctetString into a colon MAC address, or None."""
    if value is None:
        return None
    try:
        raw = bytes(value)
    except (ValueError, TypeError):
        return None
    if len(raw) != 6:
        return None
    return ":".join(f"{b:02x}" for b in raw)
