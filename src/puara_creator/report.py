# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Société des Arts Technologiques (SAT)
# Copyright (C) 2026 Input Devices and Music Interaction Laboratory (IDMIL), McGill University
# Copyright (C) 2026 Eduardo Meneses
"""The scoring report.

Two rules from docs/EVALUATION.md are enforced by the shape of this page rather than left
to the reader's discipline: the false-positive rate per minute of ambient material is the
headline, and no pooled figure appears without the per-subject spread beside it.
"""

from __future__ import annotations

import html
from pathlib import Path

from rich.console import Console
from rich.table import Table

from puara_creator.scoring import ScoreResult, holdout_consultations


def print_report(console: Console, result: ScoreResult, corpus_root: Path) -> None:
    overall = result.overall
    console.print()
    console.print(
        f"[bold]{result.options.gesture_class}[/] · split {result.options.split} · "
        f"labels {result.label_source} · tolerance {result.options.tolerance_s * 1000:.0f} ms · "
        f"osc-loopback"
    )
    if result.transport_ms:
        console.print(
            f"  transport round trip p50 {result.transport_ms[0]:.2f} ms  "
            f"p95 {result.transport_ms[1]:.2f} ms",
            style="dim",
        )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_column("note", style="dim")

    table.add_row(
        "[bold]FP / min ambient[/]",
        f"[bold]{overall.fp_per_minute_ambient:.2f}[/]",
        "headline — behaviour during ordinary handling",
    )
    table.add_row(
        "recall", f"{overall.recall:.3f}", f"{overall.counts.matched}/{overall.counts.references}"
    )
    table.add_row("precision", f"{overall.precision:.3f}", "corpus-balance dependent")
    corrected = (
        f"corrected {overall.latency_ms(0.5) - overall.transport_correction_ms:.0f} ms"
        if result.transport_ms
        else "includes transport"
    )
    table.add_row("latency p50", f"{overall.latency_ms(0.5):.0f} ms", corrected)
    table.add_row("latency p95", f"{overall.latency_ms(0.95):.0f} ms", "")
    table.add_row("onset jitter", f"{overall.onset_jitter_ms:.0f} ms", "standard deviation")
    table.add_row("double fire", f"{overall.double_fire_rate:.1%}", "extra fires within 500 ms")
    console.print(table)

    if result.per_subject:
        subjects = Table(title="per subject", title_justify="left", box=None, padding=(0, 2))
        for column in ("subject", "recall", "FP/min", "latency p50", "instances"):
            subjects.add_column(column, justify="left" if column == "subject" else "right")
        recalls = []
        for name, report in sorted(result.per_subject.items()):
            recalls.append(report.recall)
            subjects.add_row(
                name,
                f"{report.recall:.3f}",
                f"{report.fp_per_minute_ambient:.2f}",
                f"{report.latency_ms(0.5):.0f} ms",
                str(report.counts.references),
            )
        console.print(subjects)
        if len(recalls) > 1:
            gap = max(recalls) - min(recalls)
            style = "yellow" if gap > 0.05 else "dim"
            console.print(
                f"  [{style}]recall spread {gap:.3f} — the pooled figure "
                f"{'hides the weakest subject' if gap > 0.05 else 'is representative'}[/]"
            )

    if result.failures:
        console.print(f"\n[bold]failures[/] ({len(result.failures)}, first 8)")
        for failure in result.failures[:8]:
            console.print(
                f"  {failure.kind:<15} {failure.session_id} take {failure.take:03d} "
                f"t={failure.t:.3f}  [dim]{failure.detail}[/]"
            )

    for warning in result.warnings:
        console.print(f"  [yellow]{warning}[/]")

    consultations = holdout_consultations(corpus_root)
    if consultations:
        console.print(
            f"  [yellow]the test split has now been consulted {consultations} time(s); "
            f"report that number alongside any published figure[/]"
        )


_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#111; --dim:#666; --line:#ddd;
        --ok:#1a7f37; --warn:#9a6700; --bad:#b3261e; --accent:#0b5fff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101215; --fg:#e6e6e6; --dim:#9aa0a6; --line:#2a2e33;
          --ok:#4ac26b; --warn:#d4a72c; --bad:#ff6b5e; --accent:#7aa2ff; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.55 ui-monospace,SFMono-Regular,
       Menlo,monospace; margin:0; padding:2rem 1.5rem; }
main { max-width:56rem; margin:0 auto; }
h1 { font-size:1.3rem; margin:0 0 .25rem; }
.sub { color:var(--dim); margin:0 0 1.5rem; font-size:.85rem; }
.headline { border:1px solid var(--line); border-radius:.5rem; padding:1rem 1.25rem;
            margin:0 0 1.5rem; }
.headline .n { font-size:2.4rem; font-weight:700; }
.headline .l { color:var(--dim); font-size:.85rem; }
table { border-collapse:collapse; width:100%; margin:0 0 1.5rem; }
th,td { text-align:right; padding:.35rem .6rem; border-bottom:1px solid var(--line); }
th:first-child,td:first-child { text-align:left; }
th { color:var(--dim); font-weight:500; font-size:.8rem; text-transform:uppercase;
     letter-spacing:.03em; }
.note { color:var(--dim); font-size:.8rem; }
.warn { color:var(--warn); }
.bad { color:var(--bad); }
.ok { color:var(--ok); }
.scroll { overflow-x:auto; }
footer { color:var(--dim); font-size:.78rem; border-top:1px solid var(--line);
         padding-top:1rem; margin-top:2rem; }
"""


def _row(label: str, value: str, note: str = "") -> str:
    return (
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(value)}</td>"
        f"<td class='note'>{html.escape(note)}</td></tr>"
    )


def write_html_report(path: Path, result: ScoreResult, corpus_root: Path) -> None:
    overall = result.overall
    data = result.to_dict()
    consultations = holdout_consultations(corpus_root)

    subject_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{r.recall:.3f}</td>"
        f"<td>{r.fp_per_minute_ambient:.2f}</td><td>{r.latency_ms(0.5):.0f} ms</td>"
        f"<td>{r.counts.references}</td></tr>"
        for name, r in sorted(result.per_subject.items())
    )
    recalls = [r.recall for r in result.per_subject.values()]
    gap = max(recalls) - min(recalls) if len(recalls) > 1 else 0.0
    spread_note = (
        f"<p class='note {'warn' if gap > 0.05 else ''}'>recall spread {gap:.3f}"
        f"{' — the pooled figure hides the weakest subject' if gap > 0.05 else ''}</p>"
        if len(recalls) > 1
        else "<p class='note warn'>one subject only: this measures a wrist, not a descriptor</p>"
    )

    failures = "".join(
        f"<tr><td>{html.escape(f.kind)}</td><td>{html.escape(f.session_id)}</td>"
        f"<td>{f.take:03d}</td><td>{f.t:.3f}</td><td class='note'>{html.escape(f.detail)}</td></tr>"
        for f in result.failures[:100]
    )
    warnings = "".join(f"<li class='warn'>{html.escape(w)}</li>" for w in result.warnings)
    transport = (
        f"round trip p50 {result.transport_ms[0]:.2f} ms, p95 {result.transport_ms[1]:.2f} ms"
        if result.transport_ms
        else "unmeasured — the descriptor did not answer /pcr/ping, so latency includes transport"
    )

    correction = overall.transport_correction_ms
    recall_row = _row(
        "recall",
        f"{overall.recall:.3f}",
        f"{overall.counts.matched}/{overall.counts.references} instances found",
    )
    precision_row = _row(
        "precision", f"{overall.precision:.3f}", "depends on corpus balance; for completeness"
    )
    p50_row = _row(
        "latency p50",
        f"{overall.latency_ms(0.5):.0f} ms",
        f"corrected {overall.latency_ms(0.5) - correction:.0f} ms",
    )
    p95_row = _row(
        "latency p95",
        f"{overall.latency_ms(0.95):.0f} ms",
        f"corrected {overall.latency_ms(0.95) - correction:.0f} ms",
    )
    jitter_row = _row(
        "onset jitter", f"{overall.onset_jitter_ms:.0f} ms", "standard deviation of latency"
    )
    double_row = _row(
        "double fire", f"{overall.double_fire_rate:.1%}", "extra detections within 500 ms"
    )
    settling_row = _row(
        "settling discarded",
        str(overall.counts.settling),
        f"detections inside the first {result.options.warmup_s:.1f} s of a take",
    )

    body = f"""<style>{_CSS}</style>
<main>
<h1>{html.escape(result.options.gesture_class)} — scoring report</h1>
<p class="sub">
  split <b>{html.escape(result.options.split)}</b> ·
  labels <b>{html.escape(result.label_source)}</b> ·
  tolerance {result.options.tolerance_s * 1000:.0f} ms ·
  warm-up {result.options.warmup_s:.1f} s ·
  transport osc-loopback ·
  {len(result.sessions)} session(s), {overall.counts.references} instances ·
  {html.escape(data["utc"])}
</p>

<div class="headline">
  <div class="n">{overall.fp_per_minute_ambient:.2f}</div>
  <div class="l">false positives per minute of ambient material — the number that decides
  whether this is playable. {overall.counts.ambient_minutes:.1f} minutes of ambient material
  in this split.</div>
</div>

<div class="scroll"><table>
<thead><tr><th>metric</th><th>value</th><th>note</th></tr></thead>
<tbody>
{recall_row}
{precision_row}
{p50_row}
{p95_row}
{jitter_row}
{double_row}
{settling_row}
</tbody></table></div>

<h2 style="font-size:1rem">per subject</h2>
<div class="scroll"><table>
<thead><tr><th>subject</th><th>recall</th><th>FP/min</th><th>latency p50</th>
<th>instances</th></tr></thead>
<tbody>{subject_rows}</tbody></table></div>
{spread_note}

<h2 style="font-size:1rem">failures</h2>
<div class="scroll"><table>
<thead><tr><th>kind</th><th>session</th><th>take</th><th>t</th><th>detail</th></tr></thead>
<tbody>{failures or "<tr><td colspan='5' class='note'>none</td></tr>"}</tbody></table></div>

{f"<ul>{warnings}</ul>" if warnings else ""}

<footer>
  descriptor under test <b>{html.escape(result.options.dut)}</b>
  {f"version {html.escape(result.options.dut_version)}" if result.options.dut_version else ""}<br>
  transport {html.escape(transport)}<br>
  test split consulted {consultations} time(s) — report this alongside any published figure<br>
  puara-creator {html.escape(data["tool_version"])}
</footer>
</main>
"""
    path.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(result.options.gesture_class)} — puara-creator</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"</head><body>{body}</body></html>"
    )
