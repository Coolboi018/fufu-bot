// index.js
import express from "express";
import { Client, GatewayIntentBits } from "discord.js";
import play from "play-dl";
import { joinVoiceChannel, createAudioPlayer, createAudioResource, AudioPlayerStatus, NoSubscriberBehavior } from "@discordjs/voice";
import keep_alive from "./keep_alive.js";

keep_alive();

// === EXPRESS KEEP-ALIVE SERVER ===
const app = express();
const PORT = process.env.PORT || 3000;
app.get("/", (req, res) => res.send("🌐 Bot is running!"));
app.listen(PORT, () => console.log(`🌐 Keep-alive server running on port ${PORT}`));

// === YOUTUBE COOKIE SETUP ===
const ytCookie = process.env.YT_COOKIE;
if (ytCookie) {
  try {
    await play.setToken({ youtube: { cookie: ytCookie } });
    console.log("✅ YouTube cookie loaded successfully");
  } catch (err) {
    console.error("❌ Cookie load error:", err.message);
  }
} else {
  console.warn("⚠️ No YT_COOKIE found — YouTube links may fail!");
}

// === DISCORD BOT SETUP ===
const bot = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.MessageContent
  ]
});

bot.once("ready", () => {
  console.log(`✅ Logged in as ${bot.user.tag}`);
});

const queue = new Map();

bot.on("messageCreate", async (message) => {
  if (!message.content.startsWith("!") || message.author.bot) return;

  const args = message.content.slice(1).split(" ");
  const command = args.shift().toLowerCase();

  if (command === "play") {
    const voiceChannel = message.member?.voice?.channel;
    if (!voiceChannel) return message.reply("🎤 Join a voice channel first!");

    const query = args.join(" ");
    if (!query) return message.reply("❌ Please provide a song name or link!");

    let song;
    try {
      const ytInfo = await play.search(query, { limit: 1 });
      if (!ytInfo.length) return message.reply("⚠️ No results found.");
      song = ytInfo[0];
    } catch (err) {
      console.error("🔴 Error searching song:", err);
      return message.reply("❌ Could not search that song.");
    }

    const serverQueue = queue.get(message.guild.id);

    if (!serverQueue) {
      const connection = joinVoiceChannel({
        channelId: voiceChannel.id,
        guildId: voiceChannel.guild.id,
        adapterCreator: voiceChannel.guild.voiceAdapterCreator,
      });

      const player = createAudioPlayer({
        behaviors: { noSubscriber: NoSubscriberBehavior.Play },
      });

      const newQueue = {
        voiceChannel,
        connection,
        player,
        songs: [],
        playing: true,
      };

      queue.set(message.guild.id, newQueue);
      newQueue.songs.push(song);

      playSong(message.guild, newQueue.songs[0], message);
    } else {
      serverQueue.songs.push(song);
      return message.reply(`🎶 Added to queue: **${song.title}**`);
    }
  }

  if (command === "skip") {
    const serverQueue = queue.get(message.guild.id);
    if (!serverQueue) return message.reply("⚠️ Nothing to skip!");
    serverQueue.player.stop();
  }

  if (command === "stop") {
    const serverQueue = queue.get(message.guild.id);
    if (!serverQueue) return message.reply("⚠️ Nothing is playing!");
    serverQueue.songs = [];
    serverQueue.player.stop();
    serverQueue.connection.destroy();
    queue.delete(message.guild.id);
    return message.reply("🛑 Stopped playback and left the VC!");
  }
});

// === FUNCTION TO PLAY SONG ===
async function playSong(guild, song, message) {
  const serverQueue = queue.get(guild.id);
  if (!song) {
    serverQueue.connection.destroy();
    queue.delete(guild.id);
    return;
  }

  try {
    const stream = await play.stream(song.url, { quality: 2 });
    const resource = createAudioResource(stream.stream, {
      inputType: stream.type,
    });

    serverQueue.player.play(resource);
    serverQueue.connection.subscribe(serverQueue.player);

    serverQueue.player.on(AudioPlayerStatus.Idle, () => {
      serverQueue.songs.shift();
      playSong(guild, serverQueue.songs[0], message);
    });

    serverQueue.player.on("error", (error) => {
      console.error("Audio Player Error:", error);
      message.channel.send("⚠️ Error playing the track. Skipping...");
      serverQueue.songs.shift();
      playSong(guild, serverQueue.songs[0], message);
    });

    message.channel.send(`🎵 Now playing: **${song.title}**`);
  } catch (error) {
    console.error("Stream Error:", error);
    message.channel.send("❌ Could not play that track!");
    serverQueue.songs.shift();
    playSong(guild, serverQueue.songs[0], message);
  }
}

bot.login(process.env.TOKEN);
