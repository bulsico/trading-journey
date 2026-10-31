"""trading-journey deterministic trade-history pipeline (Python 3.8+ stdlib only).

Fetch raw data (Robinhood MCP via a bot-driven plan/ingest loop, Hyperliquid
public API over HTTP), then compute every number with scripts. The bot only
interprets the JSON outputs.
"""
VERSION = "1.0.0"
