"""trading-journey deterministic trade-history pipeline (Python 3.8+ stdlib only).

Fetch raw data (Robinhood via a host-driven plan/ingest loop, Hyperliquid
public API over HTTP), then compute every number with scripts. The caller
only interprets the JSON and HTML outputs.
"""
VERSION = "0.3.0"
