import re
import os
import argparse
import datetime
import sys
from types import SimpleNamespace
import moviepy.editor as mp
from moviepy.audio.io.AudioFileClip import AudioFileClip

# Extracts audio from a video that matches the subtitle times, and puts silence between clips,
# Then pauses execution to run speech to text.
# Finally, takes the new .srt file(s), and adjusts the timing back to the original.


def interpolate(x1, y1, x2, y2, x):
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


# Convert a srt timestamp string to seconds
def time_to_seconds(time_str: str) -> float:
    h, m, s = re.split(':', time_str.replace(',', '.'))
    return float(h) * 3600 + float(m) * 60 + float(s)


# Convert seconds to the srt formatted time.
def format_time(elapsed_time):
    hours, remainder = divmod(elapsed_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02.0f}:{minutes:02.0f}:{seconds:06.3f}".replace('.', ',')


def read_subtitle_file(subtitle_file, _all):
    subtitle_timestamps = []
    with open(subtitle_file, 'r', encoding='utf8') as f:
        subtitles = f.read()
        for block in subtitles.strip().split('\n\n'):
            lines = block.strip().split('\n')
            start_time, end_time = lines[1].split(' --> ')
            text = '\n'.join(lines[2:]).strip()
            if not text or _all:
                line = SimpleNamespace(start=time_to_seconds(start_time),
                                       end=time_to_seconds(end_time),
                                       text=text)
                subtitle_timestamps.append(line)
    return subtitle_timestamps


def should_overwrite_file(filename):
    global overwrite
    if not overwrite and os.path.exists(filename):
        while True:
            response = input(f"The file '{filename}' already exists.\nDo you want to overwrite it? (y/n) ")
            if response.lower() == 'y':
                return True
            if response.lower() == 'n':
                return False
    else:
        return True


# Given an .srt file path, return the path of a matching video file with the same base name
def find_matching_video(srt_path):
    base_name = os.path.splitext(os.path.basename(srt_path))[0]
    # remove the language code if it exists.
    match = re.search(r'\.[a-zA-Z]{2,3}$', base_name)
    if match:
        base_name = base_name[:match.start()]
    path = os.path.dirname(srt_path)
    if not path:
        path = '.'
    for file_name in os.listdir(path):
        if os.path.basename(file_name).startswith(base_name) and os.path.splitext(file_name)[1] != '.srt':
            return os.path.join(os.path.dirname(srt_path), file_name)
    print("Could not find a video file that matches", srt_path, file=sys.stderr)
    exit(1)


# Given a video or audio file, find the matching .srt files
def find_subtitles(file):
    base_name = os.path.splitext(os.path.basename(file))[0]
    result = []
    for file_name in os.listdir(os.path.dirname(file)):
        if file_name.startswith(base_name) and file_name.endswith(".srt"):
            result.append(file_name.lstrip(base_name))
    return result


def extract_speech(srt_file, video_file, delay, redo):
    time_ranges = read_subtitle_file(srt_file, redo)
    try:
        video = mp.VideoFileClip(video_file)
        audio = video.audio
    except:
        # The file could be just an audio file
        audio = mp.AudioFileClip(video_file)
    print("Original duration: ", format_time(audio.duration))
    running_length = 0.0
    audio_clips = []
    for line in time_ranges:
        clip = audio.subclip(line.start, line.end)
        silence_duration = delay
        silence = mp.AudioClip(lambda _: 0, duration=silence_duration / 2.0, fps=audio.fps)
        if silence_duration:
            audio_clips.append(silence)
        audio_clips.append(clip)
        running_length += clip.duration + silence_duration
        if silence_duration:
            audio_clips.append(silence)
    return mp.concatenate_audioclips(audio_clips)


# Find the subtitle segment(s) that contains the clipped timestamp
def clipped_time_to_original_time(start, end, subtitle_timestamps, delay):
    half = (end - start) / 2
    pos = 0.0
    retval = []
    for i, s in enumerate(subtitle_timestamps):
        duration = s.end - s.start
        duration += delay
        clip_end = pos + duration
        if min(end, clip_end) - max(start, pos) >= min(half, duration / 2):
            retval.append(i)
        pos += duration
    return retval


def find_clips(start, end, subtitle_timestamps):
    pos = []
    half = (end - start) / 2
    for i, s in enumerate(subtitle_timestamps):
        if min(end, s.end) - max(start, s.start) >= min(half, (s.end - s.start) / 2):
            pos.append(i)
    return pos


# writes a new subtitle file with the original video timing
def write_new_subs(subtitle_file_clipped, subtitle_file_original, _output_file, delay, redo, _force,
                   adjust, use_original_time):
    subtitle_timestamps_original_clipped = read_subtitle_file(subtitle_file_original, redo)
    subtitle_timestamps_clipped = read_subtitle_file(subtitle_file_clipped, True)

    # There might be multiple subtitles for a single original subtitle.  So we do some extra work
    # so that the multiple lines will fit exactly into the original segment.
    # also handle cases where whisper response spans multiple segments.
    blocks = {}
    # get a list of subtitles for each segment
    for _subtitle in subtitle_timestamps_clipped:
        start = _subtitle.start * adjust
        end = _subtitle.end * adjust
        if use_original_time:
            pos = find_clips(start, end, subtitle_timestamps_original_clipped)
        else:
            pos = clipped_time_to_original_time(start, end, subtitle_timestamps_original_clipped, delay)
        start = subtitle_timestamps_original_clipped[pos[0]].start
        _subtitle.span = len(pos)
        if start not in blocks:
            blocks.update({start: []})
        if _force:
            if blocks[start]:
                line = blocks[start][0][0]
                if line.text:
                    line.text += '\n'
                line.text += _subtitle.text
            else:
                blocks[start].append(_subtitle)
        else:
            blocks[start].append(_subtitle)

    # Write a new subtitle file with the adjusted timing.
    if not should_overwrite_file(_output_file):
        print("Skipping", _output_file)
        return

    # Reload it including already populated values.
    subtitle_timestamps_original = read_subtitle_file(subtitle_file_original, True)
    with open(_output_file, 'w', encoding='utf8') as f:
        if subtitle_timestamps_original[0].start == 0 and "Whisper (AI)" in subtitle_timestamps_original[0].text:
            subtitle_timestamps_original = subtitle_timestamps_original[1:]
        i = 1
        line = f'{i}\n{format_time(0)} --> {format_time(0)}\nWhisper (AI) derived text on {datetime.date.today()}.\n\n'
        f.write(line)
        skip_next = 0
        for j, original in enumerate(subtitle_timestamps_original):
            if original.start in blocks:
                _list = blocks[original.start]
                # If there's multiple subtitles in a block, adjust the timings to match the original block.
                _min = _list[0].start
                _max = _list[-1].end
                span = _list[0].span
                original_end = subtitle_timestamps_original[j + span - 1].end
                if span > 1:
                    skip_next = span
                for sub in _list:
                    i += 1
                    start = interpolate(_min, original.start, _max, original_end, sub.start)
                    end = interpolate(_min, original.start, _max, original_end, sub.end)
                    line = f'{i}\n{format_time(start)} --> {format_time(end)}\n{sub.text}\n\n'
                    f.write(line)
            else:
                if skip_next == 0:
                    i += 1
                    # Write the original blank line if there wasn't a match found
                    line = f'{i}\n{format_time(original.start)} --> {format_time(original.end)}\n{original.text}\n\n'
                    f.write(line)
            if skip_next > 0:
                skip_next -= 1
    print(f'created {_output_file}')


def main():
    global overwrite
    parser = argparse.ArgumentParser(description="""
    Whisper preprocessing tool. 
    This gets around an issue where Whisper doesn't deal well with long periods without speaking.  Also, if there's 
    music or sound effects, Whisper gets confused on when speech starts.  To use this, create a srt subtitle file, with 
    subtitles defined only where there is talking.  You can either carefully do the timing to match where you want the 
    breaks, or else do longer sections and let Whisper determine where the breaks should be. If there is music or 
    background noise, you may find that Whisper does better if the clips are quite small and contain only the bit there
    is talking. Sections without much background noise can usually be one long clip since those sections are easier for 
    Whisper to get right without help. The text of the subtitles can be left blank. This outputs an audio file with 
    just the spoken parts that can be transcribed or translated with Whisper or other speech to text programs. Finally, 
    the resulting srt file will be converted back to the original video timing. For best results use in conjunction 
    with "--word_timestamps True" on the Whisper command. 
                                    """)
    parser.add_argument('subtitle_file', type=str, help='path to existing (dummy) subtitle file')
    parser.add_argument('-d', '--delay', type=float, default=1.2,
                        help='delay between clips in seconds (defaults to 1.2 seconds)')
    parser.add_argument('-t', '--audio-file', type=str, default="clip.flac",
                        help="The temporary audio file to create. It defaults to clip.flac. It can be a wav, mp3, or "
                             "flac")
    parser.add_argument('-r', '--redo', action="store_true", help="Uses lines even if there is already text in "
                                                                  "the subtitle. Otherwise only blank lines are used")
    parser.add_argument('-f', '--force', action="store_true", help="If there are multiple subtitles for a single line, "
                                                                   "concatenate them together instead of creating "
                                                                   "separate lines")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-e', '--extract', action='store_true',
                       help='Extracts audio from a video that matches the subtitle times, separated by silence '
                            'between clips, and saves it to the temporary file')
    group.add_argument('-s', '--subtitles', action='store_true',
                       help='Takes the new .srt file(s), and adjusts them back to the original timing')
    group.add_argument('-b', '--both', action='store_true',
                       help='Runs the extract option, pauses, then continues with the subtitles. '
                            'This is the default action'),
    parser.add_argument('--sync', type=str, help="This feature will take a subtitle file that is -roughly in sync "
                                                       "and force the subtitle times to match with the source subtitle "
                                                       "the result will overwrite the source"),
    parser.add_argument('-y', '-y', '--overwrite', action='store_true', help='Automatically overwrite files that already '
                                                                       'exist')
    args = parser.parse_args()

    input_srt = args.subtitle_file
    temp_file = args.audio_file
    input_video = find_matching_video(input_srt)
    temp_file_base = os.path.splitext(temp_file)[0]
    overwrite = args.overwrite
    both = args.both or (not args.extract and not args.subtitles)
    print(f"Processing {input_srt}...")
    print(f'Video file: {input_video}')
    print(f'Audio file: {temp_file}')
    print(f'Silence between clips: {args.delay} seconds')

    audio_clip = extract_speech(input_srt, input_video, args.delay, args.redo)
    print("Clipped duration : ", format_time(audio_clip.duration))
    if args.extract or both:
        if not should_overwrite_file(temp_file):
            print("not overwriting the audio file:", temp_file)
        else:
            if temp_file.endswith(".flac"):
                # convert to mono, then flac which is lossless
                audio_clip.write_audiofile(temp_file, codec="flac", ffmpeg_params=["-ac", "1"])
            elif temp_file.endswith(".mp3"):
                # the param specifies CBR encoding, which keeps the timing accurate
                audio_clip.write_audiofile(temp_file, ffmpeg_params=["-b:a", "160k", "-ac", "1"])
            else:
                audio_clip.write_audiofile(temp_file, ffmpeg_params=["-ac", "1"])

        print(f"The File '{temp_file}' is ready. Now create one or more {temp_file_base}*.srt "
              f"and put them in the directory with the clip")
    if both:
        input("Press Enter when ready to create the adjusted subtitle files")
        print()
    adjust = 1
    if args.subtitles or both:
        base_path = os.path.splitext(temp_file)[0]
        for subtitle in find_subtitles(os.path.abspath(temp_file)):
            output_file = os.path.splitext(input_video)[0] + subtitle
            actual_duration = AudioFileClip(temp_file).duration
            adjust = audio_clip.duration / actual_duration
            if abs(audio_clip.duration - actual_duration) > 1:
                # Depending on the audio codec, the actual audio length might be slightly off.  If so, this
                # will compensate for it.
                print(f"Audio file length is off. length is {format_time(actual_duration)}, "
                      f"expecting {format_time(audio_clip.duration)}, adjusting by {adjust}")
            write_new_subs(base_path + subtitle, input_srt, output_file, args.delay, args.redo, args.force, adjust,
                           False)
    if args.sync:
        write_new_subs(args.sync, input_srt, args.sync, args.delay, args.redo, args.force, adjust, True)


if __name__ == '__main__':
    main()
