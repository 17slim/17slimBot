import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()
DISCORD_TOKEN = os.getenv('discord_token')
OWNER_ID = os.getenv('owner_id')
GUILD_ID = os.getenv('guild_id')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

music_queue = []
current_voice_client = None
current_track = None

ytdl_options = {
    'format': 'bestaudio/best',
    'noplaylist': 'True',
    'quiet': True,
    'extractorargs': 'youtube:player_skip=webpage,configs',
}

ytdl = youtube_dl.YoutubeDL(ytdl_options)

def sec_to_time(s):
    s = int(s)
    hours, remainder = divmod(s, 3600)
    minutes, seconds = divmod(remainder, 60)

    hours = f'{hours}:' if hours > 0 else ''
    return f'{hours}{minutes:02}:{seconds:02}'

def format_playing():
    title = current_track['title']
    duration = current_track['duration']
    duration = sec_to_time(duration)
    url = current_track['webpage_url']
    progress = current_track['source'].check_time() + current_track['start_timestamp']
    progress = sec_to_time(progress)
    return f'[{title}]({url}) *({progress}/{duration})*'

def progress_bar():
    duration = float(current_track['duration'])
    progress = float(current_track['source'].check_time() + current_track['start_timestamp'])
    fraction = progress/duration
    tmplt = '▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬'
    btn = ':radio_button:'
    idx = int(len(tmplt) * fraction)
    return tmplt[:idx] + btn + tmplt[idx:]

def format_queue():
    return '\n'.join([f"{idx + 1}. [{video['title']}]({video['webpage_url']}) *({sec_to_time(video['duration'])})*"
        for idx, video in enumerate(music_queue)])

def time_from_url(url):
    if not url or not isinstance(url, str):
        return 0
    url = url.strip()
    if 'http' != url[:4].lower():
        return 0
    t = 0
    if len(sp := url.replace('?','&').split('&')) > 1:
        for pair in [s.split('=') for s in sp[1:]]:
            if len(pair) == 2:
                k,v = pair
                if k == 't':
                    t = int(v.replace('s',''))
    return t

def fix_url(url, ts):
    url = url if '?' in url else url + f"?t={ts}s"
    url = url if 't=' in url else url + f"&t={ts}s"
    return url

async def search_youtube(query):
    t = time_from_url(query)
    search_query = f'ytsearch1:{query}'

    result = None
    try:
        result = ytdl.extract_info(query, download=False)  # fails if not valid URL
    except Exception as e:
        result = ytdl.extract_info(search_query, download=False)  # should always succeed; runs "search"

    video = None
    if 'entries' in result and len(result['entries']) > 0:
        video = result['entries'][0]
    elif ('title' in result) and ('webpage_url' in result) and ('duration' in result):
        video = result
    else:
        return None

    video['start_timestamp'] = t
    return video

class Audio(discord.FFmpegPCMAudio):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.counter = 0

    def read(self):
        self.counter += 20
        return super().read()

    def check_time(self):
        return self.counter / 1000

async def play_next(ctx):
    global current_voice_client
    global current_track
    if music_queue:
        video = music_queue.pop(0)
        start_time = video['start_timestamp']
        audio_source = Audio(video['url'], before_options=f'-ss {start_time} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
        current_voice_client.play(audio_source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        current_track = {
            'title': video['title'],
            'webpage_url': fix_url(video['webpage_url'], start_time),
            'duration': video['duration'],
            'start_timestamp': start_time,
            'source': audio_source
        }
        e = discord.Embed(
            title='Now playing',
            description=f"[{video['title']}]({video['webpage_url']})",
            color=discord.Colour.blue(),
        )
        await ctx.followup.send(embed=e)
    else:
        current_track = None

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} ({bot.user.id})')
    guild = await bot.fetch_guild(GUILD_ID)
    #bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    await bot.tree.sync()
    print('Synced global bot tree.')
    #bot.tree.clear_commands(guild=await bot.fetch_guild(GUILD_ID))
    #print(f'Cleared guild commands for {GUILD_ID}')

@bot.tree.command(name='sync', description='Owner only')
#@bot.tree.command(name='sync', description='Owner only', guild=discord.Object(GUILD_ID))
async def sync(ctx: discord.Interaction):
    if str(ctx.user.id) == OWNER_ID:
        await bot.tree.sync()
        await ctx.response.send_message('Synced commands.')
    else:
        await ctx.response.send_message('You must be the owner to use this command.')

@bot.tree.command(name='play', description='Play a song')
#@bot.tree.command(name='play', description='Play a song', guild=discord.Object(GUILD_ID))
async def play(ctx: discord.Interaction, query: str):
    global current_voice_client

    await ctx.response.defer()

    video = await search_youtube(query)
    if video is None:
        await ctx.followup.send(embed=discord.Embed(title='No results found.', color=discord.Colour.red()))
        return
    music_queue.append(video)

    await ctx.followup.send(embed=discord.Embed(title="Added to queue:",
        description=f"[{video['title']}]({video['webpage_url']})",
        color=discord.Colour.blue()
    ))

    if current_voice_client is None or not current_voice_client.is_connected():
        if ctx.user.voice is None or ctx.user.voice.channel is None:
            await ctx.followup.send(embed=discord.Embed(title='You need to be in a voice channel to play music.', color=discord.Colour.red()))
            return

        voice_channel = ctx.user.voice.channel
        current_voice_client = await voice_channel.connect()

    if not current_voice_client.is_playing() and not current_voice_client.is_paused():
        await play_next(ctx)

@bot.tree.command(name='pause', description='Pause the currently playing track')
#@bot.tree.command(name='pause', description='Pause the currently playing track', guild=discord.Object(GUILD_ID))
async def pause(ctx: discord.Interaction):
    if current_voice_client and current_voice_client.is_playing():
        current_voice_client.pause()
        await ctx.response.send_message(embed=discord.Embed(title='Paused playback.', color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='No track is currently playing.', color=discord.Colour.red()))

@bot.tree.command(name='stop', description='Stop playback and clear queue')
#@bot.tree.command(name='stop', description='Stop playback and clear queue', guild=discord.Object(GUILD_ID))
async def stop(ctx: discord.Interaction):
    global music_queue
    music_queue.clear()

    if current_voice_client and current_voice_client.is_playing():
        current_voice_client.stop()
    await ctx.response.send_message(embed=discord.Embed(title='Stopped playback and cleared queue', color=discord.Colour.blue()))

@bot.tree.command(name='queue', description='List all tracks in the queue')
#@bot.tree.command(name='queue', description='List all tracks in the queue', guild=discord.Object(GUILD_ID))
async def queue(ctx: discord.Interaction):
    if music_queue:
        queue_list = '**Now playing:**\n' + format_playing() + '\n\n' + '**Up next:**\n' + format_queue()
        e = discord.Embed(title='Queue', description=queue_list, color=discord.Colour.blue())
        await ctx.response.send_message(embed=e)
    elif current_track:
        queue_list = '**Now playing:**\n' + format_playing() + '\n\n' + '**The queue is empty.**'
        await ctx.response.send_message(embed=discord.Embed(title='Queue', description=queue_list, color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='The queue is empty.', color=discord.Colour.blue()))

@bot.tree.command(name='resume', description='Resume the paused track')
#@bot.tree.command(name='resume', description='Resume the paused track', guild=discord.Object(GUILD_ID))
async def resume(ctx: discord.Interaction):
    if current_voice_client and current_voice_client.is_paused():
        current_voice_client.resume()
        await ctx.response.send_message(embed=discord.Embed(title='Resumed playback.', color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='No track is currently paused.', color=discord.Colour.blue()))

@bot.tree.command(name='skip', description='Skip to the next track in the queue')
#@bot.tree.command(name='skip', description='Skip to the next track in the queue', guild=discord.Object(GUILD_ID))
async def skip(ctx: discord.Interaction):
    if current_voice_client and (current_voice_client.is_playing() or current_voice_client.is_paused()):
        current_voice_client.stop()
        await ctx.response.send_message(embed=discord.Embed(title='Skipped to the next track.', color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='No track is currently playing.', color=discord.Colour.blue()))

@bot.tree.command(name='song', description='Display details of current song')
#@bot.tree.command(name='song', description='Display details of current song', guild=discord.Object(GUILD_ID))
async def song(ctx: discord.Interaction):
    if current_track:
        await ctx.response.send_message(embed=discord.Embed(title='Now Playing', description=format_playing() + '\n' + progress_bar(), color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='No track is currently playing.', color=discord.Colour.blue()))

@bot.tree.command(name='leave', description='Kicks the bot from the channel')
#@bot.tree.command(name='leave', description='Kicks the bot from the channel', guild=discord.Object(GUILD_ID))
async def leave(ctx: discord.Interaction):
    global current_track, music_queue, current_voice_client
    if current_voice_client and current_voice_client.is_connected():
        await current_voice_client.disconnect()
        current_track = None
        music_queue = []
        current_voice_client = None
        await ctx.response.send_message(embed=discord.Embed(title='Left channel.', color=discord.Colour.blue()))
    else:
        await ctx.response.send_message(embed=discord.Embed(title='Not connected to voice.', color=discord.Colour.red()))

bot.run(DISCORD_TOKEN)
