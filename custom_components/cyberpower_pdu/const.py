"""Constants for the CyberPower PDU integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cyberpower_pdu"

# --- Config entry keys -------------------------------------------------------
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_VERSION: Final = "snmp_version"
CONF_COMMUNITY: Final = "community"
CONF_WRITE_COMMUNITY: Final = "write_community"
CONF_USERNAME: Final = "username"
CONF_AUTH_PROTOCOL: Final = "auth_protocol"
CONF_AUTH_KEY: Final = "auth_key"
CONF_PRIV_PROTOCOL: Final = "priv_protocol"
CONF_PRIV_KEY: Final = "priv_key"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# --- SNMP versions -----------------------------------------------------------
VERSION_V1: Final = "v1"
VERSION_V2C: Final = "v2c"
VERSION_V3: Final = "v3"
SNMP_VERSIONS: Final = [VERSION_V1, VERSION_V2C, VERSION_V3]

# --- v3 protocol options (values map to pysnmp protocols in snmp.py) ----------
AUTH_NONE: Final = "none"
AUTH_MD5: Final = "md5"
AUTH_SHA: Final = "sha"
AUTH_PROTOCOLS: Final = [AUTH_NONE, AUTH_MD5, AUTH_SHA]

PRIV_NONE: Final = "none"
PRIV_DES: Final = "des"
PRIV_AES: Final = "aes"
PRIV_PROTOCOLS: Final = [PRIV_NONE, PRIV_DES, PRIV_AES]

# --- Defaults ----------------------------------------------------------------
DEFAULT_PORT: Final = 161
DEFAULT_COMMUNITY: Final = "public"
DEFAULT_WRITE_COMMUNITY: Final = "private"
DEFAULT_VERSION: Final = VERSION_V1
DEFAULT_SCAN_INTERVAL: Final = 15  # seconds
MIN_SCAN_INTERVAL: Final = 5

# Device hard limit observed on PDU41008: 12 varbinds/GET fails at 13.
# We chunk reads below that for a safety margin.
SNMP_MAX_VARBINDS: Final = 10
SNMP_TIMEOUT: Final = 4.0
SNMP_RETRIES: Final = 1

# --- OID building blocks -----------------------------------------------------
ENTERPRISE: Final = "1.3.6.1.4.1.3808"
# CyberPower ePDU subtree (sysObjectID is enterprises.3808.1.1.3 on these PDUs)
EPDU: Final = f"{ENTERPRISE}.1.1.3"

# Identification group (.1)
OID_IDENT_MODEL: Final = f"{EPDU}.1.1.0"
OID_IDENT_HW_REV: Final = f"{EPDU}.1.2.0"
OID_IDENT_FW_REV: Final = f"{EPDU}.1.3.0"
OID_IDENT_NAME: Final = f"{EPDU}.1.5.0"
OID_IDENT_SERIAL: Final = f"{EPDU}.1.6.0"
OID_IDENT_OUTLET_COUNT: Final = f"{EPDU}.1.8.0"

# Load / metering table (.2.3.1.1) — columns are appended with ".<row>"
OID_LOAD_INDEX: Final = f"{EPDU}.2.3.1.1.1"
OID_LOAD_CURRENT: Final = f"{EPDU}.2.3.1.1.2"  # deci-amps
OID_LOAD_BANK_ID: Final = f"{EPDU}.2.3.1.1.5"  # 0 = device total, 1..n = bank
OID_LOAD_VOLTAGE: Final = f"{EPDU}.2.3.1.1.6"  # deci-volts
OID_LOAD_POWER: Final = f"{EPDU}.2.3.1.1.7"  # watts (real power)
OID_LOAD_APPARENT: Final = f"{EPDU}.2.3.1.1.8"  # VA (device row only)
OID_LOAD_PF: Final = f"{EPDU}.2.3.1.1.9"  # power factor percent (device row only)
OID_LOAD_ENERGY: Final = f"{EPDU}.2.3.1.1.10"  # deci-kWh (device row only)

# Outlet control table (.3.3.1.1) — write commands
OID_OUTLET_CMD: Final = f"{EPDU}.3.3.1.1.4"  # SET 1=on, 2=off, 3=reboot
OID_OUTLET_CTRL_NAME: Final = f"{EPDU}.3.3.1.1.2"

# Outlet status table (.3.5.1.1) — read state
OID_OUTLET_STATUS_NAME: Final = f"{EPDU}.3.5.1.1.2"
OID_OUTLET_STATUS_STATE: Final = f"{EPDU}.3.5.1.1.4"  # 1=on, 2=off
OID_OUTLET_STATUS_BANK: Final = f"{EPDU}.3.5.1.1.6"

# Standard MIB-2
OID_SYS_OBJECT_ID: Final = "1.3.6.1.2.1.1.2.0"
OID_SYS_NAME: Final = "1.3.6.1.2.1.1.5.0"
OID_IF_PHYS_ADDRESS: Final = "1.3.6.1.2.1.2.2.1.6.1"

# --- Magic values ------------------------------------------------------------
OUTLET_STATE_ON: Final = 1
OUTLET_STATE_OFF: Final = 2
OUTLET_CMD_ON: Final = 1
OUTLET_CMD_OFF: Final = 2
OUTLET_CMD_REBOOT: Final = 3

DEVICE_BANK_ID: Final = 0  # row in the load table representing the whole PDU

MANUFACTURER: Final = "CyberPower"
