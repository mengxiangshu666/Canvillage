# Archify upstream snapshot

This directory stores the complete Archify `v2.16.0` runtime package used for
source-level study and compatibility work in Village Infinite Canvas.

- Upstream: https://github.com/tt-a1i/archify
- Pinned commit: `39a21139a4661203888049d44e3b8c0da13fa576`
- License: MIT; see `2.16.0/LICENSE`
- Provenance: `2.16.0/UPSTREAM_PROVENANCE.json`

The upstream snapshot is kept intact. Village-specific adapters belong under
`src/novelvideo/architecture/` or `scripts/architecture/`; do not edit files
inside the versioned snapshot directly.

The default Windows release package excludes `third_party/` unless an explicit
architecture-audit distribution profile is added.
