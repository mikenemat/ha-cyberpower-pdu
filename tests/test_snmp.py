"""Tests for the SNMP engine MIB warm-up.

These use real pysnmp (no network) to guard against the in-loop blocking-I/O
regression: pysnmp lazily reads MIB .py files from disk on the first request/
response, which Home Assistant flags as a blocking call. async_create_engine
pre-loads both per-engine MibBuilders off the loop so that never happens.
"""

from __future__ import annotations

from custom_components.cyberpower_pdu.snmp import async_create_engine


async def test_async_create_engine_preloads_both_mib_builders() -> None:
    """A created engine has both pysnmp MibBuilders pre-loaded.

    pysnmp keeps two independent builders per engine — the message dispatcher's
    (response path) and the hlapi MibViewController's (request-resolution path).
    Both must be warm or the first SNMP exchange reads MIB files in the loop.
    """
    engine = await async_create_engine()
    try:
        dispatcher = engine.message_dispatcher.mib_instrum_controller.get_mib_builder()
        # Instance module the response path imports lazily on the first reply.
        assert "__SNMPv2-MIB" in dispatcher.mibSymbols

        # The request-path view controller is pre-created, cached on the engine,
        # and pre-warmed — so hlapi reuses it instead of building a cold one.
        view = engine.cache["mibViewController"]
        assert "SNMPv2-SMI" in view.mibBuilder.mibSymbols

        # They are genuinely separate builders (the crux of the fix).
        assert view.mibBuilder is not dispatcher
    finally:
        engine.close_dispatcher()
