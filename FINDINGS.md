# Findings, in plain language

A pharmacy wants a phone line that takes prescription refill requests. This
repository is the apparatus for deciding whether Deepgram's speech recognition
is good enough to put behind it. These are the results, written for someone who
does not work on speech systems.

The technical write-up is in [`reports/launch_readiness.md`](reports/launch_readiness.md)
and the customer-facing version is in [`reports/MEMO.md`](reports/MEMO.md).

---

## The one-sentence version

Deepgram's recognition is good enough for this job, but two things about *how
you connect it* would have caused real harm — and I only found them because I
checked my own test equipment as carefully as I checked Deepgram.

---

## What I found about Deepgram

### 1. A feature meant to help made things more dangerous when used halfway

Deepgram lets you hand it a list of words to listen out for, which is useful for
drug names. Give it the whole list of 50 drugs and everything improves with no
new errors. Give it *half* the list — exactly what a real pharmacy would do,
listing their best sellers — and the drugs left off started being heard as the
drugs that were on it.

Eight times, a patient said one medicine and the transcript named a different
real medicine. "Prednisolone" became "prednisone". "Cyclosporine" became
"cycloserine".

These are the worst kind of mistake, because the answer looks completely normal.
A garbled word gets noticed; a different real medicine does not. Nothing
downstream catches it.

With no list at all this never happened once. **A partial list is worse than no
list.** A conventional test would have scored that setup as a 6-point
improvement and shipped it.

### 2. The system hands over half a sentence, and half looks like the whole thing

People pause when reading out a seven-digit number. Deepgram treats a pause as
"they have finished talking", so it delivers the sentence in pieces. On real
recordings this happened to **88%** of requests.

The first piece says *"I'd like a refill on hydralazine, fifty milligrams."* The
prescription number is in the second piece. A phone system acting on the first
piece has a medicine and no prescription — which reads as a complete, sensible
request.

No setting fixes this. It has to be solved in how the phone system is built.

### 3. It never makes things up — not once

24 clips containing no speech at all: silence, hold music, dial tone, keypad
beeps, line hiss, background chatter. It returned nothing every single time.
Some systems invent plausible sentences out of hold music, which here would mean
a refill request with no patient behind it. This is a real strength and worth
stating plainly.

### 4. Numbers are perfect; names are the risk

188 prescription numbers, zero mistakes, on every quality of phone line. Drug
names ran 86% to 94% depending on settings. Digits are solid; sound-alike words
are where the danger sits.

---

## What I found about testing itself — the more valuable half

### 5. My synthetic voices misled me, in both directions

Most of the corpus used computer-generated voices. I assumed that would make
things look *easier* than reality. It was wrong both ways:

- Computer voices produced **more** word errors than a real person.
- Computer voices produced **far fewer** interrupted sentences — 38% against 94%.

Using only computer voices would have reported the interruption problem as
roughly three times smaller than it is. Sixteen recordings of one real human
voice are what caught it.

### 6. My own test rig was quietly broken, in ways that would have blamed Deepgram

- The phone microphone was dropping slivers of audio mid-word. Those would have
  appeared in the results as Deepgram mishearing things. It was the microphone.
- The key pressed to start each recording was louder than the speech, and the
  first attempt to remove it cut off the first word instead.
- "How fast does it respond" was measured three different ways before one meant
  anything. The first returned *negative three seconds*. The second was
  accidentally timing the moment the test program hung up.

Every one of those would have made Deepgram look worse than it is.

---

## The point

Deepgram came out of this looking good. Most of what nearly went wrong was the
measurement, not the product.

An evaluation that has not been tested against its own data does not measure the
thing being evaluated. It measures the test equipment and reports the result as
the vendor's fault.
