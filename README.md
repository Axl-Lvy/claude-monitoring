# claude-monitoring

Local OpenTelemetry stack for collecting **Claude Code** metrics, logs, and traces, then visualizing them in Grafana.

Claude Code can emit OTLP telemetry (token usage, tool calls, session activity, cost, etc.) when the right env vars are set. This repo runs the receiving side: an OTel Collector, Prometheus (metrics), Loki (logs), and Grafana (dashboards) — all in Docker Compose. No data leaves the host.

## Components

| Service | Image | Port | Role |
|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib` | 4317 (gRPC), 4318 (HTTP), 8889 (Prom scrape) | Receive OTLP from Claude Code, fan out to Prometheus + Loki |
| `prometheus` | `prom/prometheus` | 14703 | Scrape collector, store metrics 90 days |
| `loki` | `grafana/loki` | 3100 | Store logs |
| `grafana` | `grafana/grafana` | 3000 | Dashboards (anonymous viewer enabled, admin/admin) |

Config files:
- `otel/config.yaml` — collector pipelines (metrics → Prometheus, logs → Loki, traces → debug)
- `prometheus/prometheus.yml` — scrapes `otel-collector:8889`
- `grafana/provisioning/` — auto-provisioned datasources + dashboard loader
- `grafana/dashboards/claude-code-overview.json` — preloaded Claude Code dashboard

## Quick start

```sh
sh install.sh
```

This single script:
1. Installs Docker if missing (requires `sudo sh install.sh` in that case)
2. Starts the full monitoring stack via Docker Compose
3. Adds the required OTel env vars to your shell profile (`~/.zshrc` or `~/.bashrc`)

Once done, open a new terminal (or `source ~/.zshrc`) and start Claude Code. Telemetry flows automatically.

### Open Grafana
http://localhost:3000 -- dashboard **Claude Code Overview** preloaded under *Dashboards*.

Anonymous viewer enabled (no login needed to view). Admin login: `admin` / `admin`.

### Stop / reset
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
