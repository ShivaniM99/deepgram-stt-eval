"""Assemble measurements into a launch-readiness report.

The report is written for the customer, not for the person who ran it, so it
leads with the decision, states what was measured, and is explicit about what
was NOT measured. A readiness report that omits its own blind spots is worse
than no report, because it transfers false confidence.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from .config import PRICE_PER_MIN, REPORTS, RESULTS
from .gate import evaluate, verdict


def _pct(v: Any, digits: int = 2) -> str:
    return "n/a" if v is None else f"{float(v) * 100:.{digits}f}%"


def _num(v: Any, digits: int = 0) -> str:
    return "n/a" if v is None else f"{float(v):.{digits}f}"


def load_results() -> dict[str, Any]:
    out: dict[str, Any] = {"arms": [], "endpointing": None}
    p = RESULTS / "summary.json"
    if p.exists():
        out["arms"] = json.loads(p.read_text())
    e = RESULTS / "endpointing.json"
    if e.exists():
        out["endpointing"] = json.loads(e.read_text())
    return out


def gate_measurements(data: dict[str, Any]) -> dict[str, float | None]:
    """Map measured results onto the threshold names in configs/gate.yaml.

    Anything genuinely not measured maps to None rather than to a default, so
    the gate reports it as SKIPPED instead of silently passing. A gate that
    passes because a metric was absent is the failure mode this avoids.
    """
    arms = {a["arm"]: a for a in data["arms"]}
    best = arms.get("keyterm_all") or arms.get("baseline")
    m: dict[str, float | None] = dict.fromkeys(
        ["drug_cross_confusion_rate", "drug_accuracy", "rx_exact_match",
         "hallucination_insertion_rate", "dob_accuracy", "wer_domain",
         "wer_control_regression", "finalization_delay_p95_ms",
         "utterance_fragmentation_rate"], None)

    if best:
        ent = best.get("entities") or {}
        if "drug" in ent:
            m["drug_cross_confusion_rate"] = ent["drug"].get("cross_confusion_rate")
        hall = best.get("hallucination") or {}
        m["hallucination_insertion_rate"] = hall.get("insertion_rate")

        # Entity thresholds are scoped to the telephony condition, so they are
        # read off that slice rather than off the all-conditions aggregate.
        tel = next((r for r in best.get("by_condition", [])
                    if r.get("condition") == "telephony_8k_ulaw"), None)
        if tel:
            m["drug_accuracy"] = tel.get("drug_accuracy")
            m["rx_exact_match"] = tel.get("rx_accuracy")
            m["dob_accuracy"] = tel.get("dob_accuracy")
            m["wer_domain"] = tel.get("wer")

    if "baseline" in arms and best and best is not arms["baseline"]:
        b, k = arms["baseline"].get("wer_control"), best.get("wer_control")
        if b is not None and k is not None:
            m["wer_control_regression"] = max(0.0, k - b)

    ep = data.get("endpointing")
    if ep and ep.get("points"):
        rec_ms = (ep.get("recommendation") or {}).get("recommended_endpointing_ms")
        pt = next((p for p in ep["points"] if p["endpointing_ms"] == rec_ms),
                  None) or ep["points"][0]
        m["finalization_delay_p95_ms"] = pt.get("final_p95")
        m["utterance_fragmentation_rate"] = pt.get("fragmentation_rate")

    return m


def render(data: dict[str, Any] | None = None, gate_path: str | None = None) -> str:
    data = data or load_results()
    meas = gate_measurements(data)
    checks = evaluate(meas, None if gate_path is None else None)
    vd = verdict(checks)

    arms = {a["arm"]: a for a in data["arms"]}
    L: list[str] = []
    w = L.append

    w(f"# Launch readiness: pharmacy refill line (Deepgram Nova-3)\n")
    w(f"*Generated {date.today().isoformat()} by `dgeval`. "
      f"Every number here is reproducible from the cached responses in "
      f"`.cache/` -- see README.*\n")

    # --- verdict -----------------------------------------------------------
    w(f"## Verdict: **{vd['decision']}**\n")
    if vd["blockers_failed"]:
        w("Blocking issues:\n")
        for name in vd["blockers_failed"]:
            c = next(c for c in checks if c.name == name)
            w(f"- **{name}** — measured `{_num(c.observed, 4)}`, requires `{c.bound}`")
        w("")
    if vd["warnings"]:
        w("Conditions to accept before launch:\n")
        for name in vd["warnings"]:
            c = next(c for c in checks if c.name == name)
            w(f"- {name} — measured `{_num(c.observed, 4)}`, target `{c.bound}`")
        w("")
    if vd["skipped"]:
        w(f"**Not measured in this run** (reported as skipped, not as passing): "
          f"{', '.join(vd['skipped'])}\n")

    # --- gate table --------------------------------------------------------
    w("## Launch gate\n")
    w("| check | severity | observed | bound | result |")
    w("|---|---|---|---|---|")
    for c in checks:
        res = "SKIP" if c.skipped else ("PASS" if c.passed else "**FAIL**")
        w(f"| {c.name} | {c.severity} | {_num(c.observed, 4)} | `{c.bound}` | {res} |")
    w("\nThresholds and the reasoning behind each are in "
      "[`configs/gate.yaml`](../configs/gate.yaml). They are a starting point "
      "from the evaluator; the customer owns the final values.\n")

    # --- headline ----------------------------------------------------------
    base = arms.get("baseline")
    if base:
        w("## Headline metrics (baseline configuration)\n")
        ent = base.get("entities") or {}
        w("| metric | value | note |")
        w("|---|---|---|")
        w(f"| WER, domain (tier A) | {_pct(base.get('wer_domain'))} | "
          f"pooled over reference words, all channel conditions |")
        if base.get("wer_control") is not None:
            w(f"| WER, control (tier B) | {_pct(base.get('wer_control'))} | "
              f"LibriSpeech dev-clean; calibrates the harness itself |")
        if base.get("wer_real") is not None:
            w(f"| WER, real voice (tier C) | {_pct(base.get('wer_real'))} | "
              f"human recordings with domain vocabulary |")
        if "drug" in ent:
            w(f"| drug name accuracy | {_pct(ent['drug'].get('accuracy'))} | "
              f"n={ent['drug'].get('n')} |")
            w(f"| **drug cross-confusion** | "
              f"**{_pct(ent['drug'].get('cross_confusion_rate'))}** | "
              f"transcribed as a DIFFERENT real drug from its ISMP pair |")
        for k, lbl in (("rx", "Rx number exact match"), ("dob", "date of birth"),
                       ("dose", "dose")):
            if k in ent:
                w(f"| {lbl} | {_pct(ent[k].get('accuracy'))} | n={ent[k].get('n')} |")
        hall = base.get("hallucination") or {}
        w(f"| hallucination insertion rate | {_pct(hall.get('insertion_rate'))} | "
          f"{hall.get('n_fabricated')}/{hall.get('n_clips')} non-speech clips |")
        w("")

        if base.get("by_condition"):
            w("### Degradation across the channel\n")
            w("The clean column is the reference; the telephony columns are the "
              "real transport. A single WER figure measured on clean audio would "
              "overstate readiness by the gap shown here.\n")
            w("| condition | clips | WER | drug acc | cross-conf | Rx acc |")
            w("|---|---|---|---|---|---|")
            for r in base["by_condition"]:
                w(f"| {r['condition']} | {r['clips']} | {_pct(r['wer'])} | "
                  f"{_pct(r['drug_accuracy'])} | {_pct(r['drug_cross_confusion'])} | "
                  f"{_pct(r['rx_accuracy'])} |")
            w("")

        if base.get("by_locale"):
            w("### By accent\n")
            w("| locale | clips | WER | drug acc | cross-conf |")
            w("|---|---|---|---|---|")
            for r in base["by_locale"]:
                w(f"| {r['locale']} | {r['clips']} | {_pct(r['wer'])} | "
                  f"{_pct(r['drug_accuracy'])} | {_pct(r['drug_cross_confusion'])} |")
            w("")

        if base.get("confusions"):
            w("### Cross-confusions observed\n")
            w("Each row is a case where the transcript named a different real, "
              "dispensable drug. These do not fail downstream validation because "
              "they are well-formed.\n")
            w("| spoken | transcribed as | count |")
            w("|---|---|---|")
            for c in base["confusions"]:
                w(f"| {c['expected']} | **{c['heard_as']}** | {c['count']} |")
            w("")

        if hall.get("by_probe"):
            w("### Fabrication by probe type\n")
            w("| probe | clips | fabricated | example output |")
            w("|---|---|---|---|")
            for p, v in hall["by_probe"].items():
                ex = (v.get("examples") or [""])[0]
                ex = (ex[:70] + "...") if len(ex) > 70 else ex
                w(f"| {p} | {v['n']} | {v['fabricated']} | {ex or '—'} |")
            w("")

    # --- keyterm -----------------------------------------------------------
    if len(arms) > 1:
        w("## Keyterm prompting: lift and cost\n")
        w("| arm | domain WER | control WER | drug acc | cross-conf | Rx acc |")
        w("|---|---|---|---|---|---|")
        for name in ("baseline", "keyterm_all", "keyterm_half"):
            a = arms.get(name)
            if not a:
                continue
            e = (a.get("entities") or {}).get("drug", {})
            rx = (a.get("entities") or {}).get("rx", {})
            w(f"| {name} | {_pct(a.get('wer_domain'))} | {_pct(a.get('wer_control'))} | "
              f"{_pct(e.get('accuracy'))} | {_pct(e.get('cross_confusion_rate'))} | "
              f"{_pct(rx.get('accuracy'))} |")
        w("\nThe `keyterm_half` arm boosts only one member of each ISMP "
          "confusable pair. If boosted-half accuracy rises while the withheld "
          "half falls below baseline, the boost is moving probability between "
          "confusable terms rather than improving acoustic discrimination — "
          "a result a recall-only evaluation would report as an improvement. "
          "See `src/dgeval/experiments/keyterm.py`.\n")

    # --- endpointing -------------------------------------------------------
    ep = data.get("endpointing")
    if ep and ep.get("points"):
        w("## Endpointing: finalization delay against fragmentation\n")
        rtt = ep.get("baseline_rtt") or {}
        if rtt:
            w(f"Network floor from the test machine: p50 "
              f"{_num(rtt.get('rtt_p50_ms'))} ms, min {_num(rtt.get('rtt_min_ms'))} ms. "
              f"All latencies below are client-observed and include this round "
              f"trip in both directions; server-side figures would be lower.\n")
        w("| endpointing | clips | final p50 | p95 | p99 | ttfi p50 | fragmented | segs/clip |")
        w("|---|---|---|---|---|---|---|---|")
        for p in ep["points"]:
            w(f"| {p['endpointing_ms']} ms | {p['n_clips']} | "
              f"{_num(p['final_p50'])} | {_num(p['final_p95'])} | "
              f"{_num(p['final_p99'])} | {_num(p['ttfi_p50'])} | "
              f"{_pct(p['fragmentation_rate'], 0)} | {p['mean_segments']:.1f} |")
        rec = ep.get("recommendation") or {}
        w(f"\n**Recommended: `endpointing={rec.get('recommended_endpointing_ms')}`** "
          f"— {rec.get('reason')}\n")
        if rec.get("rejected"):
            w("Faster settings were rejected for splitting utterances:\n")
            for r in rec["rejected"]:
                w(f"- `{r['endpointing_ms']} ms`: p95 "
                  f"{_num(r['final_p95_ms'])} ms but "
                  f"{_pct(r['fragmentation_rate'], 0)} of utterances arrived "
                  f"in more than one piece")
            w("\nFragmentation matters disproportionately here because the Rx "
              "number falls at the end of the utterance. A component acting on "
              "the first final of a split request holds a drug name with no "
              "prescription attached — which looks like a complete, "
              "well-formed request rather than an error.\n")

    # --- cost --------------------------------------------------------------
    w("## Cost model\n")
    w("| volume | STT minutes/mo | Nova-3 pre-recorded | Nova-3 streaming |")
    w("|---|---|---|---|")
    for calls, mins in ((1_000, 3), (10_000, 3), (100_000, 3)):
        total = calls * mins
        w(f"| {calls:,} calls/mo @ {mins} min | {total:,} | "
          f"${total * PRICE_PER_MIN['nova-3-prerecorded']:,.0f} | "
          f"${total * PRICE_PER_MIN['nova-3-streaming']:,.0f} |")
    w("\nList Pay-As-You-Go rates; a Growth commitment discounts these by "
      "roughly 10-15%. Voice Agent API is priced separately per minute of "
      "socket connection time and is out of scope for this phase.\n")

    # --- methodology and limits -------------------------------------------
    w("## Methodology\n")
    w("- **Corpus**: four tiers. A, templated pharmacy utterances with exact "
      "entity labels generated alongside the text, drug names drawn from the "
      "ISMP confused-drug-names list. B, LibriSpeech dev-clean as a control "
      "that calibrates the harness. C, real human recordings. D, synthesized "
      "non-speech probes.\n")
    w("- **Channel**: every speech clip is rendered through clean 16 kHz, "
      "8 kHz G.711 mu-law, mu-law plus pink noise at an exact 10 dB SNR, and "
      "Opus at 16 kbps with reverb.\n")
    w("- **Normalization**: both reference and hypothesis pass through one "
      "documented normalizer before WER. Rules are listed in "
      "`src/dgeval/metrics/normalize.py` and verified by `tests/test_normalize.py`.\n")
    w("- **WER** is pooled over total reference words, not averaged across "
      "per-clip rates, which would over-weight short clips.\n")
    w("- **Caching**: every API response is keyed on (audio bytes, full request "
      "config), so results are reproducible and re-scoring costs nothing.\n")

    w("\n## What this does NOT establish\n")
    w("- **Tier A is synthetic.** Synthesized speech lacks disfluency, breath, "
      "variable rate and genuine channel interaction. It is useful for exact "
      "entity labelling at volume and it is not a substitute for real callers. "
      "Tier C exists to anchor it; the larger the gap between tier A and tier C "
      "results, the less weight tier A should carry.\n")
    w("- **No real call audio.** Actual pharmacy calls are PHI and cannot be "
      "used here. A pre-launch pilot on consented recordings from the "
      "customer's own line is the only way to close this, and it should gate "
      "full rollout.\n")
    w("- **Latency is client-observed** from one machine on one connection, "
      "inclusive of network round trip. It bounds what a user in this network "
      "position experiences; it does not measure Deepgram's processing time. "
      "Three earlier definitions of this metric were discarded as meaningless "
      "against the live API; see `src/dgeval/asr/stream.py`.\n")
    w("- **Single-turn only.** No barge-in, no interruption handling, no "
      "multi-turn state, no tool calling. The Voice Agent API is not evaluated "
      "in this phase.\n")
    w("- **One model version.** Results are valid for the model string "
      "recorded in the results files on the generation date. The gate exists "
      "so this is re-checked on every model change rather than assumed.\n")

    w("\n## Recommended next steps\n")
    w("1. Confirm or amend the thresholds in `configs/gate.yaml`; they encode "
      "risk tolerance, which is the customer's call, not the evaluator's.\n")
    w("2. Add a drug-name confirmation readback to the call flow. The "
      "`drug_accuracy` threshold assumes one exists.\n")
    w("3. Route any utterance whose drug name is below a confidence floor to a "
      "human rather than to the automated path.\n")
    w("4. Run this gate in CI against every model version bump.\n")
    w("5. Pilot on consented recordings from the real line before full rollout, "
      "and re-measure tier C against it.\n")
    w("6. Phase 2: evaluate the Voice Agent API — tool-call accuracy, "
      "groundedness on out-of-scope requests, numeric readback, and barge-in.\n")

    return "\n".join(L) + "\n"


def write(path: str | None = None) -> str:
    out = REPORTS / "launch_readiness.md" if path is None else path
    text = render()
    with open(out, "w") as f:
        f.write(text)
    return str(out)
