Clips pulled for re-recording after dropout detection.

c06, c10, c16 contain runs of exact zeros that interrupt speech -- periodic at
roughly 450 ms, characteristic of the Continuity iPhone microphone link
dropping audio. c16 has five such gaps. A hole mid-word produces a recognition
error that belongs to the capture path rather than to the recognizer, which is
the kind of false error that would make Deepgram look worse than it is.

c13 was pulled for a different reason: only 20 ms of lead silence, with a
sustained (speech) onset at sample zero, meaning the discard window clipped
the first word.

Re-recorded on the built-in microphone instead, which has no wireless link.
