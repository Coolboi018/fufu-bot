const { Client, GatewayIntentBits } = require('discord.js');
const { joinVoiceChannel, createAudioPlayer, createAudioResource, AudioPlayerStatus, getVoiceConnection, NoSubscriberBehavior } = require('@discordjs/voice');
const play = require('play-dl');
const keepAlive = require('./keepAlive');

const PREFIX = '!';
const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent] });
const queue = new Map();

keepAlive();

client.once('ready', () => {
  console.log(`Logged in as ${client.user.tag}`);
});

client.on('messageCreate', async message => {
  if (!message.content.startsWith(PREFIX) || message.author.bot) return;

  const args = message.content.slice(PREFIX.length).trim().split(/ +/);
  const command = args.shift().toLowerCase();
  const serverQueue = queue.get(message.guild.id);

  if (command === 'play') {
    const voiceChannel = message.member.voice.channel;
    if (!voiceChannel) return message.reply('Join a voice channel first!');
    const songQuery = args.join(' ');
    if (!songQuery) return message.reply('Provide a song name or link!');

    let songs = [];

    try {
      if (play.is_expired()) await play.refreshToken();
      await play.spotify_login(process.env.SPOTIFY_CLIENT_ID, process.env.SPOTIFY_CLIENT_SECRET);

      if (play.yt_validate(songQuery) === 'playlist') {
        const playlist = await play.playlist_info(songQuery, { incomplete: true });
        const videos = await playlist.all_videos();
        songs = videos.map(v => ({ title: v.title, url: v.url }));
      } else if (play.sp_validate(songQuery) === 'track') {
        const spData = await play.spotify(songQuery);
        const yt = await play.search(`${spData.name} ${spData.artists[0].name}`, { limit: 1 });
        songs.push({ title: yt[0].title, url: yt[0].url });
      } else if (play.sp_validate(songQuery) === 'playlist') {
        const spList = await play.spotify(songQuery);
        const tracks = await spList.all_tracks();
        for (const track of tracks) {
          const yt = await play.search(`${track.name} ${track.artists[0].name}`, { limit: 1 });
          songs.push({ title: yt[0].title, url: yt[0].url });
        }
      } else {
        const yt = await play.search(songQuery, { limit: 1 });
        songs.push({ title: yt[0].title, url: yt[0].url });
      }
    } catch (err) {
      console.error(err);
      return message.reply('Error fetching song.');
    }

    if (!serverQueue) {
      const queueContruct = {
        voiceChannel,
        connection: null,
        player: null,
        songs: [],
        loop: false
      };

      queue.set(message.guild.id, queueContruct);
      queueContruct.songs.push(...songs);

      try {
        const connection = joinVoiceChannel({
          channelId: voiceChannel.id,
          guildId: message.guild.id,
          adapterCreator: message.guild.voiceAdapterCreator
        });

        const player = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } });
        queueContruct.connection = connection;
        queueContruct.player = player;

        connection.subscribe(player);
        playSong(message.guild, queueContruct.songs[0]);

        player.on(AudioPlayerStatus.Idle, () => {
          if (queueContruct.loop) {
            playSong(message.guild, queueContruct.songs[0]);
          } else {
            queueContruct.songs.shift();
            if (queueContruct.songs.length) {
              playSong(message.guild, queueContruct.songs[0]);
            } else {
              connection.destroy();
              queue.delete(message.guild.id);
            }
          }
        });

      } catch (err) {
        console.error(err);
        queue.delete(message.guild.id);
        return message.reply('Error connecting to voice channel.');
      }
    } else {
      serverQueue.songs.push(...songs);
      message.reply(`Added ${songs.length} song(s) to the queue.`);
    }
  }

  if (command === 'skip') {
    if (!serverQueue) return message.reply('Nothing to skip!');
    serverQueue.player.stop();
    message.reply('Skipped!');
  }

  if (command === 'stop' || command === 'leave') {
    if (!serverQueue) return message.reply('Not playing anything.');
    serverQueue.connection.destroy();
    queue.delete(message.guild.id);
    message.reply('Stopped and left the channel.');
  }

  if (command === 'pause') {
    if (!serverQueue) return message.reply('Nothing to pause.');
    serverQueue.player.pause();
    message.reply('Paused.');
  }

  if (command === 'resume') {
    if (!serverQueue) return message.reply('Nothing to resume.');
    serverQueue.player.unpause();
    message.reply('Resumed.');
  }

  if (command === 'loop') {
    if (!serverQueue) return message.reply('Nothing playing.');
    serverQueue.loop = !serverQueue.loop;
    message.reply(`Loop is now ${serverQueue.loop ? 'enabled' : 'disabled'}.`);
  }

  if (command === 'queue') {
    if (!serverQueue) return message.reply('Queue is empty.');
    const titles = serverQueue.songs.map((s, i) => `${i + 1}. ${s.title}`).join('\n');
    message.reply(`**Queue:**\n${titles}`);
  }
});

async function playSong(guild, song) {
  const serverQueue = queue.get(guild.id);
  if (!song) return;

  const stream = await play.stream(song.url);
  const resource = createAudioResource(stream.stream, { inputType: stream.type });
  serverQueue.player.play(resource);
}

client.login(process.env.DISCORD_TOKEN);
