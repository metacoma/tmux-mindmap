# tmux-mindmap Freeplane actions

This directory contains installable Groovy actions for Freeplane 1.12+.

Recommended menu layout:

Tools
└── tmux-mindmap
    ├── Validate map
    ├── Explain map
    ├── Load session
    ├── Clear diagnostics
    └── Doctor

The scripts call the public `freeplane-tmux` CLI and keep `WINDOW` as the window tag.
They do not implement selected-window or selected-pane execution.
