# Refill line: pre-launch assessment

**To:** Northside Pharmacy, operations and engineering
**From:** Shivani Madan
**Re:** Is Deepgram Nova-3 ready for the inbound refill line?
**Date:** 12 October 2026

---

## Recommendation: do not launch yet. Two fixes, both in the call flow.

Nova-3 is accurate enough for this use case and does not invent words. Neither
blocker is a reason to look at another vendor. Both are things the integration
has to do differently.

| | |
|---|---|
| **Blocker 1** | Drug-name accuracy is 93.6% on phone-quality audio against a 98% floor. Closing it needs a confirmation readback, not a better model. |
| **Blocker 2** | 88% of real callers' requests arrive split across two or more transcript segments. Acting on the first segment gives you a drug name with no prescription number attached. |

Everything else passed, including the two tests I expected to be the problem.

---

## What I tested

648 audio clips, 67 minutes, across four sets: 100 templated refill requests
using drug pairs from the ISMP Confused Drug Names list; 40 LibriSpeech clips as
a control on my own measurement; 16 recordings of a real human voice reading the
hardest lines; and 24 clips of audio with no speech in it at all. Every speech
clip was re-rendered through four channels: clean, 8 kHz telephone, telephone
plus noise, and VoIP with echo. A number measured on clean audio is not
a number you can launch on.

I scored word error rate for comparability, but the launch decision rests on
entity accuracy: did it get the drug, the prescription number, the date of birth.
A transcript that is 12% wrong but has every entity right is fine. One that is 4%
wrong and turns hydroxyzine into hydralazine is a dispensing incident.

Total API cost: $0.93.

---

## What passed

**Prescription numbers: 100%.** 188 seven-digit numbers, every channel
condition, zero errors. This surprised me and it is the strongest result here.

**No fabrication: 0 of 24.** Hold music, dial tone, touch tones, line noise,
crosstalk, digital silence. Nova-3 returned nothing every time. Some systems
invent plausible sentences out of hold music, which on this line would mean a
refill request with no caller behind it. Nova-3 does not.

**Dates of birth: 96.2%.** Good enough, and failures are self-revealing: the
identity check fails and the caller is asked to repeat.

---

## What broke

**Drug names degrade with the channel.** 86.2% on telephone audio at baseline,
87.2% with keyterm prompting on, 80.9% once noise is added. Keyterm prompting
for your formulary is worth turning on, because it lifts telephone accuracy from 86.2%
to 93.6%. It does not get you to 98%.

**A partial keyterm list is worse than no list.** This is the finding I would
most want you to act on. I tested boosting only one member of each confusable
pair, which is what happens naturally if you list your top sellers. Accuracy on
the listed drugs rose 6.5 points, and accuracy on their unlisted partners fell
10.8 points, with 4.5% of them transcribed *as the listed drug*:

| spoken | transcribed as | times |
|---|---|---|
| cyclosporine | cycloserine | 5 |
| prednisolone | prednisone | 2 |
| amiodarone | amlodipine | 1 |

Every one of these ran from the unlisted drug toward the listed one. None went
the other way. With no keyterm list at all there were zero such errors. These
are the dangerous failures, because the output is a real, dispensable drug that
no downstream validation will reject. **If you use keyterm prompting, list the
entire formulary or none of it.**

**Requests arrive in pieces.** Deepgram finalizes a transcript segment after a
pause. On real recordings, 94% of requests were split at a 300 ms threshold and
88% were still split at 1000 ms. There is no setting that fixes this, because
people pause in the middle of reading a seven-digit number. The first segment
reads "I'd like a refill on hydralazine fifty milligrams" and the prescription
number is in the second.

---

## What to change before launch

1. **Read the drug name back and require confirmation.** The 98% floor assumes
   this exists; without it, 1 in 15 phone calls carries a wrong drug name into
   your queue.
2. **Buffer transcript segments; never act on one.** Accumulate consecutive
   finals and use Deepgram's `UtteranceEnd` event to decide a caller has
   finished. This is the fix for blocker 2 and it is integration work, not
   tuning.
3. **Turn on keyterm prompting with your complete formulary.** All of it.
4. **Route low-confidence drug names to a person.** Accept a lower automation
   rate rather than a wrong drug.
5. **Re-run this check on every model version.** The repository exits non-zero
   when a threshold regresses, so it belongs in your deployment pipeline.

---

## What to monitor after launch

- **Drug cross-confusion rate.** Target zero. Sample transcripts weekly against
  what was actually dispensed. This is the only metric here that can cause harm.
- **Confirmation-rejection rate.** How often callers correct the readback. A
  rise means accuracy is drifting before any error reaches a pharmacist.
- **Segments per caller turn.** If this climbs, your buffering has broken.
- **Escalation rate to a human.** Your real automation rate, and the number to
  watch when anyone proposes relaxing a threshold.
- **Accuracy by accent.** Baseline drug accuracy ranged from 97.2% to 82.1%
  across the accents I could synthesize. Your caller population is not mine;
  measure it on your own traffic.

---

## What this does not tell you

I had no real pharmacy call audio, because that is protected health information. My
real-voice set is 16 clips in one voice, which is enough to show that a problem
exists and not enough to size it. **Before full rollout, run this same harness
over consented recordings from your own line.** The thresholds I used are my
starting proposal; they encode a risk tolerance that is yours to set, not mine.

The synthetic portion of my corpus turned out to be *harder* than real speech on
word error rate and *easier* on fragmentation, so neither of its numbers predicts
production in the direction I assumed going in. That is the main reason the
real-voice set matters, and the main reason to repeat this on your own audio.
