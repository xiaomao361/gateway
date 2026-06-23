# ClaraCore Gateway

A single MCP entry point for several independent services:

- Memoria
- Continuity
- InnerLife
- Grafana MCP
- Context assembly and managed-service controls

The gateway does not replace the underlying services. It exposes their tools
through one MCP connection and adds a small orchestration layer.

## How it runs

The MCP server uses stdio transport. An MCP client starts it on demand and the
process exits when that client disconnects. It is not intended to run as an
unattached background daemon.

Use the shared launcher:

```text
/path/to/gateway/run_mcp.sh
```

Example client configuration:

```json
{
  "mcpServers": {
    "claracore": {
      "command": "/path/to/gateway/run_mcp.sh",
      "env": {
        "CLARACORE_AGENT_ID": "my-agent",
        "CLARACORE_PYTHON": "/path/to/python3"
      }
    }
  }
}
```

See [mcp.example.json](mcp.example.json) for a copyable template.

## Private configuration

The launcher reads an optional local file:

```text
~/.claracore/gateway/gateway.env
```

Example:

```bash
CLARACORE_PYTHON=/path/to/python3
GRAFANA_MCP_BINARY=/path/to/grafana-mcp
GRAFANA_URL=https://grafana.example.com
GRAFANA_API_KEY=replace-me
```

Do not commit this file.

## Runtime service registry

The public registry at `runtime/services.yaml` contains only the core services.
Machine-specific applications belong in:

```text
runtime/services.local.yaml
```

That file is ignored by Git and is merged over the public registry at runtime.
Paths in both files may use:

- `${CLARACORE_ROOT}`
- `${CLARACORE_PYTHON}`
- `${CLARACORE_AGENT_ID}`
- `${HOME}`

## Gateway tools

The gateway adds tools for:

- assembling memory, continuity, and inner-state context;
- recording current progress separately from durable facts;
- listing, starting, stopping, and restarting registered services;
- reading managed-service logs.

Grafana runs as a short-lived MCP subprocess. A failed Grafana request is
isolated from the other providers.

## Login services

Background daemons are separate from the stdio MCP process. A generic macOS
LaunchAgent template is available at:

```text
deploy/launch-agent.plist.example
```

Copy it outside the repository and replace every placeholder with local paths.

## Tests

Run with the Python environment that contains the project dependencies:

```bash
python3 server/test_aggregator_smoke.py
python3 server/test_cognitive_smoke.py
python3 runtime/test_supervisor_smoke.py
```

The Grafana provider is included when `GRAFANA_MCP_BINARY` points to an
available binary.

## Repository layout

```text
cognitive/   context assembly and recording
runtime/     service registry and supervisor
server/      MCP entry point and provider adapters
deploy/      generic deployment templates
run_mcp.sh   shared stdio launcher
```
