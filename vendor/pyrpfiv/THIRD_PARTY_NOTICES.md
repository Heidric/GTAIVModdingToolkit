# pyrpfiv

This directory contains a vendored and patched copy of `pyrpfiv`.

Original project: `gmroder/pyrpfiv`
Original license: MIT

Local toolkit changes:

- The parser is imported through `core.rpf` instead of from PyPI directly.
- AES key extraction reports richer executable diagnostics.
- AES key extraction checks known offsets first and then scans unknown offsets
  for already-known GTA IV AES keys.
- The parser accepts an explicit AES key for future tooling/configuration.
