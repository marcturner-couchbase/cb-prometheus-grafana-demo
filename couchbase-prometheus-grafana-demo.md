# Demo: Couchbase + Prometheus + Grafana Monitoring Stack

## Goal

Stand up a local demo environment with a single-node Couchbase cluster,
Prometheus scraping its native `/metrics` endpoint, and Grafana rendering
Couchbase's official reference dashboard against live data. This is a
teaching/demo environment, not a production reference.

Give this file to Claude Code and ask it to build and run everything below.

---

## Target Architecture

```
┌──────────────┐     scrape :8091/metrics     ┌──────────────┐     query      ┌──────────┐
│  Couchbase   │ ───────────────────────────► │  Prometheus  │ ─────────────► │ Grafana  │
│  Server (1n) │                               │              │                │          │
└──────────────┘                               └──────────────┘                └──────────┘
   :8091 UI                                       :9090 UI                       :3000 UI
```

All three services run as Docker containers on a shared bridge network via
`docker-compose`.

---

## Prerequisites

- Docker + Docker Compose installed
- Ports `8091`, `8093`, `9090`, and `3000` free on the host
- ~4 GB free RAM for the containers

---

## Step 1 — Project structure

Create this layout:

```
cb-prom-grafana-demo/
├── docker-compose.yml
├── prometheus/
│   └── prometheus.yml
└── grafana/
    └── provisioning/
        ├── datasources/
        │   └── datasource.yml
        └── dashboards/
            └── dashboard.yml
```

## Step 2 — `docker-compose.yml`

```yaml
version: "3.8"

services:
  couchbase:
    image: couchbase:enterprise-7.6.4
    container_name: cb-demo
    ports:
      - "8091-8096:8091-8096"
      - "11210:11210"
    volumes:
      - cb-data:/opt/couchbase/var

  prometheus:
    image: prom/prometheus:latest
    container_name: prom-demo
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
    depends_on:
      - couchbase

  grafana:
    image: grafana/grafana:latest
    container_name: grafana-demo
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
    depends_on:
      - prometheus

volumes:
  cb-data:
```

## Step 3 — `prometheus/prometheus.yml`

Couchbase Server exposes a native Prometheus-compatible endpoint on the
Cluster Manager — no separate exporter needed. It requires basic auth using
Couchbase admin credentials.

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "couchbase"
    metrics_path: /metrics
    scheme: http
    basic_auth:
      username: Administrator
      password: password123
    static_configs:
      - targets: ["couchbase:8091"]
```

> Use the same admin username/password you set when initializing the
> Couchbase cluster in Step 4.

## Step 4 — `grafana/provisioning/datasources/datasource.yml`

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

## Step 5 — `grafana/provisioning/dashboards/dashboard.yml`

```yaml
apiVersion: 1

providers:
  - name: "Couchbase"
    orgId: 1
    folder: "Couchbase"
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
```

(Place the exported Couchbase reference dashboard JSON, downloaded per
Step 7, in `grafana/provisioning/dashboards/` alongside this file so it
auto-loads.)

## Step 6 — Bring up the stack

```bash
cd cb-prom-grafana-demo
docker compose up -d
```

Wait ~30 seconds for Couchbase to start, then initialize the cluster:

```bash
docker exec cb-demo couchbase-cli cluster-init \
  -c localhost \
  --cluster-username Administrator \
  --cluster-password password123 \
  --cluster-ramsize 1024 \
  --cluster-index-ramsize 512 \
  --services data,index,query
```

Load the sample bucket so there's real traffic to observe:

```bash
docker exec cb-demo cbdocloader \
  -c localhost \
  -u Administrator \
  -p password123 \
  -b travel-sample \
  -m 1000 \
  /opt/couchbase/samples/travel-sample.zip
```

## Step 7 — Import the Couchbase reference Grafana dashboard

1. In the Couchbase UI (`http://localhost:8091`), or from Couchbase's
   public dashboard repo, download the official cluster-overview Grafana
   dashboard JSON.
2. Drop the JSON file into `grafana/provisioning/dashboards/`.
3. Restart Grafana so it picks up the new provisioned dashboard:
   ```bash
   docker compose restart grafana
   ```

Alternatively, import manually: Grafana UI → **Dashboards → Import** →
paste the dashboard ID or upload the JSON → select the `Prometheus`
data source created in Step 4.

## Step 8 — Verify

| Check | URL / command |
|---|---|
| Couchbase UI up | `http://localhost:8091` (login `Administrator` / `password123`) |
| Prometheus target healthy | `http://localhost:9090/targets` — `couchbase` job should show **UP** |
| Prometheus has data | Query `kv_ops` in `http://localhost:9090/graph` |
| Grafana reachable | `http://localhost:3000` (login `admin` / `admin`) |
| Dashboard rendering | Grafana → Dashboards → Couchbase folder → cluster overview panel shows live data |

## Step 9 — Generate load (optional, makes the dashboard visibly move)

```bash
docker exec cb-demo cbc-pillowfight \
  -U couchbase://localhost/travel-sample \
  -u Administrator -P password123 \
  -I 5000 -B 100
```

This runs a mixed read/write workload against the sample bucket so panels
like ops/sec and memory usage move in real time during the demo.

## Step 10 — Tear down

```bash
docker compose down -v
```

---

## Talk Track for the Demo

1. Show the Couchbase UI's built-in stats first — set the baseline.
2. Open Prometheus `/targets` — point out the scrape is native, no
   exporter sidecar required.
3. Run a live PromQL query (`rate(kv_ops[1m])`) to show raw metric access.
4. Switch to Grafana — show the same data as a polished dashboard.
5. Kick off `cbc-pillowfight` mid-demo so the audience watches the
   dashboard move in real time.
6. Close by pointing out this same dashboard/scrape config is what a
   customer would run in production, just pointed at their real cluster.

---

## Notes / Gotchas

- Default Couchbase admin credentials above are for demo use only —
  never reuse them outside a throwaway environment.
- If port `8091` is already in use on the host, change the host-side
  mapping in `docker-compose.yml`, not the container-side port.
- Couchbase can take 20–40 seconds to become responsive after container
  start; the `cluster-init` command will fail if run too early — retry
  if it errors on first attempt.
- `metrics_path: /metrics` on the Couchbase Cluster Manager (port 8091)
  is the same endpoint used in production — this demo doesn't diverge
  from a real deployment's scrape config.
