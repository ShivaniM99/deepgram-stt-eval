# Launch readiness: pharmacy refill line (Deepgram Nova-3)

*Generated 2026-10-12 by `dgeval`. Every number here is reproducible from the cached responses in `.cache/`. See README.*

## Verdict: **NO-GO**

Blocking issues:

- **drug_accuracy**: measured `0.9362`, requires `>= 0.98`
- **utterance_fragmentation_rate**: measured `0.8800`, requires `<= 0.1`

## Launch gate

| check | severity | observed | bound | result |
|---|---|---|---|---|
| drug_cross_confusion_rate | blocker | 0.0000 | `<= 0.0` | PASS |
| drug_accuracy | blocker | 0.9362 | `>= 0.98` | **FAIL** |
| rx_exact_match | blocker | 1.0000 | `>= 0.95` | PASS |
| hallucination_insertion_rate | blocker | 0.0000 | `<= 0.0` | PASS |
| dob_accuracy | warn | 0.9697 | `>= 0.9` | PASS |
| wer_domain | warn | 0.0807 | `<= 0.15` | PASS |
| wer_control_regression | blocker | 0.0007 | `delta <= 0.02` | PASS |
| finalization_delay_p95_ms | warn | 2016.1677 | `<= 2500` | PASS |
| utterance_fragmentation_rate | blocker | 0.8800 | `<= 0.1` | **FAIL** |

`utterance_fragmentation_rate` is taken from **tier C real voice @ endpointing=1000ms**, not from the synthetic tier: this is a production-readiness check, and the synthetic tier understated that metric by roughly 2.5x.


Thresholds and the reasoning behind each are in [`configs/gate.yaml`](../configs/gate.yaml). They are a starting point from the evaluator; the customer owns the final values.

## Headline metrics (baseline configuration)

| metric | value | note |
|---|---|---|
| WER, domain (tier A) | 9.04% | pooled over reference words, all channel conditions |
| WER, control (tier B) | 2.76% | LibriSpeech dev-clean; calibrates the harness itself |
| WER, real voice (tier C) | 4.79% | human recordings with domain vocabulary |
| drug name accuracy | 86.44% | n=376 |
| **drug cross-confusion** | **0.00%** | transcribed as a DIFFERENT real drug from its ISMP pair |
| Rx number exact match | 100.00% | n=188 |
| date of birth | 96.21% | n=132 |
| dose | 96.32% | n=136 |
| hallucination insertion rate | 0.00% | 0/24 non-speech clips |

### Degradation across the channel

The clean column is the reference; the telephony columns are the real transport. A single WER figure measured on clean audio would overstate readiness by the gap shown here.

| condition | clips | WER | drug acc | cross-conf | Rx acc |
|---|---|---|---|---|---|
| clean_16k | 100 | 8.24% | 87.23% | 0.00% | 100.00% |
| opus_16k_reverb | 100 | 7.65% | 91.49% | 0.00% | 100.00% |
| telephony_8k_ulaw | 100 | 9.00% | 86.17% | 0.00% | 100.00% |
| telephony_noisy_10db | 100 | 11.27% | 80.85% | 0.00% | 100.00% |

### By accent

| locale | clips | WER | drug acc | cross-conf |
|---|---|---|---|---|
| en_AU | 28 | 10.11% | 92.86% | 0.00% |
| en_GB | 36 | 5.05% | 97.22% | 0.00% |
| en_IE | 32 | 8.56% | 82.14% | 0.00% |
| en_IN | 128 | 8.64% | 84.68% | 0.00% |
| en_US | 156 | 10.13% | 82.86% | 0.00% |
| en_ZA | 20 | 9.52% | 100.00% | 0.00% |

### Fabrication by probe type

| probe | clips | fabricated | example output |
|---|---|---|---|
| babble | 3 | 0 | none |
| dial_tone | 3 | 0 | none |
| digital_silence | 3 | 0 | none |
| dtmf_sequence | 3 | 0 | none |
| hold_music | 3 | 0 | none |
| line_noise | 3 | 0 | none |
| ring_tone | 3 | 0 | none |
| room_tone | 3 | 0 | none |

## Keyterm prompting: lift and cost

| arm | domain WER | control WER | drug acc | cross-conf | Rx acc |
|---|---|---|---|---|---|
| baseline | 9.04% | 2.76% | 86.44% | 0.00% | 100.00% |
| keyterm_all | 8.47% | 2.83% | 92.82% | 0.00% | 100.00% |
| keyterm_half | 9.29% | 2.80% | 84.84% | 2.13% | 100.00% |

The `keyterm_half` arm boosts only one member of each ISMP confusable pair. If boosted-half accuracy rises while the withheld half falls below baseline, the boost is moving probability between confusable terms rather than improving acoustic discrimination, a result a recall-only evaluation would report as an improvement. See `src/dgeval/experiments/keyterm.py`.

## Endpointing: finalization delay against fragmentation

Network floor from the test machine: p50 80 ms, min 76 ms. All latencies below are client-observed and include this round trip in both directions; server-side figures would be lower.

| endpointing | clips | final p50 | p95 | p99 | ttfi p50 | fragmented | segs/clip |
|---|---|---|---|---|---|---|---|
| 10 ms | 20 | 311 | 509 | 1087 | 1045 | 75% | 2.0 |
| 100 ms | 20 | 413 | 1915 | 2219 | 1046 | 65% | 1.8 |
| 300 ms | 20 | 676 | 2017 | 2268 | 1040 | 45% | 1.4 |
| 500 ms | 20 | 954 | 2016 | 2276 | 1042 | 45% | 1.4 |
| 1000 ms | 20 | 1210 | 2034 | 2287 | 1039 | 45% | 1.4 |

**Recommended: `endpointing=None`** because it is the no swept value held fragmentation at or below 10% (best was 45% if measured). Utterances in this corpus contain internal pauses long enough to finalize on, so the knob alone cannot keep them whole. The call flow must reassemble consecutive finals before acting, or utterance_end_ms must be used to delimit turns..

### Fragmentation is worse on real speech than on synthetic

The 45% figure above comes from the synthetic tier. It was checked against real recordings on the suspicion that `say` renders a comma as an unnaturally long pause and was inflating the number. The opposite is true.

| tier | endpointing | fragmented | segments/clip | final p50 |
|---|---|---|---|---|
| A_domain_synthetic | 300 ms | 38% | 1.4 | 602 ms |
| A_domain_synthetic | 1000 ms | 38% | 1.4 | 1246 ms |
| C_real_domain | 300 ms | 94% | 2.1 | 706 ms |
| C_real_domain | 1000 ms | 88% | 1.9 | 1396 ms |

A human reading a long sentence containing a seven-digit number pauses naturally and variably; synthesis is metronomic. On real speech, **94% of utterances arrive in more than one piece at `endpointing=300ms`, and 88% still do at 1000ms**, so nearly every real caller will be split. The synthetic tier understated this by roughly 2.5x, which is the clearest argument in this report for why a real-voice tier is not optional.

**Architectural consequence:** the call flow cannot act on individual finals. It must buffer consecutive finals and use `UtteranceEnd` / `utterance_end_ms` to delimit a caller turn. No value of `endpointing` makes acting-per-final safe.

## Cost model

| volume | STT minutes/mo | Nova-3 pre-recorded | Nova-3 streaming |
|---|---|---|---|
| 1,000 calls/mo @ 3 min | 3,000 | $13 | $14 |
| 10,000 calls/mo @ 3 min | 30,000 | $129 | $145 |
| 100,000 calls/mo @ 3 min | 300,000 | $1,290 | $1,450 |

List Pay-As-You-Go rates; a Growth commitment discounts these by roughly 10-15%. Voice Agent API is priced separately per minute of socket connection time and is out of scope for this phase.

## Methodology

- **Corpus**: four tiers. A, templated pharmacy utterances with exact entity labels generated alongside the text, drug names drawn from the ISMP confused-drug-names list. B, LibriSpeech dev-clean as a control that calibrates the harness. C, real human recordings. D, synthesized non-speech probes.

- **Channel**: every speech clip is rendered through clean 16 kHz, 8 kHz G.711 mu-law, mu-law plus pink noise at an exact 10 dB SNR, and Opus at 16 kbps with reverb.

- **Normalization**: both reference and hypothesis pass through one documented normalizer before WER. Rules are listed in `src/dgeval/metrics/normalize.py` and verified by `tests/test_normalize.py`.

- **WER** is pooled over total reference words, not averaged across per-clip rates, which would over-weight short clips.

- **Caching**: every API response is keyed on (audio bytes, full request config), so results are reproducible and re-scoring costs nothing.


## What this does NOT establish

- **Tier A is synthetic, and it is PESSIMISTIC rather than flattering.** Two assumptions made before measuring turned out backwards. Synthetic WER (8.47%) is HIGHER than real-voice WER (4.52%), because older macOS `say` voices are unnaturally hard; and synthetic prosody UNDERSTATES fragmentation (38% against 94%), because synthesis pauses metronomically where a human does not. Tier A is therefore a stress test for entity recognition, not a proxy for real callers, and neither of its numbers should be read as predicting production behaviour in the same direction.

- **No real call audio.** Actual pharmacy calls are PHI and cannot be used here. A pre-launch pilot on consented recordings from the customer's own line is the only way to close this, and it should gate full rollout.

- **Latency is client-observed** from one machine on one connection, inclusive of network round trip. It bounds what a user in this network position experiences; it does not measure Deepgram's processing time. Three earlier definitions of this metric were discarded as meaningless against the live API; see `src/dgeval/asr/stream.py`.

- **Single-turn only.** No barge-in, no interruption handling, no multi-turn state, no tool calling. The Voice Agent API is not evaluated in this phase.

- **One model version.** Results are valid for the model string recorded in the results files on the generation date. The gate exists so this is re-checked on every model change rather than assumed.


## Recommended next steps

1. Confirm or amend the thresholds in `configs/gate.yaml`; they encode risk tolerance, which is the customer's call, not the evaluator's.

2. Add a drug-name confirmation readback to the call flow. The `drug_accuracy` threshold assumes one exists.

3. Route any utterance whose drug name is below a confidence floor to a human rather than to the automated path.

4. Run this gate in CI against every model version bump.

5. Pilot on consented recordings from the real line before full rollout, and re-measure tier C against it.

6. Phase 2: evaluate the Voice Agent API for tool-call accuracy, groundedness on out-of-scope requests, numeric readback, and barge-in.

