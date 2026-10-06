# Policy & Meeting Record Summarization Agent

AI agent for summarizing government policy documents and meeting records, built with Agentic Star.

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: Government
> **Template ID**: GOV-C2-004

## Overview

Government policy papers, council minutes and public-consultation records are long, and the
people who need them — a civil servant preparing a briefing, or a citizen reading a published
record — need a short structured version of the same document.

This template classifies a Japanese public-sector document (policy paper / cabinet decision,
committee minutes, or public consultation), extracts the agenda items, the decisions taken and
the action items with their owners, and renders one of two summaries: an **internal** briefing
with full detail, or a **public** version in which passages carrying non-disclosable content are
replaced with a redaction notice. A separate output gate runs last and removes classified
material from either mode, so no caller can obtain it by choosing the internal audience.

Classification, extraction and summarisation are deterministic — a keyword taxonomy and pattern
matching, no model call — so the same document always yields the same summary, and the summary
can never contain text that was not in the input.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent runs inside the platform, which supplies its secrets, audit sink and request routing. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | The `framework.*` and `shared.*` packages every module here imports. It is distributed through the platform's own package index, not the public one. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

There is no fallback or degraded mode. Without the framework package installed, every module
here fails to import and the process does not start — the failure is immediate and total rather
than a half-running agent. The test suite is the exception: it exercises the nodes against the
installed framework and needs no platform connection.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

