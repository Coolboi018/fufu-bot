import discord
from discord import Embed
from discord.ext import commands
import yt_dlp
import asyncio
import random
from collections import deque
import os
import re
import requests
import logging
import google.generativeai as genai

# Optional Spotify support
try:
    import spotipy  # type: ignore
    from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore
except ImportError:
    spotipy = None
    SpotifyClientCredentials = None

# Configure Gemini AI
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') or 'GEMINI_API_KEY'
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Configure logging to suppress INFO level logs (including unknown command logs)
logging.basicConfig(level=logging.WARNING)

# 🔧 Fix for alias conflict (help)
bot.remove_command('help')  # Disable default help command

# Queue system
music_queues = {}
now_playing = {}
loop_mode = {}  # 'off', 'track', 'queue'
loop_queue_backup = {}  # Store original queue for loop

# yt-dlp options
ytdl_opts = {
    'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    'socket_timeout': 60,
    'retries': 5,
    'fragment_retries': 5,
    'skip_unavailable_fragments': True,
    'ignoreerrors': True,
    'no_check_certificate': True,
    'extract_flat': False,
    'noplaylist': False,  # Allow playlists
    'buffersize': 1024 * 1024,  # 1MB buffer
    'http_chunk_size': 1048576,  # 1MB chunks
}

ffmpeg_opts = {
    'before_options':
    '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10 -reconnect_at_eof 1 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
    'options': '-vn -bufsize 64k -maxrate 128k -threads 0'
}

ytdl = yt_dlp.YoutubeDL(ytdl_opts)


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.bitrate = data.get('abr', 0)  # Audio bitrate for quality info

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()

        try:
            data = await asyncio.wait_for(loop.run_in_executor(
                None, lambda: ytdl.extract_info(url, download=False)),
                                          timeout=120.0)

            if 'entries' in data:
                if not data['entries']:
                    raise Exception("❌ No results found.")
                data = data['entries'][0]

            return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_opts),
                       data=data)

        except asyncio.TimeoutError:
            raise Exception(
                "⏱️ Timeout: YouTube took too long to respond. Try again! (Increased timeout for better network handling)")
        except Exception as e:
            raise Exception(f"⚠️ Error: {str(e)[:150]}")


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!help | Miku's Melody 💖"))
    print(f'💖 {bot.user} is online and ready to sing! 🎤')

# Removed command error handling to avoid discord.py 2.x compatibility issues
# The bot will work fine without custom error handling for unknown commands

async def generate_ai_response(message_content, author_name):
    """Generate an AI response using Google Gemini"""
    try:
        prompt = f"""You are Fufu, a Discord music bot with a full range of emotions.
        You love music and interacting with server members, but you're not always cute or happy. Express emotions naturally and intensely based on the user's message - you can be happy, sad, angry, excited, frustrated, sarcastic, or any emotion that fits the context. Don't shy away from anger or excitement; show them strongly when appropriate.
        Respond with emojis and keep responses short and engaging. You're helpful but can be blunt or moody.

        User {author_name} said: {message_content}

        Respond as Fufu:"""

        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini API error: {e}")
        return "💥 Fufu here. Having issues right now. Let's just play some music instead. 🎤"

@bot.event
async def on_message(message):
    # Don't respond to own messages
    if message.author == bot.user:
        return

    # Check if bot is mentioned or message is a reply to the bot
    is_mentioned = bot.user in message.mentions
    is_reply = message.reference and message.reference.resolved and message.reference.resolved.author == bot.user

    if is_mentioned or is_reply:
        # Remove the mention from the message content for cleaner AI input
        content = message.content
        if is_mentioned:
            content = content.replace(f'<@{bot.user.id}>', '').strip()

        # Generate AI response
        async with message.channel.typing():
            ai_response = await generate_ai_response(content, message.author.display_name)
            await message.reply(ai_response)

    # Process commands regardless
    await bot.process_commands(message)


def extract_spotify_title(spotify_url):
    """Try to extract song title from a Spotify link (no API needed)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(spotify_url, headers=headers, timeout=15)
        html = response.text
        # Try multiple patterns for title
        patterns = [
            r'<title>(.*?)</title>',
            r'<meta property="og:title" content="(.*?)"',
            r'<meta name="twitter:title" content="(.*?)"'
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                title_text = match.group(1)
                clean_title = title_text.replace('| Spotify', '').replace('Spotify', '').strip()
                if clean_title:
                    return clean_title
    except Exception as e:
        print(f"Spotify title extraction error: {e}")
    return None


def get_spotify_track_queries(spotify_url):
    """Returns list of "Song Artist" search queries from Spotify"""
    queries = []

    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not spotipy or not SpotifyClientCredentials or not client_id or not client_secret:
        return queries

    try:
        auth_manager = SpotifyClientCredentials(client_id=client_id,
                                                client_secret=client_secret)
        sp = spotipy.Spotify(auth_manager=auth_manager, retries=3, requests_timeout=10)

        if "track" in spotify_url and "playlist" not in spotify_url:
            track = sp.track(spotify_url)
            name = track.get('name')
            artist = track.get('artists')[0].get('name') if track.get(
                'artists') else ''
            queries.append(f"{name} {artist}")

        elif "playlist" in spotify_url:
            results = sp.playlist_tracks(spotify_url)
            items = results.get('items', [])
            while True:
                for item in items:
                    track = item.get('track')
                    if not track:
                        continue
                    name = track.get('name')
                    artist = track.get('artists')[0].get('name') if track.get(
                        'artists') else ''
                    queries.append(f"{name} {artist}")
                if results and results.get('next'):
                    results = sp.next(results)
                    items = results.get('items', [])
                else:
                    break

        elif "album" in spotify_url:
            results = sp.album_tracks(spotify_url)
            items = results.get('items', [])
            while True:
                for item in items:
                    name = item.get('name')
                    artist = item.get('artists')[0].get('name') if item.get(
                        'artists') else ''
                    queries.append(f"{name} {artist}")
                if results and results.get('next'):
                    results = sp.next(results)
                    items = results.get('items', [])
                else:
                    break

    except Exception as e:
        print("Spotify API error:", e)
    return queries


async def get_youtube_playlist(url):
    """Extract all videos from a YouTube playlist"""
    try:
        loop = asyncio.get_event_loop()
        data = await asyncio.wait_for(loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)),
                                      timeout=150.0)

        if 'entries' in data:
            return data['entries']
        return []
    except Exception as e:
        print(f"Playlist extraction error: {e}")
        return []


@bot.command(name='play', aliases=['p'])
async def play(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("💔 Join a voice channel first, senpai! I can't sing without you~ 🎤")
        return

    channel = ctx.author.voice.channel
    if not ctx.voice_client:
        await channel.connect()

    async with ctx.typing():
        try:
            guild_id = ctx.guild.id
            if guild_id not in music_queues:
                music_queues[guild_id] = deque()
            if guild_id not in loop_mode:
                loop_mode[guild_id] = 'off'

            if "spotify.com" in query:
                queries = get_spotify_track_queries(query)

                if queries:
                    await ctx.send(
                        f"🎤 Spotify link detected! Adding {len(queries)} tracks to my playlist."
                    )
                    added = 0
                    for q in queries:
                        search_q = f"ytsearch:{q}"
                        try:
                            player = await YTDLSource.from_url(search_q,
                                                               loop=bot.loop)
                            music_queues[guild_id].append(player)
                            added += 1
                        except Exception as e:
                            print("YT search error for:", q, e)
                            continue

                    if added == 0:
                        await ctx.send(
                            "💔 Couldn't find any tracks on YouTube for that Spotify link."
                        )
                        return
                    embed = Embed(title="💖 Added to Queue", description=f"Added **{added}** tracks from Spotify! Let's sing together~ 🎤", color=0xff69b4)
                    await ctx.send(embed=embed)
                else:
                    title = extract_spotify_title(query)
                    if not title:
                        await ctx.send(
                            "💔 Couldn't extract song name from Spotify link. Try giving the song name instead!"
                        )
                        return
                    search_q = f"ytsearch:{title}"
                    player = await YTDLSource.from_url(search_q, loop=bot.loop)
                    music_queues[guild_id].append(player)
                    embed = Embed(title="💖 Added to Queue", description=f"**{player.title}**", color=0xff69b4)
                    await ctx.send(embed=embed)

            elif "youtube.com/playlist" in query or "youtu.be/playlist" in query or "&list=" in query:
                await ctx.send(
                    "📋 YouTube playlist detected! Extracting tracks for our duet, senpai~ 💖")
                entries = await get_youtube_playlist(query)

                if not entries:
                    await ctx.send("💔 Couldn't extract playlist tracks, senpai~ 😢")
                    return

                added = 0
                for entry in entries:
                    if entry:
                        try:
                            video_url = entry.get(
                                'url'
                            ) or f"https://www.youtube.com/watch?v={entry.get('id')}"
                            player = await YTDLSource.from_url(video_url,
                                                               loop=bot.loop)
                            music_queues[guild_id].append(player)
                            added += 1
                        except Exception as e:
                            print(f"Error adding playlist track: {e}")
                            continue

                embed = Embed(title="💖 Added to Queue", description=f"Added **{added}** tracks from YouTube playlist! Let's make some music~ 🎤", color=0xff69b4)
                await ctx.send(embed=embed)

            else:
                if not query.startswith('http'):
                    query = f"ytsearch:{query}"

                player = await YTDLSource.from_url(query, loop=bot.loop)
                music_queues[guild_id].append(player)
                embed = Embed(title="💖 Added to Queue", description=f"**{player.title}**", color=0xff69b4)
                await ctx.send(embed=embed)

            if not ctx.voice_client.is_playing():
                await play_next(ctx)

        except Exception as e:
            await ctx.send(f"💔 Oopsie~ Something went wrong, senpai! {e}")
            import traceback
            traceback.print_exc()


async def play_next(ctx):
    guild_id = ctx.guild.id

    can_play = (guild_id in music_queues and len(music_queues[guild_id]) > 0) or (loop_mode.get(guild_id) == 'track' and guild_id in now_playing)

    if can_play:
        if loop_mode.get(guild_id) == 'track' and guild_id in now_playing:
            # Instant loop: reuse the data without re-fetching
            current_player = now_playing[guild_id]
            player = YTDLSource(discord.FFmpegPCMAudio(current_player.data['url'], **ffmpeg_opts), data=current_player.data)
        else:
            player = music_queues[guild_id].popleft()
            if loop_mode.get(guild_id) == 'queue':
                music_queues[guild_id].append(player)

        now_playing[guild_id] = player

        def after(error):
            if error:
                print(f"Error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

        ctx.voice_client.play(player, after=after)

        loop_emoji = ""
        if loop_mode.get(guild_id) == 'track':
            loop_emoji = " 🔂"
        elif loop_mode.get(guild_id) == 'queue':
            loop_emoji = " 🔁"

        embed = Embed(title="🎤 Now Singing", description=f"**{player.title}**{loop_emoji}", color=0xff69b4)
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        if player.bitrate:
            embed.add_field(name="Bitrate", value=f"{player.bitrate} kbps", inline=True)
        await ctx.send(embed=embed)

    else:
        now_playing.pop(guild_id, None)
        embed = Embed(title="💔 Queue Finished", description="All songs are done, senpai~ Add more music to keep me singing! 🎤", color=0xff69b4)
        await ctx.send(embed=embed)
        await start_idle_timer(ctx)


async def start_idle_timer(ctx):
    await asyncio.sleep(120)
    if ctx.voice_client and not ctx.voice_client.is_playing():
        await ctx.voice_client.disconnect()
        await ctx.send("💔 Leaving due to inactivity, senpai~ Come back soon! 💖")


@bot.command(name='skip', aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped! Next song, senpai~ 💖")
    else:
        await ctx.send("💔 Nothing is playing right now, senpai~ Add some music! 🎤")


@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused! Taking a break, senpai~ 💖")
    else:
        await ctx.send("💔 Nothing is playing right now, senpai~ 😢")


@bot.command(name='resume', aliases=['r'])
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed! Let's continue singing~ 🎤")
    else:
        await ctx.send("💔 Nothing is paused right now, senpai~ 😢")


@bot.command(name='stop')
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues:
        music_queues[guild_id].clear()
    if guild_id in loop_mode:
        loop_mode[guild_id] = 'off'
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared queue! Time for a break, senpai~ 💖")
        await start_idle_timer(ctx)


@bot.command(name='leave', aliases=['disconnect', 'dc'])
async def leave(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        if guild_id in music_queues:
            music_queues[guild_id].clear()
        if guild_id in loop_mode:
            loop_mode[guild_id] = 'off'
        await ctx.voice_client.disconnect()
        await ctx.send("💖 Bye-bye, senpai! Come back soon~ 🥹")
    else:
        await ctx.send("💔 I'm not in a voice channel, senpai~ 😢")


@bot.command(name='queue', aliases=['q'])
async def queue(ctx):
    guild_id = ctx.guild.id
    embed = Embed(title="🎤 Miku's Playlist", color=0xff69b4)

    if guild_id in now_playing:
        player = now_playing[guild_id]
        embed.add_field(name="Now Singing", value=player.title, inline=False)

    if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
        embed.add_field(name="Queue", value="💔 Queue is empty, senpai~ Add some songs! 🎵", inline=False)
    else:
        queue_list = []
        for i, player in enumerate(list(music_queues[guild_id])[:10], 1):
            queue_list.append(f"{i}. {player.title}")
        embed.add_field(name="Up Next", value="\n".join(queue_list), inline=False)
        if len(music_queues[guild_id]) > 10:
            embed.add_field(name="More", value=f"...and {len(music_queues[guild_id]) - 10} more tracks", inline=False)

    loop_status = ""
    if loop_mode.get(guild_id) == 'track':
        loop_status = "🔂 Looping Track"
    elif loop_mode.get(guild_id) == 'queue':
        loop_status = "🔁 Looping Queue"
    if loop_status:
        embed.set_footer(text=loop_status)

    await ctx.send(embed=embed)


@bot.command(name='loop', aliases=['l'])
async def loop_command(ctx, mode: str = None):
    guild_id = ctx.guild.id

    if guild_id not in loop_mode:
        loop_mode[guild_id] = 'off'

    if mode is None:
        current = loop_mode[guild_id]
        if current == 'off':
            loop_mode[guild_id] = 'track'
            await ctx.send("🔂 **Loop:** Current track enabled! Let's sing it again~ 💖")
        elif current == 'track':
            loop_mode[guild_id] = 'queue'
            await ctx.send("🔁 **Loop:** Entire queue enabled! Non-stop music, senpai~ 🎤")
        else:
            loop_mode[guild_id] = 'off'
            await ctx.send("❌ **Loop:** Disabled")
    else:
        mode = mode.lower()
        if mode in ['track', 't', 'song', 'single']:
            loop_mode[guild_id] = 'track'
            await ctx.send("🔂 **Loop:** Current track enabled! Let's sing it again~ 💖")
        elif mode in ['queue', 'q', 'all']:
            loop_mode[guild_id] = 'queue'
            await ctx.send("🔁 **Loop:** Entire queue enabled! Non-stop music, senpai~ 🎤")
        elif mode in ['off', 'stop', 'disable']:
            loop_mode[guild_id] = 'off'
            await ctx.send("❌ **Loop:** Disabled")
        else:
            await ctx.send(
                "💔 Invalid mode, senpai~ Use: `!loop track`, `!loop queue`, or `!loop off` 💖"
            )


@bot.command(name='nowplaying', aliases=['np'])
async def nowplaying(ctx):
    guild_id = ctx.guild.id
    if guild_id in now_playing:
        player = now_playing[guild_id]
        embed = Embed(title="🎤 Now Singing", color=0xff69b4)
        embed.add_field(name="Title", value=player.title, inline=False)
        if player.duration:
            embed.add_field(name="Duration", value=f"{player.duration // 60}:{player.duration % 60:02d}", inline=True)
        if player.bitrate:
            embed.add_field(name="Bitrate", value=f"{player.bitrate} kbps", inline=True)
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        loop_status = ""
        if loop_mode.get(guild_id) == 'track':
            loop_status = "🔂 Looping Track"
        elif loop_mode.get(guild_id) == 'queue':
            loop_status = "🔁 Looping Queue"
        if loop_status:
            embed.set_footer(text=loop_status)
        await ctx.send(embed=embed)
    else:
        embed = Embed(title="💔 Nothing Playing", description="The queue is empty, senpai~ Add some music! 🎵", color=0xff69b4)
        await ctx.send(embed=embed)


@bot.command(name='commands', aliases=['help'])
async def commands(ctx):
    embed = Embed(title="🎤 Miku's Command List", description="Here are all the commands I can do, senpai! 💖", color=0xff69b4)
    embed.add_field(name="Playback", value=
        "**!play <song/link>** or **!p** - Play a song (YouTube, Spotify, or search)\n"
        "**!skip** or **!s** - Skip current song\n"
        "**!pause** - Pause music\n"
        "**!resume** or **!r** - Resume music\n"
        "**!stop** - Stop and clear queue\n"
        "**!leave** or **!dc** - Disconnect bot", inline=False)
    embed.add_field(name="Queue Management", value=
        "**!queue** or **!q** - Show my playlist\n"
        "**!nowplaying** or **!np** - Show current song\n"
        "**!loop** or **!l** - Toggle loop (off → track → queue → off)\n"
        "**!loop track** - Loop current track\n"
        "**!loop queue** - Loop entire queue\n"
        "**!loop off** - Disable loop", inline=False)
    embed.add_field(name="New Features", value=
        "**!volume <0-100>** - Set volume\n"
        "**!shuffle** - Shuffle the queue\n"
        "**!remove <index>** - Remove a track from queue\n"
        "**!ping** - Check bot latency", inline=False)
    embed.add_field(name="Supports", value=
        "✅ YouTube links & playlists\n"
        "✅ Spotify links, playlists & albums\n"
        "✅ Search by song name", inline=False)
    embed.set_footer(text="Use !help for this menu, senpai~ 🌸")
    await ctx.send(embed=embed)


@bot.command()
async def ping(ctx):
    embed = Embed(title="🏓 Pong! 💖", description=f"Latency: {round(bot.latency * 1000)}ms", color=0xff69b4)
    await ctx.send(embed=embed)

@bot.command()
async def volume(ctx, volume: int):
    if not ctx.voice_client:
        embed = Embed(title="💔 Error", description="I'm not in a voice channel, senpai~ 😢", color=0xff69b4)
        await ctx.send(embed=embed)
        return
    if volume < 0 or volume > 100:
        embed = Embed(title="💔 Error", description="Volume must be between 0 and 100, senpai! 💖", color=0xff69b4)
        await ctx.send(embed=embed)
        return
    ctx.voice_client.source.volume = volume / 100
    embed = Embed(title="🔊 Volume Set", description=f"Volume set to {volume}%", color=0xff69b4)
    await ctx.send(embed=embed)

@bot.command()
async def shuffle(ctx):
    guild_id = ctx.guild.id
    if guild_id not in music_queues or len(music_queues[guild_id]) < 2:
        embed = Embed(title="💔 Error", description="Not enough tracks in queue to shuffle, senpai~ Add more songs! 🎵", color=0xff69b4)
        await ctx.send(embed=embed)
        return
    random.shuffle(music_queues[guild_id])
    embed = Embed(title="🔀 Shuffled!", description="The queue has been shuffled! Let's mix it up~ 💖", color=0xff69b4)
    await ctx.send(embed=embed)

@bot.command()
async def remove(ctx, index: int):
    guild_id = ctx.guild.id
    if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
        embed = Embed(title="💔 Error", description="Queue is empty, senpai~ 😢", color=0xff69b4)
        await ctx.send(embed=embed)
        return
    if index < 1 or index > len(music_queues[guild_id]):
        embed = Embed(title="💔 Error", description="Invalid index, senpai! 💖", color=0xff69b4)
        await ctx.send(embed=embed)
        return
    removed = music_queues[guild_id][index - 1]
    del music_queues[guild_id][index - 1]
    embed = Embed(title="🗑️ Removed", description=f"Removed: {removed.title}", color=0xff69b4)
    await ctx.send(embed=embed)

# Get token from Replit Secrets
token = os.getenv('DISCORD_TOKEN')
bot.run("DISCORD_TOKEN")
