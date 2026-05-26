# Plugins

Drop MathCode plugins here. Each plugin is a folder with a `.mathcode-plugin/plugin.json` manifest.

## Quick Start

```
plugins/
  my-plugin/
    .mathcode-plugin/
      plugin.json       # Required: name, version, description
    commands/            # Optional: slash commands (.md files)
    skills/              # Optional: skills (dirs with SKILL.md)
    agents/              # Optional: agent definitions (.md)
    hooks/hooks.json     # Optional: lifecycle hooks
    .mcp.json            # Optional: MCP tool servers
```

## Minimal plugin.json

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "What my plugin does"
}
```

## Loading

Plugins in this folder are loaded when MathCode starts with:

```bash
./bin/mathcode --plugin-dir plugins/my-plugin
```

Or install from a Git repo via the `/plugin` command inside MathCode.

## What Plugins Can Provide

- **Commands** — `.md` files invoked as `/plugin-name:command`
- **Skills** — reusable prompt templates (dirs with `SKILL.md`)
- **Agents** — custom agent definitions
- **MCP Servers** — external tool providers
- **Hooks** — intercept CLI lifecycle events
- **LSP Servers** — language server integrations
- **User Config** — declarative settings with secure storage

## Examples

- Proving strategy plugin: custom tactics and decomposition patterns
- Model backend plugin: route LLM calls through a local server
- Domain knowledge plugin: curated lemma databases for specific math areas
