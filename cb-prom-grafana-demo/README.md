# Couchbase + Prometheus + Grafana Demo

Local demo stack: single-node Couchbase, Prometheus scraping its native
`/metrics` endpoint, and Grafana rendering two dashboards against live data —
one general cluster-health dashboard, and one curated "Avis Fleet
Operations" dashboard built around a car-rental-style workload.

## Architecture

```
┌──────────────┐   scrape :8091/metrics   ┌──────────────┐   query   ┌──────────┐
│  Couchbase   │ ───────────────────────► │  Prometheus  │ ────────► │ Grafana  │
│  Server (1n) │                          │              │           │          │
└──────────────┘                          └──────────────┘           └──────────┘
  :18191 UI (remapped)                       :9090 UI                  :3000 UI
```

All services run as Docker containers on a shared compose network.

## Why the Couchbase ports are remapped

Ports `8091-8096`/`11210` were already held by an unrelated container on this
machine, so `docker-compose.yml` maps Couchbase's UI/API ports to
`18191-18196` and the memcached port to `11211` on the host. Container-to-
container traffic (Prometheus → Couchbase) is unaffected — it uses the
Docker network's internal `couchbase:8091` address, not the remapped host
ports. If you're running this somewhere without that conflict, you can
simplify back to the standard `8091-8096:8091-8096` mapping.

## Layout

```
cb-prom-grafana-demo/
├── docker-compose.yml
├── prometheus/
│   └── prometheus.yml
├── grafana/
│   └── provisioning/
│       ├── datasources/datasource.yml
│       └── dashboards/
│           ├── dashboard.yml
│           ├── couchbase-cluster-overview.json   (68-panel general dashboard)
│           └── avis-fleet-operations.json        (curated business-ops dashboard)
└── loadgen/
    ├── avis_loadgen.py
    └── requirements.txt
```

## Credentials & buckets

| What | Value |
| --- | --- |
| Couchbase UI | http://localhost:18191 — `Administrator` / `password123` |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 — `admin` / `admin` |
| Buckets | `travel-sample` (Couchbase's own sample data), `avis-fleet` (demo workload target) |

Demo credentials only — never reuse outside a throwaway environment.

## Bring the stack up

```bash
cd cb-prom-grafana-demo
docker compose up -d
```

Wait ~30s for Couchbase to start, then initialize the cluster:

```bash
docker exec cb-demo couchbase-cli cluster-init \
  -c localhost \
  --cluster-username Administrator \
  --cluster-password password123 \
  --cluster-ramsize 1024 \
  --cluster-index-ramsize 512 \
  --services data,index,query
```

Load the sample bucket (this image doesn't ship `cbdocloader` — use
`cbimport` instead):

```bash
docker exec cb-demo cbimport json \
  -c http://localhost:8091 -u Administrator -p password123 \
  -b travel-sample -d file:///opt/couchbase/samples/travel-sample.zip -f sample
```

Create the bucket the load generator targets:

```bash
docker exec cb-demo couchbase-cli bucket-create -c localhost \
  -u Administrator -p password123 \
  --bucket avis-fleet --bucket-type couchbase \
  --bucket-ramsize 256 --bucket-replica 0 --wait
```

## Generate traffic

```bash
docker compose run --rm loadgen
```

Seeds 500 vehicles across 10 Avis hub airports into `avis-fleet`, then runs
a mixed KV workload (65% availability GET, 15% reservation-status GET, 15%
checkout/return GET+UPSERT, 5% new-booking UPSERT) until you Ctrl+C. Tune
with env vars:

```bash
docker compose run --rm -e TARGET_OPS=400 -e THREADS=16 -e DURATION_SECONDS=120 loadgen
```

For the built-in Couchbase sample data instead, generate load against
`travel-sample` with `cbc-pillowfight`:

```bash
docker exec cb-demo cbc-pillowfight \
  -U couchbase://localhost/travel-sample \
  -u Administrator -P password123 \
  -I 5000 -B 100
```

## Dashboards

Grafana (http://localhost:3000) → **Couchbase** folder:

- **Couchbase Server — Full Observability** — 68-panel general cluster
  dashboard (KV, Query, Index, Search, XDCR, etc.), sourced from
  [celticht32/Grafana-Dashboard-Couchbase-Metrics](https://github.com/celticht32/Grafana-Dashboard-Couchbase-Metrics).
- **Avis Fleet Operations** — curated dashboard scoped to the `avis-fleet`
  bucket: availability-lookup vs. booking-write throughput, GET/SET latency
  (p50/p95/p99), memory/capacity, disk queue backpressure, fleet growth.

## Known gotchas hit while building this

- **`cbdocloader` doesn't exist** in `couchbase:enterprise-7.6.4` — use
  `cbimport json -f sample` instead (see above).
- **Prometheus 3.x rejects Couchbase's blank Content-Type** on `/metrics`
  with `non-compliant scrape target sending blank Content-Type`. Fixed by
  adding `fallback_scrape_protocol: PrometheusText0.0.4` to the scrape job
  in `prometheus/prometheus.yml`.
- **`cluster.wait_until_ready()`** in the Python SDK expects a `timedelta`,
  not a raw int, in `avis_loadgen.py`.

## Verify

| Check | Command |
| --- | --- |
| Couchbase up | `curl http://localhost:18191/pools` |
| Prometheus target healthy | `curl http://localhost:9090/api/v1/targets` → `couchbase` job `health: up` |
| Metrics flowing | `curl 'http://localhost:9090/api/v1/query?query=kv_ops'` |
| Dashboards loaded | `curl -u admin:admin http://localhost:3000/api/search?type=dash-db` |

## Tear down

```bash
docker compose down -v
```

## Production version of this guide

For rolling this same monitoring approach onto Avis's real, already-running
Couchbase cluster (not this Docker demo), see the companion installation
guide: *Couchbase Monitoring with Prometheus & Grafana — Avis IT
Installation Guide*.
