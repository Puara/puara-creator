# Session handoff

This document carries the context of the design conversation that produced this repository, so that
work can continue in a fresh Claude Code session — or by a human who was not in the room — without
re-deriving the decisions.

**Origin:** design conversation, 3 August 2026, in the `/media/Storage/Assistant` workspace.
**Participants:** Edu Meneses; Claude Code (Opus 5).
**Output of that session:** this repository, documentation only, no implementation.

---

## 1. How to resume

```bash
cd /media/Storage/puara-creator
claude
```

Then, as the opening prompt:

> Read `docs/SESSION_HANDOFF.md`, `docs/ARCHITECTURE.md`, and `docs/SPEC_V1.md`. We are implementing
> v1. Start with <component>.

Reading order for full context: `README.md` → `docs/ARCHITECTURE.md` → `docs/SPEC_V1.md` →
`docs/FORMAT.md` → `docs/EVALUATION.md`. `docs/DESIGN_NOTES.md` holds the rejected options and is
worth reading before proposing a change to any of the above.

## 2. How the design arrived where it is

The conversation began from a proposal: a tool that captures OSC sensor data, timestamps it, emits a
periodic pulse at which the user performs the target gesture, and writes a text file that an AI
agent or a human could use to derive either a deterministic algorithm or a small model. Semantic
information from the OSC namespace would help judge whether a given gesture is extractable at all.

Three things changed between that proposal and this specification.

**The pulse became a window, and cues became distinct from labels.** Reaction time to a periodic cue
varies by 150–400 ms and carries a systematic anticipation bias, which is larger than a `jab`. The
cue is now recorded as a stimulus event; the label is refined separately by an energy-based
segmenter or by cross-repetition alignment, and carries a `source` field recording how it was
obtained. A corpus labelled naively can be re-labelled later without being re-recorded.

**Negative material became mandatory and load-bearing.** A cued protocol produces positives only,
and a descriptor trained or tuned on positives only fires continuously in real use. Ambient takes —
rest, handling, confusable gestures, ordinary playing — are now budgeted at parity with cued
material, and the headline metric is false positives per minute of ambient material rather than
accuracy.

**The learned-model path was dropped, on Edu's proposal, and the whole architecture simplified
around it.** Descriptors are deterministic algorithms; machine learning, where it appears at all,
designs them offline and never ships to the instrument. This decision cascades: the corpus shrinks
by two orders of magnitude, subject diversity becomes affordable, weak cue labels become tolerable,
the deployment pipeline disappears, and the artefact stays a readable header file that a performer
can edit before a concert. The reasoning and the escape hatch are in `docs/DESIGN_NOTES.md` §1.

## 3. Decisions that are settled

| Decision | Where it is documented |
| --- | --- |
| Deterministic descriptors only; no TinyML on the instrument | `DESIGN_NOTES.md` §1 |
| Machine learning at design time only, in three separable techniques | `ARCHITECTURE.md` §2 |
| Build order: fitting → synthesis → language-model proposer | `ROADMAP.md` |
| v1 is recorder, replayer, scorer, and web interface — nothing else | `SPEC_V1.md` §1 |
| Descriptor under test is an OSC endpoint, not a linked library | `ARCHITECTURE.md` §4, `SPEC_V1.md` §3 |
| Corpus is JSONL, per-take files, arrival-ordered, `CLOCK_MONOTONIC` | `FORMAT.md` |
| Cues and labels are separate event kinds, labels carry provenance | `FORMAT.md` §4.1 |
| Namespace semantics recorded from v1 even though only partly used | `ARCHITECTURE.md` §5 |
| Splits by subject and session; holdout unlock is logged | `EVALUATION.md` §6 |
| Headline metric is false positives per minute of ambient material | `EVALUATION.md` §2.2 |
| Per-subject reporting mandatory alongside every pooled figure | `EVALUATION.md` §4 |
| Python 3.12 with `uv`; FastAPI plus one HTML page, no build step | `SPEC_V1.md` §1, `UI.md` §5 |
| AGPL-3.0 for the tool, with an exception for generated output | `LICENSING.md` |

## 4. Open questions

These were raised and not resolved. They block nothing in v1 except where noted.

1. **Firmware support for device-side sequence numbers and timestamps.** Without them, every latency
   figure includes an unquantified transport term. This is a `puara-module` change and a prerequisite
   for trustworthy latency measurement, not an optimisation. Blocking for meaningful latency results;
   not blocking for the v1 implementation.
2. **The output licence exception.** Proposed in `LICENSING.md` and needing confirmation by the
   copyright holders — SAT, IDMIL, and Edu — before the first release.
3. **Whether the T-Stick can deliver a haptic cue.** The protocol prefers haptic or audible cueing
   at the instrument over visual cueing, which adds display and eye-to-hand latency to the dominant
   error term. If the hardware cannot, audible cueing from the workstation is the fallback.
4. **Which gesture is the first target.** `jab` is used throughout the documentation as the running
   example because it is short enough to make the labelling problem obvious. A longer gesture would
   be an easier first target and would validate the pipeline with less labelling pressure.
5. **Where the reference corpus is hosted.** Too large for the repository beyond a small example.
   Candidates: Zenodo with a DOI, alongside a paper; or SAT infrastructure.
6. **Whether v1 ships an ossia/score playback node.** It would make the loop usable inside the
   environment where the descriptors already run, and `score-addon-puara` exists. Currently deferred
   to *Beyond* in the roadmap.

## 5. Next actions

In order, each independently useful:

1. **`record`, terminal only.** OSC listener, JSONL writer, session and take structure, health
   metrics, cue engine, keyboard control. Verifiable against a synthetic OSC sender before any
   hardware is involved.
2. **`play`.** Deterministic replay with faithful inter-arrival timing. Verifiable by recording the
   replay and comparing the two streams.
3. **`inspect`.** Health and coverage reporting over an existing corpus; makes the first two
   testable without the third.
4. **`score`.** Matching, metrics, the reference threshold descriptor in `examples/`, JSON and HTML
   output.
5. **Web interface.** Session, Capture, Corpus screens first; Annotate and Evaluate follow.
6. **First real session.** Two subjects, one gesture class, matched ambient material — which is
   also acceptance criterion 3 in `SPEC_V1.md` §9.

The web interface deliberately comes after the CLI, because the CLI is the contract and the
interface constructs CLI invocations.

## 6. Conventions carried from the conversation

- Documentation is written in Edu's voice and register: technical, precise, acronyms expanded on
  first use, problem-then-solution framing, explicit logical connectors, collaborators credited by
  name and role.
- The repository holds no recorded data. `corpus/` is excluded from version control; publishing a
  corpus is a deliberate act.
- Claude Code configuration files are excluded from version control. This handoff document is
  committed, since it is project documentation rather than tooling state.
