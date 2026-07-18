# freeplane-tmux

Generate reproducible [`tmuxp`](https://github.com/tmux-python/tmuxp) sessions from a Freeplane mindmap exported through [`metacoma/freeplane_plugin_grpc`](https://github.com/metacoma/freeplane_plugin_grpc).

The project treats the mindmap as a small declarative execution language: `WINDOW` nodes define tmux windows, command trees define pane startup sequences, relationships call reusable command subtrees, and path-scoped attributes provide variables, environment, `pre` commands, and shell aliases.

## Features

- Uses the **map root** as the tmux session root.
- Supports automatic `single implicit pane` and `pane list` window modes.
- Supports relationship calls to both leaf commands and composite function subtrees.
- Resolves relationship targets in the call-site context.
- Provides path-scoped template variables and environment inheritance.
- Accumulates `pre` commands instead of overwriting them.
- Supports scoped `ALIAS` declarations with late template resolution.
- Rebuilds environment and aliases when an interactive command changes shell context through `ssh` or `sudo`.
- Sanitizes Freeplane `detailsText` HTML through one centralized parser.
- Keeps the CLI flags from the original single-file script.

## Requirements

For the standalone release binary:

- Linux x86_64 with glibc compatible with Ubuntu 22.04 or newer
- `tmux`
- Freeplane with `freeplane_plugin_grpc` installed and running
- The generated Freeplane gRPC Python stubs listed below

Python is **not** required for the standalone binary. `tmuxp`, grpcio, Protobuf,
Pydantic, PyYAML, and their Python runtime are bundled into the executable.

For installation from source:

- Linux or another POSIX environment with `tmux`
- Python 3.10+
- Freeplane with `freeplane_plugin_grpc` installed and running
- The generated Python gRPC files from `freeplane_plugin_grpc/grpc/python`:
  - `freeplane_pb2.py`
  - `freeplane_pb2_grpc.py`
- Bash on hosts where alias/bootstrap context is used

When installed from source, `tmuxp`, `grpcio`, Pydantic, Protobuf, and PyYAML are installed as Python dependencies.

## Standalone Linux binary

Every tag matching `v*` runs `.github/workflows/release-binary.yml`. The workflow
builds one `freeplane-tmux-linux-x86_64` executable with PyInstaller, runs the
full tests and a frozen-binary smoke test, then attaches that executable directly
to the corresponding GitHub Release. A manual `workflow_dispatch` run builds the
same downloadable Actions artifact without creating a release.

Install a released binary without Python:

```bash
chmod +x freeplane-tmux-linux-x86_64
sudo install -m 0755 freeplane-tmux-linux-x86_64 /usr/local/bin/freeplane-tmux
freeplane-tmux --help
```

Create a release:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The executable is intentionally Linux x86_64 only, so the release contains one
binary rather than a platform matrix. `tmux` remains an external system
dependency; the Python implementation of tmuxp is embedded and `--load` does not
look for a separate `tmuxp` executable. The project pins the tmuxp 1.74 minor
line because the bundled integration calls its Python CLI directly.

## Installation

```bash
git clone https://github.com/metacoma/freeplane-tmux.git
cd freeplane-tmux
python3 -m pip install .
```

Install the Freeplane plugin from `metacoma/freeplane_plugin_grpc`, then point this tool at its generated Python stubs:

```bash
export FREEPLANE_GRPC_PYTHON_PATH="$HOME/git/freeplane_plugin_grpc/grpc/python"
```

The same directory can be supplied explicitly:

```bash
freeplane-tmux \
  --grpc-stubs-dir "$HOME/git/freeplane_plugin_grpc/grpc/python" \
  --load
```

The tool also searches the current directory, the launcher directory, and common `grpc/python` paths in a source checkout.

## Usage

Generate JSON and tmuxp YAML from the live Freeplane map:

```bash
freeplane-tmux --output-dir ./generated
```

Generate and load the session:

```bash
freeplane-tmux --load
```

Load without attaching:

```bash
freeplane-tmux --load --detached
```

Use a local JSON export instead of gRPC:

```bash
freeplane-tmux \
  --map-json examples/example-map.json \
  --tmuxp-out /tmp/demo.tmuxp.yaml
```

The original script-style entry point remains available from a source checkout:

```bash
python3 freeplane_tmux.py --load
```

## Compatible CLI flags

The refactor preserves:

```text
--addr
--host
--port
--timeout
--output-dir
--json-out
--tmuxp-out
--yaml-out
--load
--detached
--no-groovy-details
```

Additional useful flags are `--map-json` and `--grpc-stubs-dir`.

## Mindmap semantics

### Session and windows

The map root supplies `session_name`. The compiler finds top-level nodes tagged `WINDOW`; a nested `WINDOW` inside another window is not treated as a second session window.

A window becomes one implicit pane when:

- it has a `detail` or `relationship`, or
- all executable children are plain leaf commands.

Otherwise each executable child is a pane root. The behavior can be forced with a window attribute:

```text
window-mode = single-pane
```

or:

```text
window-mode = pane-list
```

### Command nodes

For a normal command node:

1. `detail` is used before `text`.
2. If a relationship exists, its target is expanded after `detail`, or instead of `text` when no `detail` exists.
3. Children continue the command sequence in tree order.

A pane root can itself contain `detail`, a relationship, children, or just a leaf command.

### Relationships

A node may reference at most one `target_id`.

The target may be:

- a leaf function containing one command, or
- a composite function root whose subtree contains multiple commands.

Relationship calls are supported from command nodes, pane roots, and window roots. A window with a relationship and no pane children becomes one implicit pane.

The target root is expanded with call-site builtins and overrides. In particular, `window-name`, `pane-name`, `node-name`, variables, environment, and accumulated `pre` state come from the invocation path rather than the target's storage location. Target-root attributes act as defaults; call-site attributes override them.

### Attributes and late resolution

Attributes are inherited along the actual path from the map root to the command.

- Names matching `^[A-Z_][A-Z0-9_]*$` are environment variables.
- Other names are template variables.
- `pre` is a separate accumulated command channel.
- `ALIAS` nodes define scoped shell aliases.

Templates use `{{ name }}` syntax. Resolution happens at each executable call site, so an ancestor alias or variable may reference a value introduced or overridden further down the path.

An unresolved command, `pre`, or alias at an executable call site is a semantic error. It is never silently emitted with broken placeholders.

### ALIAS nodes

A child tagged `ALIAS` is a declaration, not an executable command.

- Alias name: node `text`
- Alias body: `detail`, otherwise relationship target, otherwise `text`
- Non-alias children continue a composite alias body
- Declarations inherit by path and may be overridden in a descendant scope

When `ssh host` or a `sudo ...` command opens a new interactive shell context, the compiler injects a temporary Bash rcfile containing the effective environment and aliases. Subsequent tmuxp commands therefore continue in the changed context with the same shell declarations.

### Freeplane detailsText

When Groovy lookup is enabled, `detailsText` is fetched for all nodes. HTML tags and entities are normalized centrally before command splitting, preventing closing tags such as `</p>`, `</body>`, or `</html>` from leaking into shell commands.

Use `--no-groovy-details` to rely only on the normal JSON export.

## Architecture

```text
src/freeplane_tmux/
├── models.py       # raw Freeplane model and normalized execution-plan types
├── text.py         # detailsText sanitation and command splitting
├── templates.py    # template rendering and unresolved-key validation
├── scope.py        # vars/env/pre/ALIAS inheritance and late resolution
├── compiler.py     # semantic normalization into SessionSpec
├── shell.py        # shell synchronization and ssh/sudo bootstrap
├── emitter.py      # tmuxp dictionary and YAML output
├── grpc_client.py  # Freeplane RPC and Groovy detailsText enrichment
└── cli.py          # compatible command-line interface
```

The gRPC layer only produces the raw model. The compiler does not know how the map was fetched, and the emitter only consumes the normalized execution plan.

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m compileall -q src tests freeplane_tmux.py
python3 -m pytest -q
ruff check .
```

The tests cover relationship leaf and subtree expansion, window-root relationships, implicit and pane-list modes, path inheritance, `pre` chaining, environment and alias bootstrap across `ssh`/`sudo`, alias late resolution, unresolved alias failures, CLI compatibility, and HTML cleanup.

## Known boundaries

- A relationship is intentionally limited to one target. Multiple targets are rejected instead of being resolved by order-dependent behavior.
- Context bootstrap targets interactive `ssh` calls without an existing remote command and `sudo` shell transitions. An `ssh host some-command` invocation is treated as a self-contained remote command and is left unchanged.
- Alias transport uses Bash because POSIX shells do not provide a portable alias initialization mechanism.
- The generated Freeplane protobuf modules remain owned by `freeplane_plugin_grpc` and are not vendored here.

## License

MIT
