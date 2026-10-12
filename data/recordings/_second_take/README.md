Second take, partially superseded.

These clips were recorded with a 700 ms fixed head discard and the speak cue
shown 1.1 s after capture started. That assumed ffmpeg begins capturing
promptly, which it does not over a Continuity iPhone link -- device init can
exceed a second, so the discard window slid later in wall-clock time and
landed inside the first word rather than inside the keypress.

Symptoms: speech beginning at sample zero, clips 1-2.6 s shorter than the
first take, and a "head louder than body" reading that turned out to be a
clipped vowel rather than a click.

Replaced by a counted-in recorder whose countdown (4 s) is deliberately longer
than its discard (2 s), making the cut safe for any init delay up to 2 s.

Kept as evidence, not scored, not referenced by the manifest.
