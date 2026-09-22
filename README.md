# Couchbase + Prometheus + Grafana Demo

A local, Docker-based demo showing Couchbase Server monitored end-to-end by
Prometheus and Grafana — no separate exporter needed, since Couchbase 7.0+
ships a native Prometheus `/metrics` endpoint. Built for a customer-facing
(Avis) monitoring demo, but generic enough to reuse anywhere.

## What's in here

| Path | What it is |
| --- | --- |
| [`couchbase-prometheus-grafana-demo.md`](./couchbase-prometheus-grafana-demo.md) | The original spec — the step-by-step build instructions this demo was created from |
| [`cb-prom-grafana-demo/`](./cb-prom-grafana-demo) | The working demo: docker-compose stack, Prometheus config, two Grafana dashboards, and a Python KV load generator |

## Quick start

```bash
cd cb-prom-grafana-demo
docker compose up -d
```

Then follow [`cb-prom-grafana-demo/README.md`](./cb-prom-grafana-demo/README.md)
for cluster init, sample data, running the load generator, and the
dashboards — that file has the full walkthrough, credentials, and the
gotchas hit while building this (missing `cbdocloader`, a Prometheus 3.x
scrape-protocol quirk, a Python SDK `timedelta` fix).

## Dashboards

- **Couchbase Server — Full Observability** — general 68-panel cluster
  dashboard (KV, Query, Index, Search, XDCR, etc.)
- **Avis Fleet Operations** — curated dashboard over a simulated
  rental-fleet workload: availability-lookup vs. booking-write throughput,
  GET/SET latency (p50/p95/p99), capacity, and disk backpressure

## Taking this to production

The demo is Docker-only and not meant to be deployed as-is. For wiring the
same Prometheus + Grafana approach onto an existing, already-running
Couchbase cluster, see the companion installation guide referenced in
[`cb-prom-grafana-demo/README.md`](./cb-prom-grafana-demo/README.md#production-version-of-this-guide).

## Demo credentials

Everything in this repo uses throwaway demo credentials
(`Administrator`/`password123`, Grafana `admin`/`admin`) — never reuse them
outside a disposable environment.
