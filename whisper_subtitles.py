import argparse
import datetime
import os
import re
import sys
from types import SimpleNamespace
import moviepy.editor as mp
from moviepy.audio.io.AudioFileClip import AudioFileClip


# Extracts audio from a video that matches the subtitle times, and puts a 1 second of silence between clips,
# Then pauses execution to run speech to text.
# Finally, takes the new .srt file(s), and adjusts them back to the original timing.


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
        response = input(f"The file '{filename}' already exists.\nDo you want to overwrite it? (y/n) ")
        return response.lower() == 'y'
    else:
        return True


# Given an .srt file path, return the path of a matching video file with the same base name
def find_matching_video(srt_path):
    base_name = os.path.splitext(os.path.basename(srt_path))[0]
    # remove the language code if it exists.
    match = re.search(r'\.[a-zA-Z]{2,3}$', base_name)
    if match:
        base_name = base_name[:match.start()]
    for file_name in os.listdir(os.path.dirname(srt_path)):
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


# calculates the delay to use, trying to get as close to ending on a 2 second
# mark, but staying within the max & min delays. Whisper large model usually reports
# times in 2 second intervals, so this helps it to agree with the clips.
def calc_delay(min_delay, max_delay, running_time, duration):
    avg_delay = (min_delay + max_delay) / 2
    max_offset = (max_delay - min_delay) / 2
    running_time += duration + avg_delay
    fraction = running_time % 2
    if fraction >= 1:
        fraction -= 2
    return avg_delay + -min(fraction, max_offset)


def extract_speech(srt_file, video_file, min_delay, max_delay, redo):
    time_ranges = read_subtitle_file(srt_file, redo)
    video = mp.VideoFileClip(video_file)
    audio = video.audio

    running_length = 0.0
    audio_clips = []
    for line in time_ranges:
        clip = audio.subclip(line.start, line.end)
        silence_duration = calc_delay(min_delay, max_delay, running_length, clip.duration)
        silence = mp.AudioClip(lambda _: 0, duration=silence_duration / 2.0, fps=audio.fps)
        if silence_duration:
            audio_clips.append(silence)
        audio_clips.append(clip)
        running_length += clip.duration + silence_duration
        if silence_duration:
            audio_clips.append(silence)
    return mp.concatenate_audioclips(audio_clips)


# Find the subtitle segment that contains the clipped timestamp
def clipped_time_to_original_time(clipped_time, subtitle_timestamps, min_delay, max_delay):
    pos = 0.0
    i = 0
    for i, s in enumerate(subtitle_timestamps):
        duration = s.end - s.start
        duration += calc_delay(min_delay, max_delay, pos, duration)
        pos += duration
        if pos >= clipped_time:
            return i
    return -1


# writes a new subtitle file with the original video timing
def write_new_subs(subtitle_file_clipped, subtitle_file_original, _output_file, min_delay, max_delay, redo, _force,
                   adjust):
    subtitle_timestamps_original = read_subtitle_file(subtitle_file_original, redo)
    subtitle_timestamps_clipped = read_subtitle_file(subtitle_file_clipped, True)

    # There might be multiple subtitles for a single original subtitle.  So we do some extra work
    # so that the multiple lines will fit exactly into the original segment..
    blocks = {}
    # get a list of subtitles for each segment
    for _subtitle in subtitle_timestamps_clipped:
        middle = (_subtitle.start + _subtitle.end) / 2 * adjust
        pos = clipped_time_to_original_time(middle, subtitle_timestamps_original, min_delay, max_delay)
        start = subtitle_timestamps_original[pos].start
        if start not in blocks:
            blocks.update({start: []})
        if _force:
            if blocks[start]:
                line = blocks[start][0]
                if line.text:
                    line.text += '\n'
                line += _subtitle.text
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
        line = f'{i}\n{format_time(0)} --> {format_time(2)}\nWhisper (AI) derived text on {datetime.date.today()}\n\n'
        f.write(line)

        for original in subtitle_timestamps_original:
            if original.start in blocks:
                _list = blocks[original.start]
                # If there's multiple subtitles in a block, adjust the timings to match the original block.
                _min = _list[0].start
                _max = _list[-1].end
                for sub in _list:
                    i += 1
                    start = interpolate(_min, original.start, _max, original.end, sub.start)
                    end = interpolate(_min, original.start, _max, original.end, sub.end)
                    line = f'{i}\n{format_time(start)} --> {format_time(end)}\n{sub.text}\n\n'
                    f.write(line)
            else:
                i += 1
                # Write the original blank line if there wasn't a match found
                line = f'{i}\n{format_time(original.start)} --> {format_time(original.end)}\n{original.text}\n\n'
                f.write(line)
    print(f'created {_output_file}')


def main():
    global overwrite
    parser = argparse.ArgumentParser(description="""
    Whisper preprocessing tool. 
    This gets around an issue where Whisper doesn't deal well with long periods without speaking.  Also, if there's music 
    or sound effects, Whisper gets confused on when speech starts.  To use this, create a srt subtitle file, with subtitles 
    defined only where there is talking.  You can either carefully do the timing to match where you want the breaks, or 
    else do longer sections and let Whisper determine where the breaks should be. If there is music or background noise, 
    you may find that Whisper does better if the clips are quite small and contain only the bit there is talking.  
    Sections without much background noise can usually be one long clip since those sections are easier for Whisper to get 
    right without help. The text of the subtitles can be left blank. This outputs a mp3 file with just the spoken parts that
    can be transcribed or translated with Whisper or other speech to text programs. Finally, the resulting srt file will 
    be converted back to the original video timing.
                                    """)
    parser.add_argument('subtitle_file', type=str, help='path to existing (dummy) subtitle file')
    parser.add_argument('-min', type=float, default=.75,
                        help='minimum delay between clips in seconds (defaults to .75 second)')
    parser.add_argument('-max', type=float, default=1.25,
                        help='maximum delay between clips in seconds (defaults to 1.25 second)')

    parser.add_argument('-t', '--audio-file', type=str, default="clip.mp3",
                        help="The temporary audio file to create. It defaults to clip.mp3. It can be a wav or mp3")
    parser.add_argument('-r', '--redo', action="store_true", help="Uses lines even if there is already text in "
                                                                  "the subtitle. Otherwise only blank lines are used")
    parser.add_argument('-f' '--force', action="store_true", help="If there are multiple subtitles for a single line, "
                                                                  "concatenate them together instead of creating separate "
                                                                  "lines")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-e', '--extract', action='store_true',
                       help='Extracts audio from a video that matches the subtitle times, separated by silence '
                            'between clips, and saves it to the temporary file')
    group.add_argument('-s', '--subtitles', action='store_true',
                       help='Takes the new .srt file(s), and adjusts them back to the original timing')
    group.add_argument('-b', '--both', action='store_true',
                       help='Runs the extract option, pauses, then continues with the subtitles. '
                            'This is the default action')
    group.add_argument('-f', '--force', action='store_true',
                       help='This will indicate that clips should be concatenated together if Whisper generates '
                            'multiple subtitles for a single source line. Normally multiple lines are used.')
    group.add_argument('-y', '--overwrite', action='store_true', help='Automatically overwrite files that already '
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
    print(f'Silence between clips: {args.min} - {args.max} seconds')

    audio_clip = extract_speech(input_srt, input_video, args.min, args.max, args.redo)
    print("Clipped Duration : ", format_time(audio_clip.duration))
    if args.extract or both:
        if not should_overwrite_file(temp_file):
            print("not overwriting the audio file:", temp_file)
        else:
            # the param specifies CBR encoding, which keeps the timing accurate
            audio_clip.write_audiofile(temp_file, ffmpeg_params=["-b:a", "160k"])
        print(f"The File '{temp_file}' is ready. Now create one or more {temp_file_base}*.srt "
              f"and put them in the directory with the clip")
    if both:
        input("Press Enter when ready to create the adjusted subtitle files")
        print()
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
            write_new_subs(base_path + subtitle, input_srt, output_file, args.min, args.max, args.redo, args.force, adjust)


if __name__ == '__main__':
    main()
