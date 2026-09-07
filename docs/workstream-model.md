<!-- Shared model for the workstream plugin's skill suite (adopt, fork, refocus,
     close, absorb, notify, list, graph, connect, manifest, policy, sidecar).
     One source of truth so the suite never drifts. This is shipped expertise
     (spec 3.6): a plugin doc under ${CLAUDE_PLUGIN_ROOT}/docs/, NOT consuming-
     vault content.

     Ported from cos 0.20.0's docs/workstream-model.md (2026-09-06/07),
     updated for the standalone-plugin target: paths are config-driven
     (workstream_lib.state_root()/sessions_root()), the namespace is
     /workstream:<verb> (harness-auto-namespaced, no hand-rolled prefix),
     spawn and rebind are RETIRED (L2, L3 - the sidecar primitive's
     self-heal covers rebind's one real case), continuity (hot/log/policy/
     index) is Ballast's scope, not this plugin's own hooks, and the
     manifest schema gains absorbed[]/maintains[]/scope while dropping
     parents[]/parent/parent_session/rebound[] (I4). Skills themselves are
     a separate build (see README.md) - this doc is their shared contract. -->

# The workstream model

**One session-chain per parallel workstream** — named, uuid-anchored, and
self-contained — so a session has durable awareness of its own job and of
everything running in parallel, and can grow, connect, and retire those
chains.

**A workstream is not a project.** A vault project may have zero or many
workstreams pursuing it; one workstream may touch any number of vault
projects. These skills never read or write a vault project/spine node,
never validate a workstream's name against a spine/rollup, and never move
a wiki folder-note. The workstream's own **manifest** — not any vault node
— is the source of truth for what it is. The only bridge to the consuming
vault is the manifest's informational `projects` list (below); what the
vault does with its own project graph is entirely out of scope for this
suite.

The suite is split into focused skills so each triggers and behaves
reliably; they share this model rather than repeating it. **Twelve skills,
under this plugin's own namespace** (`/workstream:<verb>`):

- `adopt` — turn THIS session into a workstream (birth a new chain). The
  bare/default entry.
- `list` — roll-call every workstream session.
- `fork` — birth a fork. Run INSIDE the spawned session after native
  `/fork`: self-name, write the birth manifest, ASK whether to
  direct-report to the parent and/or collaborate with it (+scope) —
  **default neither** — and notify the parent.
- `connect` — the general reconciliation **audit**: scan all workstreams,
  check the invariants (below), and recruit `manifest`/`policy`/`fork` —
  in the current or the target workstream — to fix what it finds.
  Proposes and confirms each fix; escalates a genuinely ambiguous one to
  `grill:grilling`.
- `refocus` — refocus-with-grilling: interrogate focus, name, links and
  policy, then apply the answers (including the front-facing rename). **No
  re-parenting** — lineage is immutable and links move through `manifest`.
- `close` — mark a workstream done.
- `absorb` — end a workstream by folding it into another that overtook it
  (`absorbed`, not `done`).
- `notify` — hand a message to another workstream session.
- `graph` — regenerate the mermaid graph of the whole workstream tree
  (provenance, reporting, collaboration, succession, rename history) via
  `scripts/views.py`.
- `manifest` — the manifest **primitive**: schema authority for
  `workstream.json`, correct field writes, and verification that the boot
  render and the artifacts came out as specified. Bare = show/validate this
  workstream's own manifest; with args = set a field. Backed by
  `scripts/manifest.py`.
- `policy` — add, edit, or remove a durable standing policy on a
  workstream's `policy.md` (Ballast's file — `## Maintains` no longer
  lives here, see "Policy layer," below).
- `sidecar` — the self-healing binding primitive. Backed by
  `scripts/sidecar.py`.

**Retired from cos 0.20.0's six-verb set (L2, L3):** `spawn` (never gave a
capability anyone used — a deliberate parallel session on the same
workstream was never wanted) and `rebind` (never requested/used/
imaginable; its one adjacent real need — a missing/stale sidecar — is now
mechanical self-heal, owned by the `sidecar` primitive, not a
principal-confirmed ritual). `rebound[]` is dropped from the manifest
schema; a workstream that needs a new session simply resumes, forks, or
runs `sidecar self-heal`.

## A workstream IS its session chain

A workstream is not one session — it's a **chain of sessions**, threaded
by resume and fork, ending in close or absorb. Session identity has three
layers; only one is durable:

| Layer | What it is | Stability |
|---|---|---|
| **Title** | `ws-<name>` — human-facing, mutable by design | Changes on rename/refocus, or by accident — a VIEW, not an identity |
| **Transcript id** (`session_id`) | what `boot.py` reads off `SessionStart` stdin, echoed as `<!-- workstream-session-id: ... -->`; the sidecar's on-disk filename | **Rotates** across resume/compaction — never durable |
| **Sidebar UUID** | what session-mgmt's `get_session("self")` / `list_sessions` returns as `sessionId`, shape `local_<uuid>` | Minted at session creation, **fixed for life** — the immutable fingerprint |

The sidebar UUID is the one durable identifier. It is stored everywhere as
the **bare uuid** — strip the `local_` prefix before writing it anywhere
(that prefix is an MCP addressing convention, not identity).

- **`born_session: <bare-uuid>`** — every workstream manifest carries this:
  the sidebar uuid of the chain's ROOT session, written once at birth,
  **never edited** afterward — not by a rename, not by a self-heal. A
  workstream **comes into existence** at the moment its manifest is
  written; no manifest ⇒ no workstream. The manifest lives at
  `<state_root>/<born_session>/workstream.json` (`<state_root>` =
  `workstream_lib.state_root()`, config-driven, default
  `staff/cos/workstreams`) — the dir name IS the durable key (see
  "Storage," below).
- **The chain extends** by **resume** (same session, same sidebar uuid —
  nothing new to record) or **fork** (a new session, new sidebar uuid,
  context-carrying from the parent — recorded via the dual-field edge; see
  the graph section, below).
- **The chain ends** by **close** (`state: closed`) or **absorb**
  (`state: absorbed`) — a session merely closing is not a chain event.

### One birth, one self-heal

- **adopt** — promote THIS ordinary conversation into a brand-new chain.
  This is birth proper: it mints `born_session` from THIS session's own
  sidebar uuid (`get_session("self")`), writes the manifest via
  `manifest.py create`, and picks a free `name`. **Adopt refuses when a
  manifest with that name already exists** — you cannot birth what's
  already alive — and **refuses outright if `ballast` is not installed**
  (`workstream_lib.adopt_precheck()` — identity without continuity is the
  defect this rebuild exists to fix, not a feature to ship anyway).
- **`sidecar self-heal`** — the mechanical, non-principal-confirmed fix
  for a missing or stale sidecar (the one real case `rebind` used to
  serve): given a session id and the correct `born_session` (read off a
  title match, a manifest scan, or `connect`'s own audit), rewrites the
  sidecar and returns a LOUD note describing what changed — never silent
  (I5's target, replacing "not a failure" for a missing sidecar).

## The title, and the two session-id namespaces

The human-facing **title**, canonical shape **`ws-<name>`**, is a VIEW of
a workstream, not its durable identity (that's `born_session`, above). A
workstream session ⇔ its title matches **`^ws-`**, with **`^cos:`
accepted permanently as legacy** (the prior `cos:<name> — <focus>` shape,
from before this plugin existed). `ws-` is canonical for every new/renamed
session; `cos:` is tolerated so a session that hasn't migrated yet is
never dropped.

**`ws-` prefixes the TITLE only.** The workstream's **name** is the graph
key — `spawned_from`, `absorbed_by`, and every cross-manifest pointer name
it by this string — but the name is **freely renameable** (see "Names are
freely renameable," below); nothing locks it. The name is **recoverable
from either title form**:

- new form `ws-<name>` → strip the leading `ws-`;
- legacy form `cos:<name> — <focus>` → the token between `cos:` and the
  first ` — ` (or everything after `cos:` if there is no ` — `);
- either form may carry a trailing ` (fork)` — strip that first (see
  reconciliation).

The human **focus** phrase is **retired from the title** — never in the
binding. Its durable home is the manifest's `focus` field (a one-line
mission); the sidecar's `focus` key mirrors it within-run. It is a
cosmetic label — no graph tooling reads it (the graph rides
`spawned_from`/`direct_report`/`collaborate`/`absorbed_by`). Two
**session-id namespaces** are in play, not interchangeable:

- the **session-mgmt MCP id** (`local_<uuid>`) — what the session-mgmt MCP
  tools address a session by;
- the **transcript/hook `session_id`** (a bare uuid) — what
  `scripts/boot.py` reads off SessionStart stdin and echoes into context
  as `<!-- workstream-session-id: ... -->`, and what the on-disk sidecar
  filename uses. The MCP id is *usually* `local_` + this bare uuid, but the
  two can diverge across a restart/resume — so read both from their
  sources (`get_session("self")` + boot's own echo line), never derive one
  from the other.

Neither of these is the sidebar UUID used as `born_session` — read that
from `get_session("self")`'s `sessionId` (bare, prefix stripped), not from
the transcript line.

## Session-mgmt tooling (soft dependency — R3, see docs/dependencies.md)

The skills call Claude Code's session-management MCP:
`mcp__ccd_session_mgmt__list_sessions`, `mcp__ccd_session_mgmt__get_session`
(with `"self"`), `mcp__ccd_session_mgmt__set_session_title`,
`mcp__ccd_session_mgmt__send_message`, and
`mcp__ccd_session_mgmt__archive_session` (used by `close`/`absorb` only
when the principal confirms archiving a session — it always prompts). If
that MCP is unavailable, degrade gracefully and say so: read-only verbs
report "session index unavailable"; a bind writes the sidecar with
`mcp_session_id: null` and `title: null` (never a title it did not
actually apply — an uncorroborable title is a lie), ensures the
manifest/caches, and asks the principal to rename by hand.

## The graph — lineage, reporting, collaboration, and succession

Workstreams form a graph, recorded durably in **each workstream's own
manifest** (git-tracked; no parallel registry, no vault node). Every edge
is **dual-field** — a readable name half (a display cache, only ever
OPTIONALLY updated by a rename's cosmetic ripple) paired with an immutable
session half (written once, never edited by a rename). Four axes:

- **`spawned_from: <parent-name>` / `spawned_from_session: <bare-uuid>` —
  LINEAGE.** Immutable **fork provenance**. **Exactly one or none** —
  absent/`null` = a hand-started root. Never rewritten after minting.
  Lineage **may point at a since-closed workstream, and that is fine**.
- **`direct_report: {"name": <name>, "session": <born_session-uuid>}` |
  `null` — REPORTING.** A **single** object or `null`: who this workstream
  reports to. Reports travel UP the direct-report graph, hop by hop. A
  report-target **stores nothing**; its inbound reports are DERIVED by
  scanning for manifests whose `direct_report.session` equals its
  `born_session`.
- **`collaborate: [{"name": <name>, "session": <born_session-uuid>,
  "scope": <freeform>}, ...]` — COLLABORATION.** A **list**: bidirectional
  peer coordination, carrying **no authority**. Declaring a collaboration
  **obliges reciprocity** — the declaring side messages the counterpart,
  and the counterpart writes its own mirror entry in its OWN manifest
  (single-writer; `manifest.py collaborate-add` on the OTHER manifest).
- **`absorbed_by: <overtaker-name>` / `absorbed_by_session: <bare-uuid>`**
  — the **succession axis**, present only when `state: absorbed`.

**NEW in this target schema (I4, AB3): `absorbed[]` on the OVERTAKER's own
manifest** — `[{"name", "born_session", "dir", "transcript"}, ...]`, the
determinism anchor an overtaker uses to find what it swallowed WITHOUT a
scan (unlike cos 0.20.0, where absorption-tracking was fully derived by
scanning every manifest for `absorbed_by_session`). Written only via
`manifest.py append-absorbed`, on the overtaker's OWN manifest — additive,
idempotent on a duplicate `born_session`.

**Edge-resolution invariant.** Every cross-manifest edge above —
`spawned_from`, `direct_report`, each `collaborate[]` entry, `absorbed_by`
— is resolved by its **immutable session half**:
`workstream_lib.resolve_ref_display()` finds the manifest whose
`born_session` matches the uuid, then reads ITS current `name`. The name
half stored alongside each edge is a **display cache**, re-derivable at
any time — never the thing actually resolved; a dangling `session` draws
an `[unresolved]` stub, exactly as for the single axes. Because of this, a
rename can never dangle an edge. `scripts/views.py` and the `list` skill
both implement exactly this resolution via the ONE shared function in
`workstream_lib.py` — never a second, drifting scan.

**RETIRED (I4, L1): `parents`, the legacy `parent`/`parent_session`, and
`rebound`.** No reader and no writer in this suite touches them. Frozen
`closed`/`absorbed` manifests may still carry them from before this
migration; they are simply ignored. `workstream_lib.find_problems()`
flags a legacy field found on an ACTIVE manifest (frozen closed/absorbed
manifests are exempt).

Everything else is **derived, never duplicated** — by scanning every
manifest under `<state_root>/*/workstream.json`:

- **Fork-children** = every manifest whose `spawned_from_session` equals
  its `born_session`.
- **Direct reports** = every manifest whose `direct_report.session` equals
  its `born_session`.
- **Collaborators** = every manifest carrying a `collaborate[]` entry
  whose `session` equals its `born_session`. Reciprocal by construction —
  a disagreement is a one-sided collaboration to flag (`connect`'s
  Inv-4), never a second registry to consult.
- **Status** = the manifest's `state` (`active` → `closed` = "satisfied,"
  or `absorbed` = "superseded").
- **`spawned_at`/`absorbed_at` are not manifest fields** — these files are
  git-tracked, so "when" is recoverable from git history.

The graph artifact (`scripts/views.py`'s `render_graph`) draws **five
edge types**: a solid `spawned_from` edge ("forked from"); a bold
`direct_report` edge ("reports to"), drawn as its **own** edge even when
it points at the same node as `spawned_from`; a dashed `collaborate` edge
per entry (scope in the label); a dotted `absorbed_by` edge; and a thin
dotted **renamed-ghost** edge, one per `previous_names` entry. Overwritten
in place each run (V2: at the end of every verb/connect that writes a
manifest, and on demand); never hand-edited.

## Absorption — a workstream ends by being superseded (AB1-AB7)

Most workstreams end by finishing (`close`, `state: closed`). Some end a
different way: the work didn't finish, it **moved** — a child, a sibling,
or an unrelated workstream organically overtook the mission, and the
honest closing state is "superseded," not "satisfied." That's absorption
(`absorb`), redesigned this build to fix a real observed failure (AB1: an
overtaker that knew NOTHING about what it absorbed, since the old carry
was a lossy judgment copy with no durable pointer).

1. **Grab** (deterministic, AB2): append one absorption-record line to the
   overtaker's own log; fold the stale hot STATE BLOCK into the
   overtaker's — itemized add/update/delete per slot, never a whole-file
   re-summary; rewrite the stale hot.md to a short tombstone naming its
   successor.
2. **Point** (AB3): `manifest.py append-absorbed` on the OVERTAKER's own
   manifest — the determinism anchor.
3. **Carry** (AB4): `focus` updated to own the mission; `projects[]` =
   union; policy + `maintains[]` folded in; dir artifacts stay, nothing
   deleted. A policy/`maintains[]` collision escalates to `grill:grilling`
   (AB5) — never a silent overwrite.
4. **Close the stale manifest** (AB7) — `manifest.py absorb-close`: the
   ONE sanctioned cross-manifest write, `state: absorbed` +
   `absorbed_by`/`absorbed_by_session`, vault-lock-wrapped when available.
5. **Re-home edges** — every manifest whose `direct_report.session` equals
   the stale node's `born_session` re-homes its up-link one level (to the
   stale node's OWN `direct_report`, or `null`); dead `collaborate[]`
   entries pointing at the stale node are dropped.
6. **Notify unconditionally** (AB6) — stale, overtaker, every
   fork-descendant, regardless of measured `isRunning` reliability; a
   failed notify never blocks the absorption (the manifest write already
   stands). `log.md` is NEVER merged — it stays where it happened, as
   provenance.
7. **Offer to archive** the stale session to the principal — confirmed,
   never automatic.

**Conventions:** absorbed workstreams stay in place, visible in the
generated index/graph, marked `absorbed` — never reopened by hand-editing
`state` back to `active`; a genuinely-revived mission is a NEW workstream
(possibly `spawned_from` the absorbed one).

## Reporting and collaboration — behavior

The two relationship axes are **opt-in and asked for, never assumed**.
`fork` asks at birth — direct-report to the parent? collaborate with it,
and over what scope? — and the **default is NEITHER**. A workstream has
**at most one `direct_report`**, so the reporting graph stays a tree.

**The authority model.**

- The **principal's directives, relayed DOWN the report chain, are
  authoritative** — they **override** a subordinate's contradicting prior
  direction from the principal.
- A report-target's **OWN** directive to a subordinate is authoritative
  when it **agrees with or complements** that subordinate's current
  direction.
- When a report-target's own directive **CONTRADICTS the principal's
  direction to that subordinate**, the subordinate **DEBATES**: it
  surfaces the conflict rather than resolving it silently.
- **The principal is always ultimate.**

**Hop-by-hop relay.** Reports travel up the direct-report graph one hop
at a time; each hop digests what it received and passes up what matters
through its OWN up-link. **Never raw-multicast to every ancestor.**

**Re-home on close/absorb.** When a report-target closes or is absorbed,
every workstream reporting to it re-homes its up-link one level —
announced, never silent.

**Reciprocity is collaborate-only.** `direct_report` is one-sided by
design and the target stores nothing.

**Post-birth link changes go through `manifest.py`.** Adding, removing,
or re-pointing a `direct_report` or a `collaborate` entry after birth is a
manifest write, routed through the manifest primitive — including a
reciprocity notify and a re-home.

## Names are freely renameable — the uuid anchors identity

A workstream's **name** is how `spawned_from`/`direct_report`/
`collaborate`/`absorbed_by` name halves point at it, and what the title
(`ws-<name>`) displays. The name is never locked — because the uuid-keyed
dir and every session-half field anchor identity independent of the name,
a rename costs nothing beyond updating this workstream's own manifest:

- `refocus` may rename a workstream at any time. **Before** overwriting
  `name`, it appends the outgoing name to `previous_names` as
  `{"name": "<old-name>", "renamed_at": "<ISO date>"}` — append-only,
  never edited or trimmed. It then changes this workstream's OWN manifest
  `name` field and retitles the session — that is the **entire mandatory
  rename**.
- **Every stored name-half is a DISPLAY CACHE, not a source of truth** —
  re-derivable at any time via the edge-resolution invariant, and every
  generated view already does exactly this.
- **The cross-manifest ripple is OPTIONAL cosmetic tidiness, never a
  required step.**
- **The graph renders `previous_names` as ghost nodes.**
- The **uuid-keyed cache dir never moves** — it was never keyed by name.
- The `focus` field is always freely editable.

## Native `/fork` and reconciliation

Sessions are created by the human, not the assistant: the platform
reserves `/fork` (and a session's own first keystroke) to the user.

1. In a workstream session, the principal types native `/fork` — Claude
   Code copies the conversation into a new parallel, persistent, GUI-listed
   session, titled like the parent with **`(fork)`** appended.
2. **`fork`, run INSIDE the spawned session**, births the child: self-names
   from its own context, mints the child's OWN manifest (new
   `born_session`) with `spawned_from`/`spawned_from_session` = the
   parent's, asks direct-report/collaborate (**default NEITHER**),
   retitles the child, notifies the parent.
3. An unreconciled fork — a session still carrying `(fork)` with no
   manifest — is one of `connect`'s audit invariants; connect does not
   birth it, it **notifies that session to run `fork`** itself.

## Storage: the manifest, the caches, and the index

Everything about a workstream lives under its own uuid-keyed dir — no
wiki node, no vault-side storage at all.

- `<state_root>/<born_session>/workstream.json` — **the manifest,
  durable, git-tracked**: the record of truth. See the schema below.
- `<state_root>/<born_session>/hot.md` / `log.md` / `policy.md` /
  `index.md` — **Ballast's four classes**, per its own scope declaration
  (`<state_root>/<born_session>/ballast.json`, auto-created by
  `ballast-dispatch.py` on first use — see "Policy layer," below, and
  `docs/dependencies.md`). This plugin declares the scope; Ballast keeps
  the files fresh.
- `<state_root>/<born_session>/tasks.md` — **not part of this suite**
  (X9: no tasks artifact — a workstream wanting a task list adds a
  `/work` project or tracker to `maintains[]` instead).
- `<state_root>/index.md` — **generated, git-tracked**: the human-facing
  lookup table, regenerated by `scripts/views.py`.
- `<state_root>/workstream-graph.md` — **generated, git-tracked**: the
  mermaid graph, written by the same `scripts/views.py`.
- the sessions dir (`workstream_lib.sessions_root()`, the state root's own
  sibling, default `staff/cos/workstream-sessions`) —
  `<session_id>.json` — **ephemeral, gitignored**: the sidecar keyed by
  the volatile transcript id. A within-run convenience, self-healing, not
  content.

**Path resolution rule everywhere**: name → resolve `born_session` (from
this session's own sidecar if bound, else by scanning manifests for a
`name` match) → `<state_root>/<born_session>/`. There is no legacy
vault-node fallback — a workstream with no manifest simply isn't a
workstream yet.

## Boot, remind, precompact (this plugin's OWN hooks — X2-X4, V1)

Three hooks, deterministic, no model judgment in whether they fire:

- **`scripts/boot.py`** (`SessionStart`, no matcher — fires on every
  source including compact) — the plugin's own session-id echo line
  (`<!-- workstream-session-id: ... -->`), then, when bound, the ~1 KB
  identity block read straight from the manifest (name, focus, state,
  direct_report, collaborate[], maintains[], absorbed[] pointers, file
  paths). Also resets the remind turn-counter to 0 on every fire, and
  warns once (soft-dependency degrade) when vault-lock is not
  materialized in this vault.
- **`scripts/remind.py`** (`UserPromptSubmit`) — re-shows the identity
  block every N=25 turns (a LOCAL default from this vault's own
  turn-gap census, not model-validated — see the hub page's Open note),
  deduped via the boot-stamped counter. Fires on the SAME slot as
  Ballast's own re-ground, which must run FIRST (hooks.json ordering).
- **`scripts/precompact.py`** (`PreCompact`) — a short ~0.3 KB note
  (name, focus, state, "preserve in-flight work") so the compaction
  SUMMARY itself preserves identity — distinct from `boot.py`'s own
  re-injection on the SessionStart that follows compaction.

The manifest schema (target, this build):
```json
{
  "name": "<semantic-name>",
  "state": "active | closed | absorbed",
  "focus": "<one-line mission>",
  "created": "<ISO date>",
  "born_session": "<bare-uuid>",
  "spawned_from": "<name or null>",
  "spawned_from_session": "<bare-uuid or null>",
  "direct_report": {"name": "<name>", "session": "<bare-uuid>"} | null,
  "collaborate": [{"name": "<name>", "session": "<bare-uuid>", "scope": "<freeform>"}],
  "absorbed_by": "<name>",
  "absorbed_by_session": "<bare-uuid>",
  "absorbed": [{"name": "<name>", "born_session": "<bare-uuid>", "dir": "<path>", "transcript": "<path>"}],
  "previous_names": [{"name": "<old-name>", "renamed_at": "<ISO date>"}],
  "refocused": [{"date": "<ISO date>", "from_focus": "<text>", "to_focus": "<text>", "note": "<text>"}],
  "maintains": ["<vault [[wikilink]] or absolute path>"],
  "projects": ["<consuming-vault project ids>"]
}
```
`absorbed_by`/`absorbed_by_session` appear only when `state: absorbed`.
`absorbed[]` (NEW, AB3) lives only on an OVERTAKER's manifest — empty for
a workstream that has absorbed nothing. `maintains[]` (NEW, I6) lives in
the manifest, not `policy.md` — the boot command can inject it directly.
**Dropped from this schema going forward** (I4): `parents`/`parent`/
`parent_session` (multi-parent retired), `rebound` (rebind retired, L3).
`born_session` is duplicated inside the manifest, in addition to being the
dir name, so the file is self-describing if ever read out of directory
context.

The sidecar schema:
```json
{
  "session_id": "<transcript session_id>",
  "born_session": "<bare-uuid>",
  "name": "<optional workstream name>",
  "focus": "<optional human focus>",
  "written_at": "<ISO-8601 UTC>"
}
```

## Policy layer (Ballast's file — I6)

`policy.md` is Ballast's file, injected whole (≤7 KB, approval-gated,
B4) at the same cadence as its hot state block. **`## Maintains` no
longer lives in `policy.md`** (I6, the target build edit from cos 0.20.0's
shape) — a duty list is data, so it moved to the manifest's `maintains[]`
field, where the boot command can inject it directly beside the identity
block. `policy.md` now carries only `## Policies` — free-form durable
strategies/behaviors, written via the `policy` skill.

`maintains[]` discharges two ways (X8): a vault-domain path → dispatch
that domain's plugin; an outside-vault path → the workstream self-
maintains it in-session. Either way, two logs get the entry (the domain's
and the workstream's own).

## The connect audit — invariants (V4, adapted for absorbed[])

`connect` scans every workstream and checks a **bounded** list — no
open-ended detection. Implemented once, shared, in
`workstream_lib.find_problems()`:

1. Schema: `direct_report` object/null, `collaborate` a list, `absorbed[]`
   shape (each entry a dict with a usable name or born_session half).
2. `spawned_from_session` resolves to a real manifest, or is null.
3. `direct_report` resolves, and its target is not closed/absorbed (else
   flag re-home).
4. Every `collaborate` entry resolves AND the counterpart reciprocates
   (else flag one-sided).
5. No ACTIVE manifest still carries a legacy `parents`/`parent`/
   `parent_session`/`rebound` field (frozen closed/absorbed manifests are
   exempt).
6. **Self-heal** (renamed from cos 0.20.0's Inv-6): every live session's
   sidecar ↔ a real manifest, MECHANICALLY fixed via `sidecar.py
   self-heal`, not just flagged.
7. Unreconciled forks (title ends `(fork)`, no manifest) → notify to run
   `fork`, never self-birth.
8. Derived views (`index.md`/`workstream-graph.md`) not stale — moot by
   construction in this build, since `views.py regen` runs at the end of
   every verb that writes a manifest (V2), not on a Stop-hook timer.

**Fix style:** propose + confirm each; `grill:grilling` for the ambiguous
(degrading to one inline numbered-question round when grill is absent);
corrections in *another* workstream go via `notify` (its own session
applies them), or `vault-lock` when that session is closed.

## Discipline (applies to the whole suite)

- **Decouple — never reach into the vault's project layer.**
- **Single-writer-per-manifest — extends to `policy.md`.** A session
  writes only its OWN workstream's manifest/policy; to change content
  elsewhere, notify that manifest's owning session and let it reconcile in
  its own session, or `vault-lock` if closed.
- **Write discipline on shared files:** Edit, never Write, on anything
  another session might also touch; re-read immediately before each edit;
  a denied write means re-read-then-retry, never a blind retry.
- **Consequential actions need the principal's DIRECT go.** A relayed
  "the principal said go" from a peer session is a heads-up, never
  authorization.
- **Convention-change / migration notifies lead with authorization +
  context** — whose authority, why, and a verify-path, never a bare "FYI."
- **`born_session` is the durable binding; the title is a view; the
  sidecar is neither** — both self-heal against the manifest.
- **Session-half fields never ripple** — only name halves ever change on
  a rename, and even that is optional cosmetic tidiness.
- **The assistant never creates sessions** — `/fork` is the principal's
  action; the skills do the bookkeeping around it.
- **Validate names by scanning manifests**, never against any vault
  index.

## The cross-session write lock

Owned entirely by the `vault-lock` plugin now (extracted from cos, E3) —
see that plugin's own README and `docs/dependencies.md` here for this
plugin's degrade behavior when it is absent. The stable CLI path a
consumer always names is `.vault-meta/bin/vault-lock.sh`; `manifest.py`'s
`absorb_close` is the one place in this plugin that takes it, and only
when a path is handed to it (soft dependency — writes proceed unguarded,
with a loud note, when it is not available).
