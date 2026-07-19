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

## Requirements

For the standalone release binary:

- Linux x86_64 with glibc compatible with Ubuntu 22.04 or newer
- `tmux`
- Freeplane with `freeplane_plugin_grpc` installed and running
- Python is **not** required for the standalone binary. `tmuxp`, grpcio, Protobuf,
Pydantic, PyYAML, bundled Freeplane gRPC stubs, and their Python runtime are embedded
into the executable.

For installation from source:

- Linux or another POSIX environment with `tmux`
- Python 3.10+
- Freeplane with `freeplane_plugin_grpc` installed and running
- Bash on hosts where alias/bootstrap context is used

When installed from source, `tmuxp`, `grpcio`, Pydantic, Protobuf, PyYAML, and the
bundled Freeplane gRPC stubs are installed as Python package contents.

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

Install the Freeplane plugin from `metacoma/freeplane_plugin_grpc`. No external Python stubs are needed anymore; the project bundles the required gRPC modules into both the wheel and the standalone binary.

## Direct GUI terminal launch from `script1`

`--create` and `--create-map` generate a map-local root `script1`. The script
contains two precomputed argument lists:

1. the complete GUI terminal command;
2. the `freeplane-tmux --load` command, including the selected gRPC address and
   timeout.

When the root script runs, Groovy verifies `DISPLAY`/`WAYLAND_DISPLAY`, checks the
terminal and runtime executables, concatenates both lists, and starts the final
command with `ProcessBuilder.start()`. It does not call a shell launcher, hidden
CLI mode, or an intermediate `freeplane-tmux` process. The terminal starts the
single runtime process that performs export, compilation, YAML emission, and
`tmuxp load`.

`--create-terminal` accepts the complete terminal command:

```bash
freeplane-tmux --create-map Operations --create-terminal "gnome-terminal --"
freeplane-tmux --create-map Operations --create-terminal "xterm -e"
freeplane-tmux --create-map Operations --create-terminal "kitty --"
```

If omitted, the generated script uses:

```bash
x-terminal-emulator -e
```

The Groovy process is launched in the background and writes terminal-startup
errors to `$XDG_RUNTIME_DIR/freeplane-tmux.log` (or `/tmp/freeplane-tmux.log`).

## Create a new Freeplane map

Create a new unsaved map in the running Freeplane instance:

```bash
freeplane-tmux \
  --addr 127.0.0.1:50051 \
  --create-map Operations \
  --create-terminal "gnome-terminal --"
```

`--create Operations` is an exact alias for `--create-map Operations`.

The new map name and root-node text are set to `Operations`. The root receives
the generated `script1`. The starter branch contains `hello-win` tagged
`WINDOW`, with the child command `echo hello world`. Map creation exits without
exporting or loading the session.

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
  --yaml-out /tmp/demo.tmuxp.yaml
```

Current CLI surface:

```text
--create / --create-map
--create-terminal
--addr
--timeout
--output-dir
--json-out
--yaml-out
--load
--detached
--no-groovy-details
--map-json
--pretty
```

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

For a normal command node, execution order is deterministic:

1. its own command (`detail` when present, otherwise `text`),
2. every relationship target in relationship-list order,
3. children in tree order.

Window and pane-root text remains structural: it names the window or pane and is not executed.
A root `detail` is still executable, and root relationships are expanded before root children.

### Relationships

A node may reference one or more `target_id` values. They are expanded in the same order
in which Freeplane exports the relationships.

Each target may be:

- a leaf function containing one command, or
- a composite function root whose subtree contains multiple commands.

Relationship calls are supported from command nodes, pane roots, and window roots. A window with relationships and no pane children becomes one implicit pane.

The target root is expanded with call-site builtins and overrides. In particular, `window-name`, `pane-name`, `node-name`, variables, environment, and accumulated `pre` state come from the invocation path rather than the target's storage location. Target-root attributes act as defaults; call-site attributes override them.

### Attributes and late resolution

Attributes are inherited along the actual path from the map root to the command.

- Names matching `^[A-Z_][A-Z0-9_]*$` are environment variables.
- Other names are template variables.
- `pre` is a separate accumulated command channel.
- `ALIAS` nodes define scoped shell aliases.

Templates use `{{ name }}` syntax. Resolution happens at each executable call site, so an ancestor alias or variable may reference a value introduced or overridden further down the path.

Built-in variables are available in commands, `pre`, aliases, and relationship targets:

- `{{ session-name }}` — rendered root/session name;
- `{{ window-name }}` — rendered current `WINDOW` node name;
- `{{ pane-name }}` — rendered current named pane root, or an empty string for an unnamed/implicit pane;
- `{{ node-name }}` — rendered current command call-site node name.

Jinja expansion also applies to Freeplane node names before tmuxp emission. Session names may use root attributes, window names may additionally use `session-name`, pane names may use `session-name` and `window-name`, and executable node names may use all current builtins. For example, a pane node named `{{ window-name }}` inside window `mcmp2` is emitted with pane title `mcmp2`.

An unresolved command, node name, `pre`, or alias at an executable call site is a semantic error. It is never silently emitted with broken placeholders.

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
├── cli.py          # public CLI and workflow orchestration
├── grpc_client.py  # Freeplane RPC transport
├── groovy.py       # root script1 and starter-map Groovy generation
├── models.py       # raw Freeplane model and normalized execution-plan types
├── text.py         # detailsText sanitation and command splitting
├── templates.py    # template rendering and unresolved-key validation
├── scope.py        # vars/env/pre/ALIAS inheritance and late resolution
├── compiler.py     # semantic normalization into SessionSpec
├── shell.py        # shell synchronization and ssh/sudo bootstrap
├── emitter.py      # tmuxp dictionary and YAML output
└── runtime.py      # tmuxp load and external-process environment boundary
```

The CLI selects a workflow but does not implement Groovy generation or runtime
loading. The gRPC client owns transport only. The compiler is independent of map
acquisition, and the emitter consumes only the validated execution plan.

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m compileall -q src tests packaging
python3 -m pytest -q
ruff check .
ruff format --check .
```

Canonical maps recovered from the project history live in `examples/history/` as paired files:

```text
<name>.map.json
<name>.tmuxp.yaml
```

`tests/test_history_fixtures.py` recompiles every map and compares the complete tmuxp structure and command lists with the committed YAML. `tests/test_tmuxp_integration.py` then invokes real `tmuxp load -d` and inspects tmux to verify window names, pane counts, and rendered pane titles. The live test preserves only the generated OSC title command and replaces executable payloads with `sleep`, so historical SSH, sudo, ping, editor, and monitoring commands are never run by CI.

Run only the real integration suite with:

```bash
REQUIRE_TMUXP_INTEGRATION=1 python3 -m pytest -q -m tmuxp_integration
```

The integration suite requires `tmux` and `tmuxp`. GitHub Actions installs tmux and runs this suite in a dedicated Python 3.12 job.

The tests cover historical map-to-tmuxp compatibility, real tmuxp topology loading, ordered multi-relationship expansion, own-command/relationship/child ordering, relationship leaf and subtree expansion, window-root relationships, OSC pane titles without tmux-version-specific options, implicit and pane-list modes, path inheritance, `pre` chaining, environment and alias bootstrap across `ssh`/`sudo`, alias late resolution, unresolved alias failures, the direct Groovy terminal path, runtime loading, and HTML cleanup.

## Known boundaries

- Relationship order is significant and follows the order exported by Freeplane.
- Context bootstrap targets interactive `ssh` calls without an existing remote command and `sudo` shell transitions. An `ssh host some-command` invocation is treated as a self-contained remote command and is left unchanged.
- Alias transport uses Bash because POSIX shells do not provide a portable alias initialization mechanism.
- The bundled protobuf modules cover only the RPCs used by this project.

## License

MIT


## Bundled gRPC stubs

`freeplane_pb2.py` and `freeplane_pb2_grpc.py` are bundled into the wheel and onefile binary, so runtime access to `metacoma/freeplane_plugin_grpc/grpc/python` is no longer required. The bundled stubs are derived from the upstream `freeplane.proto` definitions and cover the RPCs used by this project (`Groovy` and `MindMapToJSON`).
