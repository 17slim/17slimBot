import discord
import asyncio
import datetime
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import youtube_dl
import copy

load_dotenv()

DISCORD_TOKEN = os.getenv("discord_token")

intents = discord.Intents().default()
bot = commands.Bot(command_prefix=",",intents=intents)
songs = asyncio.Queue()
songlist = [] # contains list of songs and lengths to display in queue
time_started = None
play_next_song = asyncio.Event()

youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'best',
    # shouldn't matter because format is 'best' not 'mp3'
    'audioquality': '320',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}


ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.length = data.get('duration')
        self.url = data.get('url')
        self.weburl = data.get('webpage_url')
        self.playlist_title = data.get('playlist_title')
        self.link = '[{}]({})'.format(self.title,self.weburl)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, timestamp=0):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -ss %d' % timestamp
        }

        filenames = []
        entries = []
        if 'entries' in data:
            print('queuing {} songs'.format(len(data['entries'])))
            for entry in data['entries']:  # formerly data = data['entries'][0]
                filenames.append(entry['url'] if stream else ytdl.prepare_filename(entry))
                entries.append(entry)
        else:
            filenames.append(data['url'])
            entries.append(data)
        return [cls(discord.FFmpegPCMAudio(filenames[i], **ffmpeg_options), data=entries[i]) for i in range(len(filenames))]

async def audio_player_task():
    global time_started
    while True:
        play_next_song.clear()
        (ctx, vc, ytsource) = await songs.get()
        songlist.pop(0)
        if vc.is_connected():
            vc.play(ytsource, after=toggle_next)
            time_started = datetime.datetime.now()
            embed = discord.Embed(
                title='Now playing',
                description=ytsource.link,
                color=discord.Colour.blue(),
            )
            await ctx.send(embed=embed)
        await play_next_song.wait()

def toggle_next(e):
    if e:
        print('Error playing audio: %s' % e)
    bot.loop.call_soon_threadsafe(play_next_song.set)

def sec_to_time(seconds):
    seconds = int(seconds)
    ret = str(datetime.timedelta(seconds=seconds))
    if len(ret.split(':')) == 3 and int(ret.split(':')[0]) == 0:
        ret = ':'.join(ret.split(':')[1:])
    return ret
    
def time_to_sec(tm):
    tm = tm.split(':')
    if len(tm) == 1:
        return int(tm[0])
    if len(tm) == 2:
        return 60*int(tm[0]) + int(tm[1])
    if len(tm) == 3:
        return 3600*int(tm[0]) + 60*int(tm[1]) + int(tm[2])
    if len(tm) == 4:
        return 86400*int(tm[0]) + 3600*int(tm[1]) + 60*int(tm[2]) + int(tm[3])
    return 0

@bot.command(help='Tells the bot to join the voice channel')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send(embed=discord.Embed(description="{} is not connected to a voice channel".format(ctx.message.author.name),
            color=discord.Colour.gold()))
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()

@bot.command(help='Tells the bot to leave the voice channel')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send(embed=discord.Embed(description="The bot is not connected to a voice channel.",
            color=discord.Colour.gold()))

@bot.command(aliases=['p'], help='Plays a song')
async def play(ctx, *args):
    if len(args) == 0:
        await resume(ctx)
        return

    voice_client = ctx.message.guild.voice_client
    if not (voice_client and voice_client.is_connected()):
        await join(ctx)
    voice_client = ctx.message.guild.voice_client
    # if still not in voice, exit
    if not (voice_client and voice_client.is_connected()):
        return

    # combine mutli-word search to one url
    url = ''
    for word in args:
        url += word
        url += ' '

    async with ctx.typing():
        ytsources = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ytsource = ytsources[0]
        for ytsource in ytsources:
            await songs.put((ctx, voice_client, ytsource))
            songlist.append({'link':ytsource.link, 'length':ytsource.length})
        embed = discord.Embed(description='Failed to queue songs',
            color = discord.Colour.gold())
        if len(ytsources) == 1:
            embed = discord.Embed(
                title='Queued track (Position {})'.format(songs.qsize()),
                description=songlist[-1]['link'],
                color=discord.Colour.blue(),
            )
        else:
            embed = discord.Embed(
                title="Queued {} tracks from `{}`".format(len(ytsources), ytsources[0].playlist_title),
                description='Fuck yeah, playlist',
                color=discord.Colour.blue(),
            )
        await ctx.send(embed=embed)

@bot.command(help='Pauses the song')
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client
    if (voice_client and voice_client.is_playing()):
        voice_client.pause()
    else:
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))

@bot.command(help='Resumes the song')
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if (voice_client and voice_client.is_paused()):
        voice_client.resume()
        ytsource = voice_client.source
        embed = discord.Embed(
            title='Resumed track',
            description=ytsource.link,
            color=discord.Colour.blue(),
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send(embed=discord.Embed(description='The bot was not playing anything before this.',
            color=discord.Colour.gold()))

@bot.command(help='Skips the song')
async def skip(ctx):
    voice_client = ctx.message.guild.voice_client
    if (voice_client and voice_client.is_playing()):
        voice_client.stop()
        await ctx.send(embed=discord.Embed(description='Skipped track.', color=discord.Colour.blue()))
    else:
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))

@bot.command(help='Stops the song')
async def stop(ctx):
    voice_client = ctx.message.guild.voice_client
    while not songs.empty():
        try:
            songs.get_nowait()
            songlist.pop(0)
        except e:
            pass
    if (voice_client and voice_client.is_playing()):
        voice_client.stop()
    else:
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))

@bot.command(aliases=['q','songlist','list','ls'], help="Displays the queue")
async def queue(ctx):
    voice_client = ctx.message.guild.voice_client
    if not (voice_client and voice_client.is_playing()):
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))
        return
    sb = ''
    for i in range(len(songlist)):
        sb += '{}.\t{} [{}]\n'.format(i + 1, songlist[i]['link'], sec_to_time(songlist[i]['length']))
        
    elapsed = datetime.datetime.now() - time_started
    tm = sec_to_time(elapsed.total_seconds())
    total = sec_to_time(voice_client.source.length)
    fmt = '**Currently playing:**\n{} [{}/{}]\n\n**Currently queued:**\n{}'
    await ctx.send(embed=discord.Embed(description=fmt.format(
            voice_client.source.link,
            tm,
            total,
            sb),
        color=discord.Colour.blue()))

@bot.command(aliases=['s','songinfo','playing','length'], help="Displays the currently playing song")
async def song(ctx):
    voice_client = ctx.message.guild.voice_client
    if not (voice_client and voice_client.is_playing()):
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))
        return

    elapsed = datetime.datetime.now() - time_started
    tm = sec_to_time(elapsed.total_seconds())
    total = sec_to_time(voice_client.source.length)
    prg = '\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac\u25ac'
    i = int(1.0 * len(prg) * elapsed.total_seconds() / voice_client.source.length)
    prg = prg[:i] + ':radio_button:' + prg[i:]
    fmt = prg + '\n[{}/{}]'
    await ctx.send(embed=discord.Embed(title=voice_client.source.title,
        url=voice_client.source.weburl,
        description=fmt.format(tm, total),
        color=discord.Colour.blue()))

@bot.command(aliases=['playfrom'], help='Seeks to a given point in the track')
async def seek(ctx, *args):
    global time_started
    if len(args) == 0:
        cmd = bot.get_command('seek')
        bot.help_command.context = ctx
        await bot.help_command.send_command_help(cmd)
        return

    voice_client = ctx.message.guild.voice_client
    if not (voice_client and voice_client.is_playing()):
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))
        return

    tm = args[0]
    sec = time_to_sec(tm)
    tm = sec_to_time(sec)
    ytsource = voice_client.source
    voice_client.source = (await YTDLSource.from_url(ytsource.weburl, loop=bot.loop, stream=True, timestamp=sec))[0]
    time_started = datetime.datetime.now() - datetime.timedelta(seconds = sec)

    await ctx.send(embed=discord.Embed(description='Now playing from {}'.format(tm),
        color=discord.Colour.blue()))

#@bot.command(aliases=['mv'], help='Moves a song to a different place in the queue')
#async def move(ctx, *args):
#    global songs
#    if len(args) < 2:
#        bot.help_command.context = ctx
#        await bot.help_command.send_command_help(bot.get_command('move'))
#        return


if __name__ == '__main__':
    bot.loop.create_task(audio_player_task())
    bot.run(DISCORD_TOKEN)
