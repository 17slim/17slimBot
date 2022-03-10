import discord
import asyncio
import datetime
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import youtube_dl
import audioread

load_dotenv()

DISCORD_TOKEN = os.getenv("discord_token")

intents = discord.Intents().default()
bot = commands.Bot(command_prefix=",",intents=intents)

class Queue2(asyncio.Queue):
    def move(self, a, b):
        if self.empty():
            raise asyncio.QueueEmpty
        item1 = self._queue[a]
        del self._queue[a]
        self._queue.insert(b,item1)

    def value_at(self, a):
        if self.empty():
            return None
        return self._queue[a]

    def remove(self, a):
        if self.empty():
            return
        del self._queue[a]

songs = Queue2()
songlist = [] # contains list of songs and lengths to display in queue
time_started = None
play_next_song = asyncio.Event()
next_delete = None

last_vc = None

thumbsup = '👍'

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
            for entry in data['entries']:  # formerly data = data['entries'][0]
                filenames.append(entry['url'] if stream else ytdl.prepare_filename(entry))
                entries.append(entry)
        else:
            filenames.append(data['url'])
            entries.append(data)
        return [cls(discord.FFmpegPCMAudio(filenames[i], **ffmpeg_options), data=entries[i]) for i in range(len(filenames))]

class FileSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, title, volume=0.5):
        super().__init__(source, volume)
        self.title = title
        self.link = self.title
        self.filepath = './downloads/' + self.title
        self.length = audioread.audio_open(self.filepath).duration

    @classmethod
    async def from_file(cls, filename, *, loop=None, stream=False, timestamp=0):
        ffmpeg_options = {
            'options': '-ss %d' % timestamp,
        }
        src = discord.FFmpegPCMAudio(source=filename, **ffmpeg_options)
        return cls(src, title=filename.split('/')[-1])

async def audio_player_task():
    global time_started, next_delete
    while True:
        play_next_song.clear()
        (ctx, vc, source) = await songs.get()
        songlist.pop(0)
        if isinstance(source, FileSource):
            next_delete = './downloads/' + source.title

        if vc.is_connected():
            vc.play(source, after=toggle_next)
            time_started = datetime.datetime.now()
            embed = discord.Embed(
                title='Now playing',
                description=source.link,
                color=discord.Colour.blue(),
            )
            await ctx.send(embed=embed)
        await play_next_song.wait()

def toggle_next(e):
    global next_delete
    if e:
        print('Error playing audio: %s' % e)
    bot.loop.call_soon_threadsafe(play_next_song.set)
    if next_delete:
        try:
            os.remove(next_delete)
            next_delete = None
        except PermissionError:
            # permission errors are fine, as they only seem to occur when future sources
            # have a handle on the file still. if no queued tracks use the same file
            # then it seems to delete as expected.
            pass

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
    global last_vc
    if not ctx.message.author.voice and not last_vc:
        await ctx.send(embed=discord.Embed(description="{} is not connected to a voice channel and bot has no channel history.".format(ctx.message.author.name),
            color=discord.Colour.gold()))
        return False
    # either sender in voice or last_vc exists (or both)
    if ctx.message.author.voice:
        # sender in voice, connect to same channel
        last_vc = ctx.message.author.voice.channel
    voice_client = ctx.message.guild.voice_client
    if last_vc and voice_client and last_vc == voice_client.channel:
        # bot is in channel it would join (either last_vc, or sender's vc set above)
        await ctx.send(embed=discord.Embed(description="Already connected to channel.",
            color=discord.Colour.gold()))
        return True
    await last_vc.connect()
    if last_vc and voice_client and last_vc == voice_client.channel:
        return True
    return False

@bot.command(help='Tells the bot to leave the voice channel')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await ctx.message.add_reaction(thumbsup)
    elif not voice_client:
        await ctx.send(embed=discord.Embed(description="The bot has no voice client for the server.",
            color=discord.Colour.red()))
    else:
        await ctx.send(embed=discord.Embed(description="The bot is not connected to a voice channel.",
            color=discord.Colour.gold()))

@bot.command(aliases=['p'], help='Plays a song')
async def play(ctx, *args):
    if len(args) == 0 and len(ctx.message.attachments) == 0:
        await resume(ctx)
        return

    voice_client = ctx.message.guild.voice_client
    if not (voice_client and voice_client.is_connected()):
        succ = await join(ctx)
        if not succ:
            print('failed to join')
            return
        else:
            print('joined successfully')
    voice_client = ctx.message.guild.voice_client
    # if still not in voice, exit
    if not (voice_client and voice_client.is_connected()):
        print('error trying to join server')
        await ctx.send(embed=discord.Embed(description="Error trying to join server.",
            color=discord.Colour.red()))
        return

    # play file if no args and has attachment
    if len(args) == 0:
        print('playing from attachment')
        for a in ctx.message.attachments:
            async with ctx.typing():
                f = './downloads/' + a.filename.replace('/','_').replace('\\','_')
                await a.save(f)
                source = await FileSource.from_file(f)
                await songs.put((ctx, voice_client, source))
                songlist.append({'link':source.link, 'length':source.length})
                await ctx.send(embed=discord.Embed(
                    title='Queued track (Position {})'.format(songs.qsize()),
                    description=songlist[-1]['link'],
                    color=discord.Colour.blue(),
                ))
        return

    print('playing from args')
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
        await ctx.message.add_reaction(thumbsup)
    elif not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
    else:
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))

@bot.command(help='Resumes the song')
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if (voice_client and voice_client.is_paused()):
        voice_client.resume()
        source = voice_client.source
        #embed = discord.Embed(
        #    title='Resumed track',
        #    description=source.link,
        #    color=discord.Colour.blue(),
        #)
        #await ctx.send(embed=embed)
        await ctx.message.add_reaction(thumbsup)
    elif not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
    else:
        await ctx.send(embed=discord.Embed(description='The bot was not playing anything before this.',
            color=discord.Colour.gold()))

@bot.command(help='Skips the song')
async def skip(ctx):
    voice_client = ctx.message.guild.voice_client
    if (voice_client and voice_client.is_playing()):
        voice_client.stop()
        await ctx.message.add_reaction(thumbsup)
        #await ctx.send(embed=discord.Embed(description='Skipped track.', color=discord.Colour.blue()))
    elif not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
    else:
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))

@bot.command(aliases=['clr'], help='Stops the song')
async def clear(ctx):
    if songs.empty():
        await ctx.send(embed=discord.Embed(description='The bot has nothing queued.',
            color=discord.Colour.gold()))
    while not songs.empty():
        try:
            songs.get_nowait()
            songlist.pop(0)
        except e:
            pass
    await ctx.send(embed=discord.Embed(description='Cleared the queue.',
        color=discord.Colour.blue()))

@bot.command(aliases=['q','songlist','list','ls'], help="Displays the queue")
async def queue(ctx):
    voice_client = ctx.message.guild.voice_client
    if not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
        return
    elif not voice_client.is_playing():
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
    if not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
        return
    elif not voice_client.is_playing():
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
    url = ''
    if isinstance(voice_client.source, YTDLSource):
        url = voice_client.source.weburl
    await ctx.send(embed=discord.Embed(title=voice_client.source.title,
        url=url,
        description=fmt.format(tm, total),
        color=discord.Colour.blue()))

@bot.command(aliases=['playfrom','goto'], help='Seeks to a given point in the track')
async def seek(ctx, *args):
    global time_started
    if len(args) == 0:
        cmd = bot.get_command('seek')
        bot.help_command.context = ctx
        await bot.help_command.send_command_help(cmd)
        return

    voice_client = ctx.message.guild.voice_client
    if not voice_client:
        await ctx.send(embed=discord.Embed(description='The bot has no voice client for the server.',
            color=discord.Colour.red()))
        return
    elif not voice_client.is_playing():
        await ctx.send(embed=discord.Embed(description='The bot is not playing anything at the moment.',
            color=discord.Colour.gold()))
        return

    voice_client.pause()
    tm = args[0]
    sec = time_to_sec(tm)
    tm = sec_to_time(sec)
    source = voice_client.source
    if isinstance(source, YTDLSource):
        voice_client.source = (await YTDLSource.from_url(source.weburl, loop=bot.loop, stream=True, timestamp=sec))[0]
    else:
        voice_client.source = await FileSource.from_file(source.filepath, timestamp=sec)
    time_started = datetime.datetime.now() - datetime.timedelta(seconds = sec)
    voice_client.resume()

    await ctx.send(embed=discord.Embed(description='Now playing from {}'.format(tm),
        color=discord.Colour.blue()))

@bot.command(aliases=['mv'], help='Moves a song to a different place in the queue')
async def move(ctx, *args):
    if len(args) < 2:
        bot.help_command.context = ctx
        await bot.help_command.send_command_help(bot.get_command('move'))
        return

    a = int(args[0]) - 1
    b = int(args[1]) - 1
    a = a if a >= 0 else 0
    b = b if b >= 0 else 0
    a = a if a < songs.qsize() else songs.qsize() - 1
    b = b if b < songs.qsize() else songs.qsize() - 1
    songs.move(a, b)
    songlist.insert(b, songlist.pop(a))
    await ctx.send(embed=discord.Embed(description='Moved {} to position {}'
        .format(songs.value_at(b)[2].title, b + 1),
        color=discord.Colour.blue()))

@bot.command(aliases=['rm','del','delete'], help='Removes a track from the queue')
async def remove(ctx, *args):
    if len(args) < 1:
        bot.help_command.context = ctx
        await bot.help_command.send_command_help(bot.get_command('move'))
        return

    a = int(args[0]) - 1
    a = a if a >= 0 else 0
    a = a if a < songs.qsize() else songs.qsize() - 1

    title = songs.value_at(a)[2].title
    songs.remove(a)
    songlist.pop(a)
    await ctx.send(embed=discord.Embed(description='Removed track {}: {}'
        .format(a + 1, title),
        color=discord.Colour.blue()))

@bot.event
async def on_ready():
    print('changing presence')
    await bot.change_presence(activity=discord.Activity(name='music - now with files!', type=discord.ActivityType.playing))
    print('presence changed')

if __name__ == '__main__':
    bot.loop.create_task(audio_player_task())
    bot.run(DISCORD_TOKEN)
