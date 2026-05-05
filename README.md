# claude-monitoring

Local OpenTelemetry stack for collecting **Claude Code** metrics, logs, and traces, then visualizing them in Grafana.

Claude Code can emit OTLP telemetry (token usage, tool calls, session activity, cost, etc.) when the right env vars are set. This repo runs the receiving side: an OTel Collector, Prometheus (metrics), Loki (logs), and Grafana (dashboards) — all in Docker Compose. No data leaves the host.

## Components

| Service | Image | Port | Role |
|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib` | 4317 (gRPC), 4318 (HTTP), 8889 (Prom scrape) | Receive OTLP from Claude Code, fan out to Prometheus + Loki |
| `prometheus` | `prom/prometheus` | 9090 | Scrape collector, store metrics 90 days |
| `loki` | `grafana/loki` | 3100 | Store logs |
| `grafana` | `grafana/grafana` | 3000 | Dashboards (anonymous viewer enabled, admin/admin) |

Config files:
- `otel/config.yaml` — collector pipelines (metrics → Prometheus, logs → Loki, traces → debug)
- `prometheus/prometheus.yml` — scrapes `otel-collector:8889`
- `grafana/provisioning/` — auto-provisioned datasources + dashboard loader
- `grafana/dashboards/claude-code-overview.json` — preloaded Claude Code dashboard

## Usage

### 1. Start the stack
```sh
docker compose up -d
```

Check everything is up:
```sh
docker compose ps
```

### 2. Point Claude Code at the collector

Export these in your shell (or add to `~/.zshrc` / `~/.bashrc`):
```sh
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

Restart Claude Code. Telemetry now flow to the collector.

### 3. Open Grafana
http://localhost:3000 — dashboard **Claude Code Overview** preloaded under *Dashboards*.

Anonymous viewer enabled (no login needed to view). Admin login: `admin` / `admin`.

### 4. Stop / reset
```sh
docker compose down            # stop, keep data
docker compose down -v         # stop + wipe Prometheus/Loki/Grafana volumes
```

## Layout
```
.
├── docker-compose.yml
├── otel/config.yaml
├── prometheus/prometheus.yml
└── grafana/
    ├── provisioning/{datasources,dashboards}/
    └── dashboards/claude-code-overview.json
```

## Notes
- Prometheus retention: 90 days (`--storage.tsdb.retention.time=90d` in compose).
- Traces currently go to the collector `debug` exporter only (printed to logs). Add a tracing backend (Tempo, Jaeger) in `otel/config.yaml` if needed.
- Volumes (`prometheus-data`, `grafana-data`, `loki-data`) are Docker-managed; nothing written to repo dir.
