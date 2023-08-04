import copy
import re
import logging
from asyncio import Event
import random

import discord
import pytube
from discord import Intents
from discord.ext import commands
from pytube import YouTube, Playlist
from pytube.exceptions import RegexMatchError
from youtubesearchpython import VideosSearch
import asyncio
from moviepy.editor import AudioFileClip

# Bot token and prefix
DS_TOKEN = 'YOUR_TOKEN_HERE'
PREFIX = '!'

intents = Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(intents=intents, command_prefix=PREFIX)

track_queue = []

stop_event = Event()

FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10', 'options': '-vn'}

writers = 0

logger = logging.getLogger('discord')
logger.setLevel(logging.WARNING)  # Set the desired log level

# Create a file handler and set its log level
file_handler = logging.FileHandler(filename='bot.log', encoding='utf-8', mode='w')
file_handler.setLevel(logging.DEBUG)  # Set the desired log level for the file

# Create a formatter for the log messages
formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')

# Set the formatter for the file handler
file_handler.setFormatter(formatter)

# Add the file handler to the logger
logger.addHandler(file_handler)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')


def get_timecode(timecode):
    if re.match(r'\d+:\d+:\d+', timecode):
        split = timecode.split(':')
        start_time = int(split[0]) * 3600 + int(split[1]) * 60 + int(split[2])
    elif re.match(r'\d+:\d+', timecode):
        split = timecode.split(':')
        start_time = int(split[0]) * 60 + int(split[1])
    else:
        raise ValueError('Invalid timecode')
    return start_time


def get_track_from_youtube(url, timecode, depth=0):
    if depth > 10:
        raise ValueError('Invalid URL.')
    try:
        url = url.replace('**', '')
        if not url.startswith('https://'):
            videos_search = VideosSearch(url, limit=5)
            video_url = videos_search.result()['result'][0]['link']
            yt = YouTube(video_url)
        else:
            yt = YouTube(url)
        audio_stream = yt.streams.filter(only_audio=True).get_by_itag(251)
    except RegexMatchError as e:
        print(e)
        get_track_from_youtube(url, timecode, depth + 1)
        return
    except pytube.exceptions.VideoUnavailable as e:
        raise ValueError('Video unavailable.')

    start_time = get_timecode(timecode)
    if start_time > yt.length:
        raise ValueError('Timecode exceeds video duration.')
    track_queue.append((f'{yt.author} - {yt.title}', audio_stream.url, start_time))

    return f'{yt.author} - {yt.title}'


async def get_track(func, ctx, url, timecode):
    global writers
    try:
        track_name = func(url, timecode)
        await ctx.send(f'Added **{track_name}** to queue')
        writers -= 1
        if len(track_queue) == 1:
            await play_audio(ctx)
    except ValueError as e:
        writers -= 1
        await ctx.send(e.args[0])


async def get_playlist_from_youtube(ctx, url):
    playlist = Playlist(url)
    i = 0
    for video_url in playlist.video_urls:
        i += 1
        if stop_event.is_set():
            break
        try:
            get_track_from_youtube(video_url, '00:00:00')
        except ValueError as e:
            await ctx.send(e.args[0][:-1] + f' on pos {i}. Possibly age restricted')
            continue
        if len(track_queue) == 1:
            await play_audio_dont_wait(ctx)
    return len(playlist.video_urls)


async def get_playlist(func, ctx, url):
    global writers
    is_empty = len(track_queue) == 0
    await ctx.send('Adding playlist to queue... Please wait.')
    length = await func(ctx, url)
    if not stop_event.is_set():
        await ctx.send(f'Added **{length}** tracks to queue')
    else:
        stop_event.clear()
        writers = 0
        return
    writers = 0
    while ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        await asyncio.sleep(1)
    if is_empty and track_queue:
        track_queue.pop(0)
    if not ctx.voice_client.is_playing():
        await play_audio(ctx)


@bot.command()
async def play(ctx, url='', timecode='00:00:00'):
    global writers
    while writers > 0:
        await asyncio.sleep(1)
    writers += 1
    if ctx.author.voice is None:
        await ctx.send("You are not in a voice channel.")
        return
    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.send("Im busy rn.")
        return
    if "/playlist" not in url:
        await get_track(get_track_from_youtube, ctx, url, timecode)
    else:
        if "youtube" in url or "youtu.be" in url:
            await get_playlist(get_playlist_from_youtube, ctx, url)
        else:
            await ctx.send("Invalid URL")


async def play_audio_dont_wait(ctx, is_move=False):
    temp_ffmpeg_options = FFMPEG_OPTIONS.copy()
    temp_ffmpeg_options['before_options'] += f' -ss {track_queue[0][2]}'
    ctx.voice_client.play(discord.FFmpegPCMAudio(source=copy.copy(track_queue[0][1]),
                                                 **temp_ffmpeg_options))
    if ctx is not None:
        if is_move:
            await ctx.send(f"Moved to **{track_queue[0][0]}**")
        else:
            await ctx.send(f"Playing **{track_queue[0][0]}**")


async def play_audio_non_recursive(ctx):
    while writers > 0:
        while writers > 0:
            await asyncio.sleep(1)
        await asyncio.sleep(1)
    await play_audio_dont_wait(ctx)
    while ctx.voice_client is not None and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await asyncio.sleep(1)
    while writers > 0:
        while writers > 0:
            await asyncio.sleep(1)
        await asyncio.sleep(1)
    if track_queue:
        track_queue.pop(0)


async def play_audio(ctx):
    if ctx.voice_client is None or not ctx.voice_client.is_connected():
        await ctx.author.voice.channel.connect()
    if len(track_queue) > 0:
        await play_audio_non_recursive(ctx)
        await play_audio(ctx)
    else:
        await asyncio.sleep(600)
        while writers > 0:
            while writers > 0:
                await asyncio.sleep(1)
            await asyncio.sleep(1)
        if len(track_queue) == 0 and ctx.voice_client is not None:
            await ctx.voice_client.disconnect()


@bot.command()
async def queue(ctx):
    while writers > 0:
        while writers > 0:
            await asyncio.sleep(1)
        await asyncio.sleep(1)
    if len(track_queue) == 0:
        await ctx.send("Queue is empty.")
        return

    res = f"Queue:\n1. **{track_queue[0][0]}** (playing)\n"
    for i in range(1, len(track_queue)):
        res += f"{i + 1}. **{track_queue[i][0]}**\n"

    await ctx.send(res)


async def skip_track(ctx, pos, is_playlist=False, start=0, end=0):
    global writers
    if len(track_queue) < pos or pos < 1:
        await ctx.send("Invalid queue position.")
        return
    if pos == 1 and ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    if not is_playlist:
        await ctx.send(f'Removed **{track_queue[pos - 1][0]}** (pos {pos}) from queue')

    track_queue.pop(pos - 1)

    if is_playlist and pos == 1:
        await ctx.send(f'Removed tracks from position {start} to {end} from queue')

    if pos == 1 and len(track_queue) > 0 and ctx.voice_client.is_connected():
        writers -= 1
        await play_audio(ctx)


@bot.command()
async def skip(ctx, pos='1'):
    global writers
    if not track_queue:
        await ctx.send("Queue is empty.")
        return
    else:
        while writers > 0:
            print("waiting")
            await asyncio.sleep(1)
        writers += 1
        try:
            num = int(pos)
            await skip_track(ctx, num)
        except ValueError:
            if re.match(r'^\d+:\d+$', pos) or re.match(r'^\d+:$', pos) or re.match(r'^:\d+$', pos):
                pos = pos.split(':')
                if pos[0]:
                    start = int(pos[0])
                else:
                    start = 1
                if pos[1]:
                    end = int(pos[1])
                else:
                    end = len(track_queue)
                if start >= end or start < 1 or end > len(track_queue):
                    await ctx.send("Invalid queue position.")
                    writers -= 1
                    return
                if start == 1:
                    ctx.voice_client.stop()
                for i in range(start + 1, end + 1):
                    await skip_track(ctx, i, True, start, end)
                await skip_track(ctx, start, True, start, end)
                if start != 1:
                    await ctx.send(f'Removed tracks from position {start} to {end} from queue')
            else:
                await ctx.send("Invalid command argument.")
    if writers > 0:
        writers -= 1


@bot.event
async def on_voice_state_update(member, before, after):
    global writers
    if member == bot.user:
        if before.channel is not None and after.channel is None:
            i = 0
            while writers > 0 and i < 300:
                await asyncio.sleep(1)
                i += 1
            track_queue.clear()
            writers = 0


@bot.command()
async def stop(ctx):
    global writers
    stop_event.set()
    if ctx.voice_client is not None:
        ctx.voice_client.stop()
    track_queue.clear()
    writers = 0
    await ctx.send('Stopped playing')


@bot.command()
async def pause(ctx):
    ctx.voice_client.pause()
    await ctx.send('Paused playing')


@bot.command()
async def resume(ctx):
    ctx.voice_client.resume()
    await ctx.send('Resumed playing')


@bot.command()
async def shuffle(ctx, start=2, end=-3463346):
    global writers
    while writers > 0:
        await asyncio.sleep(1)
    writers += 1
    if end == -3463346:
        end = len(track_queue)
    if start >= end or start < 2 or end > len(track_queue):
        await ctx.send("Invalid queue position.")
        return
    slice_copy = track_queue[start - 1:end].copy()
    random.shuffle(slice_copy)
    track_queue[start - 1:end] = slice_copy
    writers -= 1
    await ctx.send(f'Shuffled tracks from position {start} to {end}')


@bot.command()
async def moveto(ctx, timecode):
    global writers
    while writers > 0:
        await asyncio.sleep(1)
    writers += 1
    if not ctx.voice_client.is_playing():
        await ctx.send("Nothing is playing.")
        writers -= 1
        return
    try:
        seconds = get_timecode(timecode)
    except ValueError:
        await ctx.send("Invalid timecode.")
        writers -= 1
        return
    audio_clip = AudioFileClip(track_queue[0][1])
    if audio_clip.duration < seconds:
        await ctx.send("Invalid timecode.")
        writers -= 1
        return
    ctx.voice_client.stop()
    temp = (track_queue[0][0], track_queue[0][1], seconds)
    track_queue[0] = temp
    await play_audio_dont_wait(ctx)
    writers -= 1


@bot.command()
async def clearlock(ctx):
    global writers
    stop_event.set()
    writers = 0


@bot.command()
async def commands(ctx):
    await ctx.send("Commands:\n" +
                   "!play <youtube link> (optional: <timecode (format hh:mm:ss or mm:ss)>) - adds a song or playlist to the queue\n" +
                   "!queue - shows the queue\n" +
                   "!skip (optional: <pos>) - skips the song at position <pos> in the queue\n" +
                   "!skip <start>:<end> - skips the songs from position <start> to <end> in the queue\n" +
                   "!stop - stops playing and clears the queue\n" +
                   "!pause - pauses playing\n" +
                   "!resume - resumes playing\n" +
                   "!shuffle <start>:<end> - shuffles the songs from position <start> to <end> in the queue\n " +
                   "!moveto <timecode (format hh:mm:ss or mm:ss)> - moves the current song to the specified timecode\n" +
                   "!clearlock - clears the lock on the queue in case something gone wrong\n")


bot.run(DS_TOKEN)
