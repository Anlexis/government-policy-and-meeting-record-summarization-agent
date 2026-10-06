"""AgentCore Platform v1.0"""

# Service layer: domain queries, external API wrappers, data aggregation.
# Nodes call this; this calls shared/services/ for external integrations. It must
# not contain business logic, routing, or credentials — credentials reach a
# service through the invocation context's secret provider, never as arguments or
# module state.
#
# GOV-C2-004 summarizes the document it is given and reads nothing else, so no
# node uses this class today. It is the extension point for a deployment that
# needs to enrich a summary from an external source — a document register, an
# organisation directory — without putting that call inside a node.

from __future__ import annotations

from typing import Any


class Service:
    """Domain service — the boundary between this agent and external data."""

    async def fetch(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch domain data for the given query.

        Not implemented: this template's pipeline is self-contained. Implement it
        in a deployment that needs external lookups, and call it from a node.
        """
        raise NotImplementedError("Service.fetch() has no external source configured")
