# Robots

This directory contains robot implementations managed by the repository.

Current contents:

- `openclaw/`: OpenClaw-based robot subsystems currently managed by the control
  plane.
- `nanobot/`: nanobot implementation grouped under the same robot tree.

Rule of thumb:

- New managed robot implementations should be added under `robots/`.
- Control-plane code should stay outside this directory.
