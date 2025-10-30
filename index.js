require("dotenv").config();
const { Client, GatewayIntentBits, Partials } = require("discord.js");
const {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  AudioPlayerStatus,
  NoSubscriberBehavior,
  getVoiceConnection
} = require("@discordjs/voice");
const play = require("play-dl");
const express = require("express");

const app = express();
const PORT = process.env.PORT || 3000;
const PREFIX = "!";
const AUTO_LEAVE_MS = 5 * 60 * 1000; // 5 mins

if (!process.env.DISCORD_TOKEN) {
  console.error("❌ Please set DISCORD_TOKEN in environment variables");
  process.exit(1);
}

// optional Spotify login
(async () => {
  if (process.env.SPOTIFY_CLIENT_ID && process.env.SPOTIFY_CLIENT_SECRET) {
    try {
      await play.spotify.login({
        clientId: process.env.SPOTIFY_CLIENT_ID,
        clientSecret: process.env.SPOTIFY_CLIENT_SECRET,
      });
      console.log("✅ Spotify login successful");
    } catch {
      console.warn("⚠️ Spotify login failed, using fallback search");
    }
  }
})();

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
  partials: [Partials.Channel],
});

const guildMap = new Map();

function createGuildQueue(guildId) {
  return {
    connection: null,
    player: createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } }),
    queue: [],
    loop: false,
    nowPlaying: null,
    idleTimeout: null,
  };
}

async function playNext(guildId) {
  const g = guildMap.get(guildId);
  if (!g) return;

  if (g.queue.length === 0) {
    g.nowPlaying = null;
    if (g.idleTimeout) clearTimeout(g.idleTimeout);
    g.idleTimeout = setTimeout(() => {
      const conn = getVoiceConnection(guildId);
      if (conn) conn.destroy();
      guildMap.delete(guildId);
    }, AUTO_LEAVE_MS);
    return;
  }

  const track = g.loop ? g.nowPlaying : g.queue.shift();
  g.nowPlaying = track;

  try {
    if (g.idleTimeout) clearTimeout(g.idleTimeout);
    const stream = await getStream(track.url);
    const resource = createAudioResource(stream.stream, { inputType: stream.type });
    g.player.play(resource);

    g.player.once(AudioPlayerStatus.Idle, () => {
      if (!g.loop) setImmediate(() => playNext(guildId));
      else setImmediate(() => playNext(guildId));
    });
  } catch (err) {
    console.error("Error playing track:", err);
    setImmediate(() => playNext(guildId));
  }
}

async function getStream(query) {
  if (play.is_expired()) await play.refreshToken();

  if (play.is_spotify(query)) {
    const info = await play.spotify(query);
    const search = `${info.name} ${info.artists.map(a => a.name).join(" ")}`;
    const yt = await play.search(search, { limit: 1 });
    if (yt && yt.length > 0) return play.stream(yt[0].url, { discordPlayerCompatibility: true });
  }

  if (play.is_youtube(query) || play.is_youtube_video(query))
    return play.stream(query, { discordPlayerCompatibility: true });

  const results = await play.search(query, { limit: 1 });
  if (results && results.length > 0) return play.stream(results[0].url, { discordPlayerCompatibility: true });

  throw new Error("No results");
}

client.once("ready", () => {
  console.log(`🤖 Logged in as ${client.user.tag}`);
});

client.on("messageCreate", async (msg) => {
  if (msg.author.bot || !msg.guild) return;
  if (!msg.content.startsWith(PREFIX)) return;

  const args = msg.content.slice(PREFIX.length).trim().split(/ +/);
  const cmd = args.shift().toLowerCase();

  const g = guildMap.get(msg.guild.id) || createGuildQueue(msg.guild.id);
  guildMap.set(msg.guild.id, g);

  const vc = msg.member.voice.channel;

  switch (cmd) {
    case "play":
      if (!vc) return msg.reply("Join a voice channel first!");
      const query = args.join(" ");
      if (!query) return msg.reply("Usage: !play <song name | YouTube | Spotify>");
      if (!g.connection) {
        g.connection = joinVoiceChannel({
          channelId: vc.id,
          guildId: msg.guild.id,
          adapterCreator: msg.guild.voiceAdapterCreator,
        });
        g.connection.subscribe(g.player);
      }
      g.queue.push({ title: query, url: query });
      msg.reply(`�
