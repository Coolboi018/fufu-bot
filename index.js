require("dotenv").config();
const { Client, GatewayIntentBits } = require("discord.js");
const {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  AudioPlayerStatus,
  NoSubscriberBehavior,
  getVoiceConnection,
} = require("@discordjs/voice");
const play = require("play-dl");
const express = require("express");

const app = express();
app.get("/", (req, res) => res.send("Bot is alive!"));
app.listen(3000, () => console.log("🌐 Web server running for Render"));

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildVoiceStates,
  ],
});

const PREFIX = "!";
let queue = [];
let isLooping = false;
let player = createAudioPlayer({
  behaviors: { noSubscriber: NoSubscriberBehavior.Pause },
});

client.once("ready", () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
});

async function playSong(guildId, textChannel) {
  const serverQueue = queue.find((q) => q.guildId === guildId);
  if (!serverQueue || !serverQueue.songs.length) {
    const connection = getVoiceConnection(guildId);
    if (connection) connection.destroy();
    return;
  }

  const song = serverQueue.songs[0];
  try {
    const source = await play.stream(song.url);
    const resource = createAudioResource(source.stream, {
      inputType: source.type,
    });
    player.play(resource);
    serverQueue.connection.subscribe(player);

    textChannel.send(`🎶 Now playing: **${song.title}**`);
  } catch (err) {
    console.error(err);
    serverQueue.songs.shift();
    playSong(guildId, textChannel);
  }
}

player.on(AudioPlayerStatus.Idle, () => {
  queue.forEach((serverQueue) => {
    if (serverQueue.songs.length > 0) {
      if (!isLooping) serverQueue.songs.shift();
      playSong(serverQueue.guildId, serverQueue.textChannel);
    }
  });
});

client.on("messageCreate", async (message) => {
  if (message.author.bot || !message.content.startsWith(PREFIX)) return;

  const args = message.content.slice(PREFIX.length).trim().split(/ +/);
  const command = args.shift().toLowerCase();

  if (command === "play") {
    if (!message.member.voice.channel)
      return message.reply("❌ Join a voice channel first!");

    const query = args.join(" ");
    if (!query) return message.reply("🎵 Provide a YouTube/Spotify link or song name!");

    let songInfo;
    let songURL = query;

    try {
      if (play.is_expired()) await play.refreshToken();

      if (play.sp_validate(query)) {
        const spData = await play.spotify(query);
        if (spData.type === "track") {
          const yt = await play.search(`${spData.name} ${spData.artists[0].name}`, { limit: 1 });
          songInfo = { title: yt[0].title, url: yt[0].url };
        } else if (spData.type === "playlist") {
          const tracks = await spData.all_tracks();
          const ytTracks = [];
          for (const track of tracks) {
            const yt = await play.search(`${track.name} ${track.artists[0].name}`, { limit: 1 });
            if (yt.length) ytTracks.push({ title: yt[0].title, url: yt[0].url });
          }
          if (!ytTracks.length) return message.reply("⚠️ Couldn't load playlist.");
          let serverQueue = queue.find((q) => q.guildId === message.guild.id);
          if (!serverQueue) {
            const connection = joinVoiceChannel({
              channelId: message.member.voice.channel.id,
              guildId: message.guild.id,
              adapterCreator: message.guild.voiceAdapterCreator,
            });
            serverQueue = {
              guildId: message.guild.id,
              textChannel: message.channel,
              connection,
              songs: [],
            };
            queue.push(serverQueue);
          }
          serverQueue.songs.push(...ytTracks);
          return message.reply(`📜 Added **${ytTracks.length}** songs from Spotify playlist!`);
        }
      } else if (!query.startsWith("http")) {
        const yt = await play.search(query, { limit: 1 });
        songInfo = { title: yt[0].title, url: yt[0].url };
      } else {
        const ytInfo = await play.video_info(query);
        songInfo = { title: ytInfo.video_details.title, url: ytInfo.video_details.url };
      }
    } catch (err) {
      console.error(err);
      return message.reply("⚠️ Failed to find the song!");
    }

    let serverQueue = queue.find((q) => q.guildId === message.guild.id);
    if (!serverQueue) {
      const connection = joinVoiceChannel({
        channelId: message.member.voice.channel.id,
        guildId: message.guild.id,
        adapterCreator: message.guild.voiceAdapterCreator,
      });
      serverQueue = {
        guildId: message.guild.id,
        textChannel: message.channel,
        connection,
        songs: [],
      };
      queue.push(serverQueue);
    }

    serverQueue.songs.push(songInfo);
    message.reply(`🎧 Added to queue: **${songInfo.title}**`);

    if (serverQueue.songs.length === 1) playSong(message.guild.id, message.channel);
  }

  if (command === "skip") {
    player.stop();
    message.reply("⏭️ Skipped the song!");
  }

  if (command === "pause") {
    player.pause();
    message.reply("⏸️ Paused!");
  }

  if (command === "resume") {
    player.unpause();
    message.reply("▶️ Resumed!");
  }

  if (command === "stop") {
    const serverQueue = queue.find((q) => q.guildId === message.guild.id);
    if (serverQueue) serverQueue.songs = [];
    player.stop();
    message.reply("🛑 Stopped the music!");
  }

  if (command === "queue") {
    const serverQueue = queue.find((q) => q.guildId === message.guild.id);
    if (!serverQueue || !serverQueue.songs.length)
      return message.reply("🚫 Queue is empty!");
    const q = serverQueue.songs.map((s, i) => `${i + 1}. ${s.title}`).join("\n");
    message.reply(`🎶 **Queue:**\n${q}`);
  }

  if (command === "loop") {
    isLooping = !isLooping;
    message.reply(isLooping ? "🔁 Loop enabled!" : "➡️ Loop disabled!");
  }

  if (command === "leave") {
    const connection = getVoiceConnection(message.guild.id);
    if (connection) connection.destroy();
    queue = queue.filter((q) => q.guildId !== message.guild.id);
    message.reply("👋 Left the voice channel!");
  }
});

client.login(process.env.DISCORD_TOKEN);
