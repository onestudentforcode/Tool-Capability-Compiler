"""Arg-less executable tools for the optimize-pipeline CLI end-to-end test.

The JSON executable path has no typed contracts (consumes/produces are
Python-path only), so the validate-stage slow runs use arg-less tools: no
cross-layer chaining is needed and every trial completes. Capabilities live
in the JSON topology, which is all the fast gate consumes.
"""

from __future__ import annotations


async def fetch() -> dict:
    return {"tool": "fetch"}


async def finalize() -> dict:
    return {"tool": "finalize"}
