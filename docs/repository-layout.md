# Repository Layout

This repository currently mixes three kinds of content: active product code,
supporting assets, and historical/reference material. The goal of this file is
to make those boundaries explicit.

## Active product path

These directories define the current Hub runtime and managed robot set and should be treated as
the main delivery path:

- `hub/`: current control-plane service, static web UI, and API
  implementation.
- `robots/openclaw/`: current OpenClaw-based managed robot implementations.
- `robots/nanobot/`: current nanobot code kept under the unified robot tree.
- `scripts/`: bootstrap, run, stop, package, and verification entrypoints for
  the Hub stack.
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

## Hub frontend structure

The Hub frontend is currently shipped as one SPA, but its source is now
organized for a future split into robot-specific sites:

- `hub/frontend/src/shell/`: Hub shell, app router, and shared navigation.
- `hub/frontend/src/platform/`: cross-app platform concerns such as auth,
  UCAN-adjacent session wiring, API clients, shared types, and query setup.
- `hub/frontend/src/apps/`: robot-facing application modules. Each robot app
  should keep its own screens and business UI here.
- `hub/frontend/dist/`: built frontend artifacts served by the Python backend.
  This directory is runtime/package output and should not be committed as source.

Current rule of thumb:

1. Add shared control-plane concerns to `platform/`.
2. Add global layout and routing concerns to `shell/`.
3. Add robot-specific UI to `apps/<robot>/`.
4. Keep new robot apps separable so they can later evolve into independent
   sites without rewriting platform and shell concerns first.
