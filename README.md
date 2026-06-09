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

## Claude Desktop (Cowork)

The stack also receives telemetry from **Claude Cowork** (the agentic feature in Claude Desktop), not just the Claude Code CLI. Both products emit the same five OTLP log events (`api_request`, `tool_result`, `tool_decision`, `user_prompt`, `api_error`), distinguished by the `service.name` resource attribute (`claude-code` vs `cowork`).

Requirements (Anthropic side):
- Team or Enterprise plan, Claude Desktop **>= 1.1.4173**.
- An admin enables it in *Organization settings > Cowork*: set the OTLP endpoint to this collector (`http://<host>:4318`), pick HTTP/JSON or HTTP/protobuf, add auth headers if any.

Unlike the CLI, Cowork has no env vars; config lives in the app. Cowork emits **log events only** (no Prometheus metrics), so its panels are Loki-based.

In the dashboard, the **Source** variable filters by `service.name`, and the **CLI + Cowork (unified, log-based)** row shows cost, tokens, latency, tools, and MCP usage for either or both products. The original Prometheus-backed rows stay CLI-only (Cowork emits no metrics).

The collector preserves each product's `service.name` (`action: insert` in `otel/config.yaml`) and only defaults it to `claude-code` when a client sends none.

## Notes
- Prometheus retention: 90 days (`--storage.tsdb.retention.time=90d` in compose).
- Traces currently go to the collector `debug` exporter only (printed to logs). Add a tracing backend (Tempo, Jaeger) in `otel/config.yaml` if needed.
- Volumes (`prometheus-data`, `grafana-data`, `loki-data`) are Docker-managed; nothing written to repo dir.
