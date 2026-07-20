# freeplane-tmux

Generate reproducible [`tmuxp`](https://github.com/tmux-python/tmuxp) sessions from a Freeplane mindmap exported through [`metacoma/freeplane_plugin_grpc`](https://github.com/metacoma/freeplane_plugin_grpc).

The compiler treats the map as a small declarative execution language:

- `WINDOW` nodes define tmux windows.
- Window children define implicit command runs or explicit panes.
- Relationships call reusable helper subtrees or inherit another `WINDOW`.
- Templates are resolved with a strict, explicit namespace model.

## Features

- Strict template resolution with helpful undefined-variable errors.
- Explicit global `vars.*` namespace built from the root child `vars`.
- Object-style runtime namespaces: `session.*`, `window.*`, `pane.*`, `node.*`, `env.*`, `args.*`.
- Explicit scoped variables via `var.*`.
- Explicit environment variables via `env.*`.
- Explicit helper arguments via `arg.*` and helper defaults via `default.*`.
- Explicit list values via `LIST` tags or `type: list`.
- Ordered `exec.pre` accumulation across root, window, pane, command, and helper scopes.
- Scoped `ALIAS` declarations with late resolution.
- Automatic alias / environment bootstrap through `ssh` and interactive `sudo` transitions.
- Root `exec.workdir` emitted as tmuxp `start_directory`.
- Root `script1` preserved in map dumps but excluded from template context.

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
python3 -m pip install .
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
--dump
--dump-from-node
--pretty
```

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

### Relationships

A node may reference one or more `target_id` values.

Helper relationship targets are expanded as reusable command subtrees. Variable passing is explicit:

- call-site arguments use `arg.<name>`;
- helper defaults use `default.<name>`;
- helpers read them through `args.<name>`.

There is no implicit merge of arbitrary call-site and helper attributes.

A relationship declared directly on a `WINDOW` node is window inheritance rather than a helper call. The current window inherits target windows in relationship order, merges panes, re-renders inherited panes in the derived-window context, and uses last-wins pane replacement by rendered pane title.

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

The allowed template namespaces are:

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

Leaf values are scalars only when they come from `detail`. A field may also be declared as a leaf child node:

```text
mysql
└── username
    detail: alice
```

That still resolves as:

```jinja
{{ vars.credentials.prod.mysql.username }}
```

Objects cannot be rendered as scalars. `{{ vars.credentials.prod.mysql }}` raises an error listing the available child fields.

### Explicit lists

Lists are explicit. A node becomes a list only when it has the `LIST` tag or `type: list`.

Example:

```text
vars
└── ips [LIST]
    ├── 10.10.0.1
    └── 10.10.0.2
```

Template usage:

```jinja
{{ vars.ips }}
```

When a list is rendered inside a shell command, each item is shell-quoted independently.

### Scoped variables

Scoped variables are explicit and inherit along:

```text
root → window → pane → parent command → current command
```

Declare them with `var.<name>` and read them as flat names:

```text
var.region = eu
```

```jinja
{{ region }}
```

Ordinary attributes do **not** become flat variables.

### Environment

Environment variables are explicit and inherit along the same path as scoped variables.

Declare them with `env.<NAME>`:

```text
env.PROJECT_DIR = /srv/project
env.TOKEN = secret
```

Read them through:

```jinja
{{ env.PROJECT_DIR }}
{{ env.TOKEN }}
```

They are also exported into the generated shell bootstrap.

### Helper arguments

Call-site helper arguments use `arg.<name>`, helper defaults use `default.<name>`, and helpers read them through `args.<name>`.

Example:

```text
connect
  arg.username = {{ vars.credentials.prod.mysql.username }}
  arg.password = {{ vars.credentials.prod.mysql.password }}
  arg.db = jira_cmdb_sam
relationship → mongo-helper

mongo-helper
  default.auth_source = admin
  detail = mongosh 'mongodb://{{ args.username }}:{{ args.password }}@host/?authSource={{ args.auth_source }}&db={{ args.db }}'
```

### Runtime object fields

Runtime namespaces expose object-style data only:

```jinja
{{ session.name }}
{{ session.id }}
{{ window.name }}
{{ window.host }}
{{ pane.name }}
{{ pane.database }}
{{ node.name }}
{{ node.db }}
```

If a pane or session has no stable ID in the current compilation context, no synthetic ID is invented.

### Reference table

```text
Map definition                    Template
----------------------------------------------------------
root/vars/db/prod.user            vars.db.prod.user
window attr host                  window.host
pane attr db                      pane.db
node attr db                      node.db
var.region                        region
env.PROJECT_DIR                   env.PROJECT_DIR
arg.username                      args.username
```

### Removed legacy semantics

The following legacy forms are not supported:

```jinja
{{ root.* }}
{{ window-name }}
{{ pane-name }}
{{ node-name }}
{{ session-name }}
{{ scoped.* }}
{{ .foobar }}
```

Likewise, plain attributes such as `host`, `database`, or `db` are not automatically published as top-level template variables.

## Development

Run the required checks:

```bash
python -m compileall src tests
ruff check .
ruff format --check .
pytest -q
```

`tests/test_tmuxp_integration.py` also runs when `tmux` is available.
