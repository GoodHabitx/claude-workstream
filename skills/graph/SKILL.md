---
name: graph
description: Regenerate the mermaid graph of the whole workstream tree — lineage, reporting, collaboration, succession, and rename history — from every manifest, via scripts/views.py regen. A GENERATED artifact, overwritten each run; lands at <state_root>/workstream-graph.md. Trigger on "workstream graph", "graph the workstreams", "show the workstream tree", "/workstream:graph". Do NOT use to roll-call sessions (workstream:list), adopt (workstream:adopt), or reconcile forks (workstream:connect).
---

# Workstream graph

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Regenerate a mermaid graph of the whole workstream tree — a
**generated** artifact (never hand-edited), overwritten each run.
Produced entirely from manifests (`spawned_from`, `direct_report`,
`collaborate`, `absorbed_by`, `previous_names` — every cross-manifest
edge resolved by its immutable session half; the legacy `parents` axis
is retired and never read), so it is independent of the `ws-`/`cos:`
title binding format and of any vault project/spine data, and works
even without the session-mgmt MCP.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## `/workstream:graph [--root <path>]`

1. **Invoke the generator.** `python3 scripts/views.py regen [--root
   <path>]` — scans every `workstream.json` under the state root,
   resolves every edge by its immutable session half, and writes both
   `<state_root>/index.md` and `<state_root>/workstream-graph.md`
   (views.py regenerates both together — there is no graph-only mode
   in the core; this skill's job is narrower than the whole regen but
   the underlying write is not separable). There is nothing left for
   this skill to hand-draw.

2. **Report** what the generator returned: `manifests`/`orphans`/
   `problems` counts and the two output paths (`index_path`,
   `graph_path`). On a nonzero exit (no `staff/` dir at the target
   root — the core's own refusal), surface the script's own error
   rather than guessing at a fix; this skill does not re-implement or
   patch over generator failures.

## What the graph draws

One node per manifest (styled by `state`: active / closed ✓ / absorbed
— dimmed/terminal), plus one dimmed, terminal ghost node per
`previous_names` entry, joined by five edge types:

- **`spawned_from`** (solid) — "forked from": immutable fork provenance.
- **`direct_report`** (thick) — "reports to": the single reporting
  up-link, drawn as its own edge even when it points at the same node
  as `spawned_from` — the two axes are independent.
- **`collaborate`** (dashed) — "collaborates with": one edge per
  `collaborate[]` entry, carrying the scope in the label when declared.
  A reciprocated collaboration shows as two edges — each side declares
  its own.
- **`absorbed_by`** (dotted) — succession: from the absorbed node to
  its overtaker.
- **renamed** (dotted) — from each `previous_names` ghost node to the
  current node.

The retired `parents` "belongs to" edge is gone — a workstream reports
to at most one node, so the reporting graph is a tree.

## Discipline

A generated view — never hand-edit; re-run to refresh. `views.py`
reads manifests only — never titles, never a vault node. Draws every
workstream that has a manifest; there is nothing else to filter in
this model, since workstreams no longer live among plain vault
projects.
