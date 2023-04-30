I'm working on a program that aids in using Whisper to create subtitles. It is a command line program written in
Python. There's two problems I'm trying to solve:
1:  The timings of Whisper's subtitles are not very accurate, especially when there is music or background noises
2:  Long periods of no speech can confuse Whisper.

Here's my process:

I first use Subtitle Edit to create a dummy subtitle file to indicate where there is speech. I can either
try to get the timing just right for the final captions, or else I can make a longer subtitle entry, and let Whisper
break up the text as it sees fit. I leave the text blank.  If there's music or background noise, then I try to get
the clips accurate. Otherwise I usually switch lines when there is a change of speaker.

Next, I run a program that takes the video and dummy subtitle file, and creates an audio file with just the part of
the movie that has speech (where there are subtitles entries). It puts between .75 and 1.25 seconds of silence between
clips, so the clips generally change on the even seconds mark (because that is where Whisper likes to break subtitles).

Then, I run Whisper using the Google collab process where the Google servers do the translation.  I run it twice,
once to get to get the English, and another run to get the original language.

Next, the program takes the clipped subtitles and converts the timing back to the original. If there
are multiple lines that Whisper made for a single entry, the durations will be adjusted so they start and stop at
the original entry.  It does this for both the translated version and the original.

There may be sections where the timing got off, so the the subtitles end up in the wrong stop. You can
either just adjust them, or else bring up the clipped file in Subtitle Edit, and move the entries that are off by
looking at the waveform. It doesn't have to be perfect since if the middle of the subtitle is in the right block it will
end up in the right place.  But generally, there aren't many bad placements.

So after doing this you have an original language and English subtitle files, which should pretty closely agree with
each other, with accurate timings.  There may be empty translations, which means those sections weren't
understood (or they got mistakenly put a line early or late).

By default, it only processes lines that are blank, so that way if you missed dialog, or want to redo a section,
you can add new blank lines to the subtitle file, and the program will only process those lines.  Then it will
incorporate the new whisper text into the working subtitle file.

