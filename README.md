# freeplane-tmux

Generate reproducible [`tmuxp`](https://github.com/tmux-python/tmuxp) sessions from a Freeplane mindmap exported through [`metacoma/freeplane_plugin_grpc`](https://github.com/metacoma/freeplane_plugin_grpc).

Version 0.4 turns the map into a small Freeplane IDE for tmux sessions:

- compile the whole map into tmuxp;
- validate maps without starting tmux;
- explain the resolved execution plan;
- project diagnostics back onto Freeplane nodes;
- run environment checks with `doctor`;
- keep the existing `WINDOW` semantics and current template model.

## Guarantees and non-goals

- The window tag is still exactly `WINDOW`.
- No schema attribute or schema version is required.
- Selected-window / selected-pane execution is not supported.
- `script1` is a service launcher attribute and is not exposed to templates.
- Warnings do not block compilation.
- Errors block `--load` and `validate` returns a failing exit code.

## Features

- Structured diagnostics with severities `error`, `warning`, `info`.
- `freeplane-tmux validate` and `freeplane-tmux validate --json`.
- `freeplane-tmux explain` and `freeplane-tmux explain --json`.
- `freeplane-tmux doctor` and `freeplane-tmux doctor --json`.
- `freeplane-tmux clear-diagnostics`.
- Projection of `tmux-mindmap` diagnostics back to the opened Freeplane map through the existing Groovy RPC.
- Explicit global `vars.*` namespace built from the root child `vars`.
- Runtime namespaces: `session.*`, `window.*`, `pane.*`, `node.*`, `env.*`, `args.*`.
- Explicit scoped variables via `var.*`.
- Explicit environment variables via `env.*`.
- Explicit helper arguments via `arg.*` and helper defaults via `default.*`.
- Explicit list values via `LIST` tags or `type: list`.
- Ordered `exec.pre` accumulation across root, window, pane, command, and helper scopes.
- Scoped `ALIAS` declarations with late resolution.
- Automatic alias / environment bootstrap through supported `ssh` and interactive `sudo` transitions.
- `alias ...` is always followed by `clear` in emitted shell bootstrap.
- Root `exec.workdir` emitted as tmuxp `start_directory`.
- Existing tmux session is killed before a new `--load`; no interactive `Attach? [Y/n]` prompt is used.

## Requirements

For the standalone release binary:

- Linux x86_64 with glibc compatible with Ubuntu 22.04 or newer
- `tmux`
- Freeplane with `freeplane_plugin_grpc` installed and running
- Python is **not** required

For installation from source:

- Linux or another POSIX environment with `tmux`
- Python 3.10+
- Freeplane with `freeplane_plugin_grpc` installed and running
- Bash on hosts where alias/bootstrap context is used

## Installation

```bash
git clone https://github.com/metacoma/freeplane-tmux.git
cd freeplane-tmux
python3 -m pip install .[dev]
```

Install the Freeplane plugin from `metacoma/freeplane_plugin_grpc`.

## Standalone Linux binary

Every tag matching `v*` runs `.github/workflows/release-binary.yml`. The workflow builds one `freeplane-tmux-linux-x86_64` executable with PyInstaller, runs tests and a frozen-binary smoke test, then uploads that executable to the GitHub Release.

Install a released binary without Python:

```bash
chmod +x freeplane-tmux-linux-x86_64
sudo install -m 0755 freeplane-tmux-linux-x86_64 /usr/local/bin/freeplane-tmux
freeplane-tmux --help
```

## Freeplane addon

A minimal installable set of Groovy actions is stored in:

```text
packaging/freeplane-addon/
```

Recommended menu layout:

```text
Tools
└── tmux-mindmap
    ├── Validate map
    ├── Explain map
    ├── Load session
    ├── Clear diagnostics
    └── Doctor
```

The addon keeps `WINDOW` as the window tag and does not add selected-window execution.

## Direct GUI terminal launch from `script1`

`--create` / `--create-map` generate a root `script1` attribute containing the Groovy launcher used by Freeplane. `script1` is a service attribute:

- it stays in the dumped map JSON;
- it is **not** a template variable;
- it is **not** inherited;
- it is **not** published through `vars`, `session`, `window`, `pane`, `node`, `env`, or `args`.

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

Dump the complete live map to stdout without compiling:

```bash
freeplane-tmux --dump --pretty
```

Dump the currently selected node and its descendant subtree as a standalone JSON root:

```bash
freeplane-tmux --dump-from-node --pretty
```

### Validation

```bash
freeplane-tmux validate
freeplane-tmux validate --json
```

Exit codes:

- `0` — no errors, warnings may exist
- `1` — user-facing map errors were found
- `2` — system/runtime problem

### Explain

```bash
freeplane-tmux explain
freeplane-tmux explain --json
```

`explain` does not start tmux. It prints the resolved execution plan with window, pane, command, relationship, and provenance information.

### Doctor

```bash
freeplane-tmux doctor
freeplane-tmux doctor --json
```

`doctor` checks the Freeplane gRPC connection, available Groovy projection capabilities, tmux, tmuxp, terminal command, temporary tmux session creation, launcher log path, and PyInstaller resource paths.

### Clear diagnostics

```bash
freeplane-tmux clear-diagnostics
```

This clears only markers created by `tmux-mindmap`.

## Diagnostics in Freeplane

When `validate` runs against a live Freeplane instance, diagnostics are projected back into the currently opened map through the existing Groovy RPC.

The projector is designed to be idempotent:

- previous `tmux-mindmap` markers are cleared before new ones are applied;
- the first error node is focused when possible;
- user command details are not modified;
- only project-managed diagnostic attributes / status / icons are touched.

If a given Freeplane runtime cannot add icons safely, `doctor` reports that capability and the projector falls back to reversible attributes / status updates.

## Warnings

Current warning codes include:

- `INFERRED_PANE`
- `UNTYPED_RELATIONSHIP`
- `EMPTY_WINDOW`
- `INEFFECTIVE_LAYOUT`
- `UNUSED_HELPER`
- `CONTEXT_PROPAGATION_SKIPPED`

Warnings are machine-readable and do not block compilation.

## Relationship semantics

Relationships are resolved internally as one of two kinds:

- `call`
- `inherit`

Rules:

- relationship to a `WINDOW` node means `inherit`;
- relationship to a helper/function node means `call`;
- if the dump carries an explicit kind (`call` / `inherit`), it is validated and preserved;
- if the dump has no explicit kind, the compiler infers it and emits `UNTYPED_RELATIONSHIP`.

Window inheritance semantics remain unchanged:

- inherited panes are merged in relationship order;
- the referencing `WINDOW` wins on conflicts;
- inheritance cycles are reported as diagnostics.

## Mindmap semantics

### Session and windows

The map root supplies `session_name`. The compiler finds top-level nodes tagged `WINDOW`; a nested `WINDOW` inside another window does not create another tmux session window.

Set `exec.workdir` on the map root to emit tmuxp `start_directory`:

```text
exec.workdir = /srv/project
```

A `WINDOW` body is parsed as an ordered sequence of command runs and pane declarations:

- consecutive plain leaf children are commands in one unnamed implicit pane;
- a child with children, `detail`, relationships, or a `PANE` tag declares a separate pane;
- command runs and declared panes keep their order.

You can force the whole window with:

```text
tmux.mode = single-pane
```

or:

```text
tmux.mode = pane-list
```

Per-window layout can be set with:

```text
tmux.layout = main-horizontal
```

### Command nodes

For a normal command node, execution order is deterministic:

1. its own command (`detail` when present, otherwise executable `text`),
2. every helper relationship target in relationship-list order,
3. children in tree order.

Window text and declared pane-root text remain structural: they name the window or pane and are not executed.

### Service attributes

These attributes control compilation and execution but are not published as template data:

- `script1`
- `exec.pre`
- `exec.pre_mode`
- `exec.workdir`
- `tmux.layout`
- `tmux.mode`

Legacy names such as `pre`, `workdir`, and `window-mode` are not supported.

## Template namespaces

The supported template namespaces are:

- `vars.*`
- `session.*`
- `window.*`
- `pane.*`
- `node.*`
- `env.*`
- `args.*`
- flat scoped variables declared via `var.*`

### `vars.*`

A root child named `vars` is a special global namespace. Each child node becomes one path segment. Attributes on a `vars` node become fields on that object.

Example map:

```text
root
└── vars
    └── credentials
        └── prod
            └── mysql
                attributes:
                  username: alice
                  password: secret
                └── env1
                    attributes:
                      env_name: env1
```

Example templates:

```jinja
{{ vars.credentials.prod.mysql.username }}
{{ vars.credentials.prod.mysql.password }}
{{ vars.credentials.prod.mysql.env1.env_name }}
```

## Development checks

Run the full local validation set:

```bash
python -m compileall -q src tests packaging
ruff check .
ruff format --check .
pytest -q
```
