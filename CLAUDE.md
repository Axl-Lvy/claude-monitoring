# claude-monitoring

Local OTLP monitoring stack for **Claude Code** (CLI) and **Claude Cowork** (Claude Desktop). OTel Collector → Prometheus (metrics) + Loki (logs) → Grafana. All in Docker Compose, nothing leaves the host.

## Layout

- `docker-compose.yml` — the four services (otel-collector, prometheus, loki, grafana).
- `otel/config.yaml` — collector pipelines. Metrics → Prometheus, logs → Loki, traces → debug.
- `prometheus/prometheus.yml` — scrapes the collector at `otel-collector:8889`.
- `grafana/provisioning/` — auto-provisioned datasources (uids `prometheus`, `loki`) and the dashboard loader.
- `grafana/dashboards/claude-code-overview.json` — the "Claude Code & Cowork" dashboard.
- `install.sh` — installs Docker if missing, starts the stack, adds CLI env vars to the shell profile.

## CLI vs Cowork

Claude Code CLI and Cowork emit the **same five OTLP log events**: `api_request`, `tool_result`, `tool_decision`, `user_prompt`, `api_error`. They differ only by the `service.name` resource attribute: `claude-code` vs `cowork`.

## Bash command-kind hook (`bash_command` event)

`tool_result` carries `tool_name` but **never the command string**, so Bash calls can't be split into build / test / git / search downstream. `hooks/classify-bash-command.py` is a Claude Code **PostToolUse** hook (matcher `Bash`) that classifies the command and emits a custom OTLP log: `event_name=bash_command`, with `command_category` (test, build, lint_format, vcs, package, search_read, edit_fs, run_exec, shell_nav, other), `command_head`, `result_size_bytes`, and `input_size_bytes` as structured metadata. It POSTs to the collector at `localhost:4318/v1/logs` with `service.name=claude-code`, so it flows to Loki on the same path as native events. `install.sh` registers it idempotently in `~/.claude/settings.json`. CLI-only (Cowork can't run hooks). The token-share panels in the **Tools** dashboard row read it.

## Token-share by tool kind (no real per-tool token field)

Tokens and cost live only on `api_request`; tool events carry no token count. The honest proxy is **result bytes**: `tool_result_size_bytes` (native events) and the hook's `result_size_bytes` (Bash) are the result payload that re-enters the conversation as input tokens next turn, so `tokens ~= bytes / 4`. Summing those bytes per `command_category` / `mcp_server_name` / `tool_name` and rendering as a pie `percent` gives token share. The constant factor cancels in a percentage. The Tools row has three such pies (by tool, by Bash command kind, by MCP server) plus two byte-weighted tables. This is a proxy, not billed tokens — label it as such on any new panel.

To tune the classifier, mine real command samples from local session transcripts (OTLP strips them, transcripts keep them):

```sh
cat ~/.claude/projects/*/*.jsonl \
 | jq -rc 'select(.message.content) | .message.content[]? | select(.type=="tool_use" and .name=="Bash") | .input.command | gsub("\n";" ⏎ ")'
```

- CLI emits **both** Prometheus metrics and Loki log events. Configured via env vars (`install.sh`, OTLP endpoint `localhost:4317` gRPC).
- Cowork emits **log events only** (no metrics). Configured in Claude Desktop (Organization settings > Cowork → OTLP endpoint `http://<host>:4318`). No env vars.

The collector must use `action: insert` (not `upsert`) on `service.name` so Cowork keeps its own value; `upsert` would relabel everything to `claude-code` and make the two indistinguishable.

## Loki data model (important when editing dashboard queries)

Only `service_name` is a Loki **stream label** (usable inside `{}`). Everything else — `event_name`, `cost_usd`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `duration_ms`, `model`, `tool_name`, `success`, `decision`, `mcp_server_name`, `user_email`, ... — is **structured metadata**, which must be filtered AFTER the `|` pipe.

- Right: `{service_name=~"$source"} | event_name=` + "`api_request`" + ` | unwrap cost_usd [$__range]`
- Wrong: `{service_name=~"$source", event_name="api_request"}` → returns nothing.

`unwrap <field>` works on structured metadata. `mcp_server_name!=""` isolates MCP tool calls (empty for CLI, populated for Cowork).

## Dashboard structure

- Prometheus-backed rows (cost, tokens, productivity, etc.) are **CLI-only** — Cowork emits no metrics.
- The **CLI + Cowork (unified, log-based)** row is built on Loki and works for both sources.
- The **Source** variable is `label_values(service_name)` from Loki, `refresh: 1` (on load). It is time-scoped, so a `service_name` value only appears while that source has data in the selected window.

## Common operations

```sh
docker compose ps                              # status
docker compose restart otel-collector grafana  # reload config.yaml / dashboard
docker compose logs --since 1m otel-collector  # collector logs
docker compose down            # stop, keep data
docker compose down -v         # stop + wipe all volumes (destroys history)
```

Test a LogQL query against live data before wiring it into a panel:

```sh
curl -s -G 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query=sum(count_over_time({service_name=~"claude-code"} | event_name=`api_request` [30d]))'
```

Grafana auto-reloads provisioned dashboards, but restart it to apply immediately. The collector reads `otel/config.yaml` at start, so restart it after editing.

Loki delete API is **not** enabled by default (returns 404). Per-source purges need the compactor delete API enabled, or `docker compose down -v` (wipes everything).
