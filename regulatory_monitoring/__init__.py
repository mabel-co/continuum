"""Regulatory monitoring pipeline.

Fetches the curated regulatory sources, hashes their content, detects
changes, and writes durable JSONL change records compatible with the
change-notification workflow documented in the README.

Stdlib-only, matching the convention of ``continuum_monitoring`` and
``continuum_integration``.
"""
