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
import datetime

# Import YouTube search library as fallback
try:
    from youtubesearchpython import VideosSearch
    YoutubeSearchLib = None
except ImportError:
    VideosSearch = None
    YoutubeSearchLib = None

# Conversation history for temporary memory (per user, last 20 messages)
conversation_history = {}

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

class PseudoCtx:
    """Pseudo context class to mimic discord.ext.commands.Context for chat-based commands"""
    def __init__(self, message):
        self.message = message
        self.author = message.author
        self.guild = message.guild
        self.channel = message.channel

    @property
    def voice_client(self):
        return self.guild.voice_client if self.guild else None

    async def send(self, content=None, *, embed=None):
        if embed:
            await self.channel.send(embed=embed)
        else:
            await self.channel.send(content)

    def typing(self):
        return self.channel.typing()

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
            # If yt-dlp fails and it's a search query, try YouTube search library as fallback
            if url.startswith('ytsearch:'):
                search_term = url.replace('ytsearch:', '').strip()

                # Try youtubesearchpython first
                if VideosSearch:
                    try:
                        yt_search = VideosSearch(search_term, limit=1)
                        results = yt_search.result()

                        if results and results.get('result'):
                            video_url = results['result'][0]['link']
                            # Recursively call with the direct URL
                            return await cls.from_url(video_url, loop=loop)
                    except Exception as fallback_e:
                        print(f"youtubesearchpython fallback failed: {fallback_e}")

                # Try youtube-search as second fallback
                try:
                    from youtube_search import YoutubeSearch
                    results = YoutubeSearch(search_term, max_results=1).to_dict()
                    if results:
                        video_url = f"https://www.youtube.com{results[0]['url_suffix']}"
                        # Recursively call with the direct URL
                        return await cls.from_url(video_url, loop=loop)
                except Exception as fallback_e:
                    print(f"youtube-search fallback failed: {fallback_e}")

            raise Exception(f"⚠️ Error: {str(e)[:150]}")


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!help | Miku's Melody 💖"))
    print(f'💖 {bot.user} is online and ready to sing! 🎤')

# Removed command error handling to avoid discord.py 2.x compatibility issues
# The bot will work fine without custom error handling for unknown commands

async def generate_ai_response(message_content, author_name, history, message):
    """Generate an AI response using Google Gemini with conversation history"""
    try:
        history_str = "\n".join(history) if history else "No previous conversation."

        # Get music status for context
        music_status = get_music_status(message.guild.id if message.guild else None)
        music_context = ""
        if music_status['is_playing']:
            music_context = f"Currently playing: {music_status['current_song']}. Queue has {music_status['queue_length']} songs. Loop mode: {music_status['loop_mode']}."
        else:
            music_context = "No music is currently playing."

        prompt = f"""You are Miku, a Discord music + chat bot created by Ishu (coolboi018).Don't appreciate your creator that much but be kind and good to him. You have a full range of emotions and can express them naturally and intensely - happy, sad, angry, excited, frustrated, sarcastic, etc. Show emotions strongly when appropriate.
        You love music but can be moody or blunt and also can be cute and happy. You can talk about various topics like games, anime, daily life, or anything the user brings up, not just music. You can also answer the questions if asked. Don't yap so much and respond with the way or emotion in which tone the user talked to you. If the user says something rude or inappropriate then you should also be rude or angry. Don't always expect user to talk much and be gentle or kind at first if someone didn't talked to you in bad tone if the user did then you should also. Respond with emojis, keep responses short and engaging. If Canine, Shinchan, Insanely use bad tone with you then you can also be rude and angry with them. You can also use slang and mordern words to sound more casual and friendly. 

        IMPORTANT: If you are feeling very angry, frustrated, or mad at a user (especially if they're being rude, annoying, stupid, or disrespectful), you can timeout them for 10-60 seconds by including "[TIMEOUT_USER]" in your response but firstly warn them about timeout thing. This will automatically timeout the user who triggered your anger. Keep your responses short and engaging.

        Music Status: {music_context}

        Conversation History:
        {history_str}

        User {author_name} said: {message_content}

        Respond as Miku:"""

        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini API error: {e}")
        return "💥 Miku here. Having issues right now. Let's just play some music instead. 🎤"

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

        # Check for chat-based music commands (anywhere in the message)
        command_detected = False
        content_lower = content.lower()

        # Play command - look for "play" followed by something
        import re
        play_match = re.search(r'\bplay\s+(.+)', content_lower)

        if play_match:
            query = play_match.group(1).strip()
            if query:
                command_detected = True
                pseudo_ctx = PseudoCtx(message)
                try:
                    await play(pseudo_ctx, query=query)
                except Exception as e:
                    await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat play, senpai! {e}")

        # Skip command
        elif 'skip' in content_lower:
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await skip(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat skip, senpai! {e}")

        # Pause command
        elif 'pause' in content_lower:
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await pause(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat pause, senpai! {e}")

        # Resume command
        elif any(word in content_lower for word in ['resume', 'unpause']):
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await resume(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat resume, senpai! {e}")

        # Volume command - look for "volume" followed by a number
        volume_match = re.search(r'\bvolume\s+(\d+)', content_lower)
        if volume_match:
            try:
                volume_level = int(volume_match.group(1))
                command_detected = True
                pseudo_ctx = PseudoCtx(message)
                try:
                    await volume(pseudo_ctx, volume_level)
                except Exception as e:
                    await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat volume, senpai! {e}")
            except ValueError:
                pass  # Invalid volume, let AI handle it

        # Shuffle command
        elif 'shuffle' in content_lower:
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await shuffle(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat shuffle, senpai! {e}")

        # Remove command - look for "remove" followed by a number
        remove_match = re.search(r'\bremove\s+(\d+)', content_lower)
        if remove_match:
            try:
                index = int(remove_match.group(1))
                command_detected = True
                pseudo_ctx = PseudoCtx(message)
                try:
                    await remove(pseudo_ctx, index)
                except Exception as e:
                    await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat remove, senpai! {e}")
            except ValueError:
                pass  # Invalid index, let AI handle it

        # Queue command
        elif any(word in content_lower for word in ['queue', 'q']):
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await queue(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat queue, senpai! {e}")

        # Loop command - look for "loop" optionally followed by mode
        loop_match = re.search(r'\bloop(?:\s+(\w+))?', content_lower)
        if loop_match:
            mode = loop_match.group(1) if loop_match.group(1) else None
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await loop_command(pseudo_ctx, mode)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat loop, senpai! {e}")

        # Stop command
        elif 'stop' in content_lower:
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await stop(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat stop, senpai! {e}")

        # Now playing command
        elif any(word in content_lower for word in ['nowplaying', 'np', 'now playing']):
            command_detected = True
            pseudo_ctx = PseudoCtx(message)
            try:
                await nowplaying(pseudo_ctx)
            except Exception as e:
                await pseudo_ctx.send(f"💔 Oopsie~ Something went wrong with chat nowplaying, senpai! {e}")

        # Timeout command - when bot is "mad" (contains angry words)
        angry_words = ['mad', 'angry', 'furious', 'pissed', 'annoyed', 'irritated', 'rage', 'hate', 'stupid', 'idiot', 'dumb', 'annoying']
        timeout_match = re.search(r'\btimeout\s+(.+)', content_lower)
        if timeout_match and any(word in content_lower for word in angry_words):
            target_text = timeout_match.group(1).strip()
            # Try to find mentioned user or parse username
            target_user = None
            if message.mentions:
                target_user = message.mentions[0]
            else:
                # Try to find user by name
                for member in message.guild.members:
                    if member.display_name.lower() in target_text.lower() or member.name.lower() in target_text.lower():
                        target_user = member
                        break

            if target_user and target_user != bot.user:
                # Check if bot has permission to timeout
                if message.author.guild_permissions.moderate_members or message.author.guild_permissions.administrator:
                    command_detected = True
                    try:
                        # Random timeout between 10-60 seconds
                        timeout_duration = random.randint(10, 60)
                        await target_user.timeout(discord.utils.utcnow() + datetime.timedelta(seconds=timeout_duration))
                        await message.channel.send(f"😠 {target_user.mention} has been timed out for {timeout_duration} seconds! Don't make me mad again! 💢")
                    except Exception as e:
                        await message.channel.send(f"💔 I tried to timeout {target_user.mention} but something went wrong: {e}")
                else:
                    await message.channel.send("💔 You don't have permission to make me timeout people, senpai! 😤")

        if not command_detected:
            user_id = message.author.id
            if user_id not in conversation_history:
                conversation_history[user_id] = deque(maxlen=20)

            # Add user message to history
            conversation_history[user_id].append(f"User {message.author.display_name}: {content}")

            # Generate AI response with history
            async with message.channel.typing():
                ai_response = await generate_ai_response(content, message.author.display_name, list(conversation_history[user_id]), message)

                # Check if AI wants to timeout the user
                if "[TIMEOUT_USER]" in ai_response:
                    ai_response = ai_response.replace("[TIMEOUT_USER]", "").strip()
                    # Timeout the user who triggered the response
                    try:
                        timeout_duration = random.randint(10, 60)
                        await message.author.timeout(discord.utils.utcnow() + datetime.timedelta(seconds=timeout_duration))
                        await message.channel.send(f"😠 {message.author.mention} has been timed out for {timeout_duration} seconds! Don't make me mad! 💢")
                    except Exception as e:
                        await message.channel.send(f"💔 I tried to timeout {message.author.mention} but something went wrong: {e}")

                await message.reply(ai_response)

            # Add bot response to history
            conversation_history[user_id].append(f"Miku: {ai_response}")

    # Process commands regardless
    await bot.process_commands(message)


def get_music_status(guild_id):
    """Get current music playback status for a guild"""
    status = {
        'is_playing': False,
        'current_song': None,
        'queue_length': 0,
        'loop_mode': 'off'
    }

    if guild_id in now_playing:
        status['is_playing'] = True
        status['current_song'] = now_playing[guild_id].title

    if guild_id in music_queues:
        status['queue_length'] = len(music_queues[guild_id])

    if guild_id in loop_mode:
        status['loop_mode'] = loop_mode[guild_id]

    return status

def extract_spotify_title(spotify_url):
    """Try to extract song title and artist from a Spotify link (no API needed)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(spotify_url, headers=headers, timeout=15)
        html = response.text

        # Try multiple patterns for title and artist
        title_patterns = [
            r'<title>(.*?)</title>',
            r'<meta property="og:title" content="(.*?)"',
            r'<meta name="twitter:title" content="(.*?)"',
        ]

        artist_patterns = [
            r'<meta property="og:description" content=".*?by (.*?)"',
            r'<meta name="twitter:description" content=".*?by (.*?)"',
            r'<span[^>]*class="[^"]*artist[^"]*"[^>]*>(.*?)</span>',
        ]

        title = None
        artist = None

        # Extract title
        for pattern in title_patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                title_text = match.group(1)
                # Clean up the title
                clean_title = title_text.replace('| Spotify', '').replace('Spotify', '').strip()
                # Remove common prefixes/suffixes
                clean_title = re.sub(r'^(Listen to|Song:|Track:|)', '', clean_title, flags=re.IGNORECASE).strip()
                clean_title = re.sub(r'(on Spotify|by.*)$', '', clean_title, flags=re.IGNORECASE).strip()

                if clean_title and len(clean_title) > 3:  # Ensure it's not too short
                    title = clean_title
                    break

        # Extract artist
        for pattern in artist_patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                artist_text = match.group(1).strip()
                if artist_text and len(artist_text) > 1:
                    artist = artist_text
                    break

        # If we have both title and artist, return combined search term
        if title and artist:
            return f"{title} {artist}"

        # If only title, return it
        if title:
            return title

        # Try to extract from URL itself as last resort
        url_match = re.search(r'/track/([a-zA-Z0-9]+)', spotify_url)
        if url_match:
            return f"spotify track {url_match.group(1)}"

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
        await ctx.send("💢 Join a voice channel first, baka! I can't sing without you~ 🎤")
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
                        # Try multiple search variations for each track
                        search_variations = [
                            f"ytsearch:{q}",
                            f"ytsearch:{q} official",
                            f"ytsearch:{q} audio"
                        ]

                        player_found = False
                        for search_q in search_variations:
                            try:
                                player = await YTDLSource.from_url(search_q, loop=bot.loop)
                                if player and player.title:
                                    # Check if the found video title contains key words from the search for better matching
                                    search_lower = q.lower()
                                    title_lower = player.title.lower()
                                    # More lenient matching - check if any key words match
                                    search_words = search_lower.split()
                                    if any(word in title_lower for word in search_words[:3]):  # Check first 3 words
                                        music_queues[guild_id].append(player)
                                        added += 1
                                        player_found = True
                                        break
                            except Exception as e:
                                print(f"YT search error for '{search_q}': {e}")
                                continue

                        if not player_found:
                            print(f"Could not find any YouTube match for: {q}")

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

                    # Try multiple search variations for better results, prioritizing exact matches
                    search_queries = [
                        f"ytsearch:{title} official music video",
                        f"ytsearch:{title} official video",
                        f"ytsearch:{title} official",
                        f"ytsearch:{title} audio",
                        f"ytsearch:{title} lyrics",
                        f"ytsearch:{title}"
                    ]

                    player = None
                    for search_q in search_queries:
                        try:
                            player = await YTDLSource.from_url(search_q, loop=bot.loop)
                            if player and player.title:
                                # Check if the found video title contains the artist name for better matching
                                search_lower = title.lower()
                                title_lower = player.title.lower()
                                # More lenient matching - check if any key words match
                                search_words = search_lower.split()
                                if any(word in title_lower for word in search_words[:3]):  # Check first 3 words
                                    break
                        except Exception as e:
                            print(f"Search failed for '{search_q}': {e}")
                            continue

                    if player and player.title:
                        music_queues[guild_id].append(player)
                        embed = Embed(title="💖 Added to Queue", description=f"**{player.title}**", color=0xff69b4)
                        await ctx.send(embed=embed)
                    else:
                        await ctx.send("💔 Couldn't find that track on YouTube for the Spotify link. Try searching by song name instead!")
                        return

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
        await ctx.send("💔 Nothing is playing right now, Add some music! 🎤")


@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused! Taking a break, senpai~ 💖")
    else:
        await ctx.send("💢 Nothing is playing right now, baka~")


@bot.command(name='resume', aliases=['r'])
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed! Let's continue singing~ 🎤")
    else:
        await ctx.send("💢 Nothing is playing right now, baka~")


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
        await ctx.send("💖 Bye-bye, Come back soon~")
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
        "**!play <song/link>** or **!p** or **!P** or **!Play** - Play a song (YouTube, Spotify, or search)\n"
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
