# Tier C -- real recordings

Tier C is the credibility anchor of the corpus. Tiers A and D are synthetic and
tier B is studio-read audiobook speech; none of them is a real person saying a
real drug name into a real phone. Twenty clips here change the claim from
"tested on synthetic audio" to "tested on real speech with domain vocabulary,
including a non-native accent over a narrowband channel" -- which is both the
hardest realistic condition and the most likely place to find a genuine defect.

## How to record

1. Phone voice memo app is fine. Quiet room, phone held normally -- do NOT
   use a headset or try for studio quality. The degradation chain adds the
   channel effects; what is wanted here is a natural speaking voice.
2. Read each line below at a normal pace. Do not over-enunciate the drug
   names: the point is to find out what happens when a caller says them the
   way callers actually do.
3. One clip per line. Save as `c01.m4a`, `c02.m4a`, ... in this directory.
4. For each clip, save a matching `c01.txt` containing exactly what you said.
   A clip without a `.txt` is skipped by the builder -- an unlabelled clip
   cannot be scored.

`scripts/make_tier_c_script.py` generates the lines and pre-writes every
`.txt` for you, so you only record audio.

## Why the transcript must match what you actually said

If you misspeak and the `.txt` says something else, that becomes a false
error attributed to Deepgram. Re-record, or edit the `.txt` to match. The
harness has no way to tell a reader error from a recognizer error.
