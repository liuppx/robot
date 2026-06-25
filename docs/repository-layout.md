# Repository Layout

This repository currently mixes three kinds of content: active product code,
supporting assets, and historical/reference material. The goal of this file is
to make those boundaries explicit.

## Active product path

These directories define the current Bot Hub runtime and managed robot set and should be treated as
the main delivery path:

- `rust/control-plane/`: current control-plane service, static web UI, and API
  implementation.
- `robots/openclaw/`: current OpenClaw-based managed robot implementations.
- `robots/nanobot/`: current nanobot code kept under the unified robot tree.
- `scripts/`: bootstrap, run, stop, package, and verification entrypoints for
  the Bot Hub stack.
- `config/`: runtime configuration templates used by the active stack.
- `docs/`: current product and runbook documentation.

## Active support directories

These directories are still useful to the current repository, but they are not
the primary production entrypoint:

- `workspace/`: workspace templates used for bot instances.
- `data/`: sample or domain data used by templates and experiments.
- `ops/`: operational helper scripts and sidecar workflows.
- `examples/`: example prompt or scenario material.

## Historical reference material

- `docs/archive/`: archived documentation that remains useful as background, but
  should not be treated as the current source of truth.

## Practical rules

1. New production code should default to the active product path, not to a new
   top-level directory.
2. New robot implementations should go under `robots/`.
3. New docs should go under `docs/`, and legacy docs should be moved to
   `docs/archive/` once they are no longer current.
4. If a directory exists mainly for migration support, document that status in
   its local `README.md`.
