import express from "express";
import {
  Client,
  GatewayIntentBits,
  EmbedBuilder
} from "discord.js";
import {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  AudioPlayerStatus,
  NoSubscriberBehavior
} from "@discordjs/voice";
import play from "play-dl";
import dotenv from "dotenv";
dotenv.config();

// 🧩 Load YouTube cookies if available
(async () => {
  try {
    if (process.env.YT_COOKIES) {
      await play.setToken({
  youtube: {
    cookie: process.env.YT_COOKIE
  }
});
      console.log("✅ YouTube cookies loaded!");
    } else {
      console.warn("⚠️ No YT_COOKIES found — YouTube playback may fail.");
    }
  } catch (err) {
    console.error("Cookie load error:", err);
  }
})();

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent
  ],
});

const prefix = "!";
const queue = new Map();
const app = express();

// keep-alive web server
app.get("/", (_, res) => res.send("Bot is alive!"));
app.listen(3000, () => console.log("🌐 Keep-alive server running on port 3000"));

client.once("ready", () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
});

client.on("messageCreate", async (message) => {
  if (message.author.bot || !message.content.startsWith(prefix)) return;

  const args = message.content.slice(prefix.length).trim().split(/ +/);
  const cmd = args.shift().toLowerCase();
  const serverQueue = queue.get(message.guild.id);

  if (cmd === "play") {
    const voiceChannel = message.member?.voice?.channel;
    if (!voiceChannel) return message.reply("❌ Join a voice channel first!");

    const permissions = voiceChannel.permissionsFor(message.client.user);
    if (!permissions.has("Connect") || !permissions.has("Speak"))
      return message.reply("❌ I don’t have permission to join/speak.");

    let songInfo;
    let song;

    try {
      const query = args.join(" ");
      if (!query) return message.reply("🎵 Provide a song name or link!");

      let source = await play.search(query, { limit: 1 });
      if (source.length === 0) return message.reply("❌ No results found!");
      songInfo = source[0];
      song = {
        title: songInfo.title,
        url: songInfo.url
      };
    } catch (err) {
      console.error(err);
      return message.reply("⚠️ Error finding song!");
    }

    if (!serverQueue) {
      const queueConstruct = {
        textChannel: message.channel,
        voiceChannel: message.member.voice.channel,
        connection: null,
        songs: [],
        player: createAudioPlayer({
          behaviors: { noSubscriber: NoSubscriberBehavior.Stop },
        }),
        loop: false,
      };

      queue.set(message.guild.id, queueConstruct);
      queueConstruct.songs.push(song);

      try {
        const connection = joinVoiceChannel({
          channelId: voiceChannel.id,
          guildId: voiceChannel.guild.id,
          adapterCreator: voiceChannel.guild.voiceAdapterCreator,
        });
        queueConstruct.connection = connection;
        playSong(message.guild, queueConstruct.songs[0]);
      } catch (err) {
        console.error(err);
        queue.delete(message.guild.id);
        return message.reply("❌ Error connecting to voice channel!");
      }
    } else {
      serverQueue.songs.push(song);
      return message.reply(`🎶 **${song.title}** added to the queue!`);
    }
  }

  if (cmd === "skip") {
    if (!serverQueue) return message.reply("❌ No songs to skip!");
    serverQueue.player.stop();
    message.reply("⏭️ Skipped!");
  }

  if (cmd === "stop") {
    if (!serverQueue) return message.reply("❌ Nothing to stop!");
    serverQueue.songs = [];
    serverQueue.player.stop();
    message.reply("⏹️ Stopped!");
  }

  if (cmd === "pause") {
    if (!serverQueue) return message.reply("❌ Nothing is playing!");
    serverQueue.player.pause();
    message.reply("⏸️ Paused!");
  }

  if (cmd === "resume") {
    if (!serverQueue) return message.reply("❌ Nothing to resume!");
    serverQueue.player.unpause();
    message.reply("▶️ Resumed!");
  }

  if (cmd === "leave") {
    if (!serverQueue) return message.reply("❌ Not connected!");
    serverQueue.voiceChannel.leave();
    queue.delete(message.guild.id);
    message.reply("👋 Left the channel!");
  }

  if (cmd === "queue") {
    if (!serverQueue || !serverQueue.songs.length)
      return message.reply("🎶 Queue is empty!");
    const list = serverQueue.songs
      .map((s, i) => `${i + 1}. ${s.title}`)
      .join("\n");
    message.reply("🎵 **Current Queue:**\n" + list);
  }

  if (cmd === "loop") {
    if (!serverQueue) return message.reply("❌ Nothing is playing!");
    serverQueue.loop = !serverQueue.loop;
    message.reply(`🔁 Loop is now **${serverQueue.loop ? "ON" : "OFF"}**`);
  }
});

async function playSong(guild, song) {
  const serverQueue = queue.get(guild.id);
  if (!song) {
    setTimeout(() => {
      if (serverQueue && serverQueue.connection) {
        serverQueue.connection.destroy();
        queue.delete(guild.id);
        console.log("💤 Auto-disconnected due to inactivity.");
      }
    }, 30000);
    return;
  }

  try {
    const stream = await play.stream(song.url);
    const resource = createAudioResource(stream.stream, {
      inputType: stream.type,
    });

    serverQueue.player.play(resource);
    serverQueue.connection.subscribe(serverQueue.player);

    serverQueue.textChannel.send(`🎶 Now playing: **${song.title}**`);

    serverQueue.player.on(AudioPlayerStatus.Idle, () => {
      if (!serverQueue.loop) serverQueue.songs.shift();
      playSong(guild, serverQueue.songs[0]);
    });
  } catch (error) {
    console.error(error);
    serverQueue.songs.shift();
    playSong(guild, serverQueue.songs[0]);
  }
}

client.login(process.env.DISCORD_TOKEN);
