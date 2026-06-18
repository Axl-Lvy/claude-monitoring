#!/usr/bin/env python3
"""Claude Code PostToolUse hook: classify a Bash command and emit it as OTLP.

Claude Code's native ``tool_result`` event carries ``tool_name`` but never the
command string, so a Bash call cannot be told apart as "build" vs "test" vs
"git" downstream. This hook fills that gap: it reads the PostToolUse payload on
stdin, classifies the command into a coarse category, and ships a single OTLP
log record (``event_name=bash_command``) to the local collector. The collector
forwards it to Loki, where ``command_category`` becomes structured metadata the
dashboard can group on.

Design constraints:
  * Never block or slow the tool. Any error is swallowed and we exit 0.
  * Standard library only, so it runs anywhere python3 does.
  * Fire-and-forget POST with a short timeout.
"""

import json
import re
import sys
import urllib.request

# Collector OTLP/HTTP logs endpoint (same one Cowork points at).
OTLP_LOGS_ENDPOINT = "http://localhost:4318/v1/logs"
POST_TIMEOUT_SECONDS = 2

# Ordered (category, regex) rules. First match wins, so the more specific
# intent (test, build, lint) is checked before generic runners and shells.
# Patterns match anywhere in the command so pipelines and prefixes still hit.
RULES = [
    # Test runs. Checked before build because "mvn test" / "gradle test"
    # share a head program with build goals.
    ("test", r"""
        \b(pytest|jest|vitest|mocha|phpunit|rspec|ctest)\b
        | \b(go|cargo)\s+test\b
        | \bgo\s+test\b
        | \b(mvn|mvnw)\b[^\n]*\b(test|verify|surefire|failsafe|integration-test)\b
        | \bgradlew?\b[^\n]*\btest\b
        | \b(npm|yarn|pnpm)\s+(run\s+)?test\b
        | \bdotnet\s+test\b
        | \bphp\s+artisan\s+test\b
    """),
    # Lint / format / static analysis.
    ("lint_format", r"""
        \b(eslint|prettier|ruff|black|isort|flake8|pylint|mypy|gofmt|golangci-lint
          |clippy|rubocop|stylelint|ktlint|ktfmt|checkstyle|spotless|shellcheck)\b
        | \b(mvn|mvnw)\b[^\n]*\b(checkstyle|spotless|fmt|format|pmd)\b
        | \bgradlew?\b[^\n]*\b(ktfmtFormat|ktfmtCheck|spotless\w*|lint\w*|detekt)\b
        | \b(npm|yarn|pnpm)\s+(run\s+)?(lint|format)\b
        | \bcargo\s+(fmt|clippy)\b
        | \bgo\s+(fmt|vet)\b
    """),
    # Build / compile / package.
    ("build", r"""
        \b(make|cmake|ninja|bazel|buck)\b
        | \b(mvn|mvnw)\b[^\n]*\b(install|compile|package|clean)\b
        | \bgradlew?\b[^\n]*\b(build|assemble|compile\w*|jar|bootJar|installDist)\b
        | \b(npm|yarn|pnpm)\s+(run\s+)?build\b
        | \b(go|cargo)\s+build\b
        | \b(tsc|webpack|vite\s+build|rollup|esbuild|gradle\s+build)\b
        | \bdocker\s+build\b
        | \bdocker\s+compose\s+build\b
    """),
    # Version control.
    ("vcs", r"""
        ^\s*git\b | \bgit\s | \bgh\b | \bjj\b | \bhg\b | \bsvn\b
    """),
    # Dependency / package management.
    ("package", r"""
        \b(npm|yarn|pnpm)\s+(install|add|ci|i)\b
        | \bpip3?\s+install\b
        | \b(poetry|uv|pipenv)\s+(install|add|sync|lock)\b
        | \b(mvn|mvnw)\b[^\n]*\bdependency:\w+
        | \b(apt|apt-get|dnf|yum|brew|pacman|apk)\s+(install|update|add|upgrade)\b
        | \bcargo\s+(add|update|fetch)\b
        | \bgo\s+(mod|get)\b
    """),
    # Search / read / inspect. Read-only exploration.
    ("search_read", r"""
        \b(grep|rg|ag|ack|find|fd|locate|cat|bat|less|more|head|tail|awk|jq|yq
          |wc|sort|uniq|cut|diff|tree|stat|file|ls|ll|column|nl|tac)\b
        | \bsed\s+-n\b
        | \bgit\s+(log|diff|status|show|blame)\b
    """),
    # Filesystem mutation.
    ("edit_fs", r"""
        \b(rm|rmdir|mv|cp|mkdir|touch|ln|chmod|chown|tar|unzip|zip|gzip|truncate
          |install|dd|rsync)\b
        | \bsed\s+-i\b
        | (^|\s)>\s*\S | (^|\s)>>\s*\S
    """),
    # Process / service / container runners and ad-hoc execution.
    ("run_exec", r"""
        \bdocker\b | \bdocker\s+compose\b | \bkubectl\b | \bhelm\b
        | \bcurl\b | \bwget\b | \bhttp\b
        | \b(python3?|node|deno|bun|ruby|php|java|sh|bash|zsh)\s+\S
        | \./\S | \bsystemctl\b | \bservice\b | \bnpx\b
    """),
    # Shell navigation / environment / trivial.
    ("shell_nav", r"""
        ^\s*(cd|echo|pwd|export|source|\.|env|set|unset|alias|sleep|true|false
            |which|type|printf|read|cd|clear|exit|history)\b
    """),
]

COMPILED = [(name, re.compile(pat, re.VERBOSE | re.IGNORECASE)) for name, pat in RULES]


def classify(command: str) -> str:
    """Return the category for a Bash command, or ``other`` if nothing fits."""
    if not command or not command.strip():
        return "other"
    for name, rx in COMPILED:
        if rx.search(command):
            return name
    return "other"


def command_head(command: str) -> str:
    """First meaningful token (skips VAR=val prefixes and sudo), for drilldown."""
    for tok in command.split():
        if tok in ("sudo", "command"):
            continue
        if "=" in tok and not tok.startswith("-"):
            continue
        return tok[:40]
    return ""


def emit(category: str, payload: dict, command: str) -> None:
    """Fire a single OTLP log record at the collector. Best effort."""
    tool_resp = payload.get("tool_response") or {}
    # PostToolUse success signal varies by Claude Code version; default true.
    success = "true"
    if isinstance(tool_resp, dict) and tool_resp.get("is_error"):
        success = "false"

    # Byte sizes mirror Claude Code's own tool_result_size_bytes /
    # tool_input_size_bytes. The result bytes are what re-enters the
    # conversation as input tokens next turn, so result_size_bytes is the
    # token-weight of this command kind (tokens ~= bytes / 4). The dashboard
    # sums it per command_category to get a token-share percentage.
    try:
        result_bytes = len(json.dumps(tool_resp))
    except Exception:
        result_bytes = 0

    attrs = [
        ("event_name", "bash_command"),
        ("command_category", category),
        ("command_head", command_head(command)),
        ("success", success),
        ("session_id", str(payload.get("session_id", ""))),
        ("result_size_bytes", str(result_bytes)),
        ("input_size_bytes", str(len(command))),
    ]
    log_record = {
        # Collector stamps observed time; omit a client clock to stay simple.
        "body": {"stringValue": "claude_code.bash_command"},
        "attributes": [
            {"key": k, "value": {"stringValue": v}} for k, v in attrs if v != ""
        ],
    }
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "claude-code"},
                        }
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "claude_monitoring.bash_hook"},
                        "logRecords": [log_record],
                    }
                ],
            }
        ]
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OTLP_LOGS_ENDPOINT, data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=POST_TIMEOUT_SECONDS).close()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command", "")
    try:
        emit(classify(command), payload, command)
    except Exception:
        # Never let a monitoring hook break the user's tool call.
        pass


if __name__ == "__main__":
    main()
    # Always succeed so Claude Code never sees a hook failure.
    sys.exit(0)
