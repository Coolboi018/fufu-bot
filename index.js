import express from "express";
import { Client, GatewayIntentBits } from "discord.js";
import play from "play-dl";
import { createAudioPlayer, createAudioResource, joinVoiceChannel, AudioPlayerStatus, NoSubscriberBehavior } from "@discordjs/voice";
import dotenv from "dotenv";
dotenv.config();

const app = express();
const PORT = process.env.PORT || 10000;
app.get("/", (req, res) => res.send("🌐 Web server running for Render"));
app.listen(PORT, () => console.log("🌐 Web server running for Render"));

const bot = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent, GatewayIntentBits.GuildVoiceStates]
});

const queue = new Map();

bot.once("ready", () => console.log(`✅ Logged in as ${bot.user.tag}`));

bot.on("messageCreate", async (message) => {
  if (!message.content.startsWith("!") || message.author.bot) return;

  const serverQueue = queue.get(message.guild.id);
  const args = message.content.split(" ");
  const command = args.shift().toLowerCase();

  if (command === "!play") {
    const query = args.join(" ");
    if (!query) return message.reply("❌ Please provide a song name or link!");

    const voiceChannel = message.member?.voice?.channel;
    if (!voiceChannel) return message.reply("❌ You must join a voice channel first!");

    let yt_info;
    try {
      // Prevent play-dl crash if session is undefined
      if (!play.is_expired || typeof play.is_expired !== "function") {
        console.warn("⚠️ Skipping session expiry check due to missing session.");
      } else {
        const session = await play.getFreeClientID();
        if (session && play.is_expired(session)) await play.refreshToken();
      }

      if (play.yt_validate(query) === "video") {
        yt_info = await play.video_info(query);
      } else {
        const search = await play.search(query, { limit: 1 });
        yt_info = search[0];
      }
    } catch (err) {
      console.error("Error fetching video:", err);
      return message.reply("❌ Could not find or play that song. Try another one!");
    }

    if (!yt_info) return message.reply("❌ Song not found.");

    const song = {
      title: yt_info.title || "Unknown Title",
      url: yt_info.url,
    };

    if (!serverQueue) {
      const queueConstruct = {
        voiceChannel,
        connection: null,
        songs: [],
        player: createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Play } }),
        playing: true,
      };

      queue.set(message.guild.id, queueConstruct);
      queueConstruct.songs.push(song);

      try {
        const connection = joinVoiceChannel({
          channelId: voiceChannel.id,
          guildId: message.guild.id,
          adapterCreator: message.guild.voiceAdapterCreator,
        });
        queueConstruct.connection = connection;
        playSong(message.guild, queueConstruct.songs[0], message);
      } catch (err) {
        console.error("Voice connection error:", err);
        queue.delete(message.guild.id);
        return message.reply("❌ Could not join voice channel.");
      }
    } else {
      serverQueue.songs.push(song);
      return message.reply(`🎶 Added to queue: **${song.title}**`);
    }
  }

  if (command === "!skip") skipSong(message, serverQueue);
  if (command === "!stop") stopSong(message, serverQueue);
});

async function playSong(guild, song, message) {
  const serverQueue = queue.get(guild.id);
  if (!song) {
    serverQueue.connection.destroy();
    queue.delete(guild.id);
    return;
  }

  try {
    const stream = await play.stream(song.url);
    const resource = createAudioResource(stream.stream, { inputType: stream.type });
    const player = serverQueue.player;

    player.play(resource);
    serverQueue.connection.subscribe(player);

    player.on(AudioPlayerStatus.Idle, () => {
      serverQueue.songs.shift();
      playSong(guild, serverQueue.songs[0], message);
    });

    message.channel.send(`🎵 Now playing: **${song.title}**`);
  } catch (err) {
    console.error("Playback error:", err);
    message.channel.send("⚠️ Skipping broken song...");
    serverQueue.songs.shift();
    playSong(guild, serverQueue.songs[0], message);
  }
}

function skipSong(message, serverQueue) {
  if (!serverQueue) return message.reply("❌ No song is playing.");
  serverQueue.player.stop();
  message.channel.send("⏭️ Skipped!");
}

function stopSong(message, serverQueue) {
  if (!serverQueue) return message.reply("❌ No song is playing.");
  serverQueue.songs = [];
  serverQueue.player.stop();
  serverQueue.connection.destroy();
  queue.delete(message.guild.id);
  message.channel.send("🛑 Stopped the music!");
}

bot.login(process.env.TOKEN);
