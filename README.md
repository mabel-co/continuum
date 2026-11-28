# Continuum

> **Experimental — AI-generated, not human-validated.** This repository is
> built by an AI agent team under human ownership. Its contents have **not
> been validated by human experts**; we publish it for you to explore and give
> feedback on, not for production or compliance use. There is **no guarantee
> that anything here is correct or complete, and no liability is accepted**
> for its use. Nothing here is professional, compliance, or legal advice.
> The licence terms (including warranty disclaimers) apply in full.

**About this project.** Continuum is built almost entirely by an organised
team of AI agents, under human ownership and direction. It is an experiment
in agent-built software as much as a compliance product — the code, the
methodology, and the documentation are all artifacts of that experiment.

**Status: demo concept.** Continuum is a demonstration of an approach to
AI-assisted regulatory compliance. It is not a finished commercial product,
not compliance advice, and not a substitute for qualified professional or
legal advice. The regulatory content it references is provided to help you
navigate the rules yourself; mapping quality is demo-grade and improving.

**Licence and warranty.** The code is licensed under the Apache License,
Version 2.0; the regulatory content pack is licensed under CC BY 4.0
(see [`mabel-co/continuum-content`](https://github.com/mabel-co/continuum-content)).
Neither licence is a promise about fitness for any purpose. Both licences
and all accompanying materials are provided **as is**, without warranty of
any kind, express or implied — see the `LICENSE` files for the full
disclaimer, which applies to the maximum extent permitted by law.

**"Assurer" and "Continuum" are names of this project; these names and any
associated marks are not licensed for use under either licence** (see
Apache-2.0 §6 for the analogous rule on contributors' names).

**Privacy.** Self-hosted Continuum collects no usage data or telemetry by
default. Any opt-in telemetry is documented where you switch it on. GDPR
and other obligations for your deployment are yours; this statement is
there to make your path easier, not to disclaim it.

## No ISO text

This repository and its content pack contain **zero verbatim ISO text**.
ISO references are clause numbers with our own mapping language.

## Quickstart

The engine lives in this repository. It is **stdlib-only** — three
Python packages, no third-party dependencies, Python 3.11+.

```bash
git clone https://github.com/mabel-co/continuum.git
cd continuum
python -m unittest discover -s tests   # 77 tests, all stdlib
```

### Publish a regulatory change notification

`continuum_integration` turns an activated change-log record into a canonical
event on an append-only, idempotent JSONL notification bus:

```bash
python -m continuum_integration publish \
    --change-log examples/change-log.json \
    --base-dir ./meta
cat ./meta/notifications/*.jsonl
```

Events land under `meta/notifications/<UTC-day>.jsonl`; re-publishing the same
`event_id` is a no-op, and non-`activated` records are skipped with a reason.
The `fanout` module dispatches events to named downstream channels.

### Run the monitoring dashboard

`continuum_monitoring` aggregates compliance operation events into counters,
error rates, latency percentiles, and alert-rule state, served over stdlib
HTTP:

```bash
MONITORING_PORT=8080 python -m continuum_monitoring
# then open http://localhost:8080/dashboard  (also: /health)
```

Use it as a library to record `ComplianceOperationEvent`s and evaluate alert
rules (`alerting.py` — console, JSONL, or webhook sinks).

### Monitor regulatory sources

`regulatory_monitoring` is the detection step that feeds the notification
bus: it fetches a curated list of public regulatory sources (RSS/Atom
feeds, JSON APIs, and page snapshots), hashes normalised content, detects
changes against a durable state file, and appends change records for
`continuum_integration` to publish:

```bash
python -m regulatory_monitoring --data-dir ./data
# or a single cadence: python -m regulatory_monitoring --cadence daily
```

Sources live in `regulatory_monitoring/sources.json`; a run exits non-zero
if any fetch fails, so a scheduler sees failures in the run result, not
just the log. Network access is the one runtime need of this package;
everything else runs offline.

**What is in this release.** All three stdlib-only engine packages:
regulatory source monitoring (`regulatory_monitoring`), the change
notification bus (`continuum_integration`), and compliance operation
monitoring with a live dashboard (`continuum_monitoring`).

**What is not.** The hosted demonstration slice around these packages —
the FastAPI web app, database-backed evidence store, and report generator
that tie them into a running service — depends on third-party frameworks
and deployment-specific configuration, so it is not part of this
engine release. The engine itself is complete and runnable as shipped.

**Layout:** `continuum_integration/`, `continuum_monitoring/`, and
`regulatory_monitoring/` are the three importable packages (installable
via `pip install .`); `tests/` is the unittest suite; `examples/` holds
sample input. This is experimental software — see the disclaimer above.

**Related repositories.** The regulatory content pack this engine publishes
change notifications about lives in
[`mabel-co/continuum-content`](https://github.com/mabel-co/continuum-content).
Product overview, methodology, and roadmap:
[Assurer — the Continuum product page](https://mabel-co.github.io/website/continuum/).
