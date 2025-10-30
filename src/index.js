import { Client, GatewayIntentBits, Partials } from "discord.js";
import { PREFIX, MAX_QUEUE_LENGTH } from "./config.js";
import { makeWeb } from "./web.js";
import { GuildQueue, Track } from "./queue.js";
import { GuildAudioController } from "./player.js";
import { resolveYouTube } from "./youtube.js";
import { resolveSpotify } from "./spotify.js";
import playdl from "play-dl";

// Start a minimal web server for Render
makeWeb();
// Force load native voice dependencies
import "libsodium-wrappers";
import "@discordjs/opus";
// Discord client
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.MessageContent
  ],
  partials: [Partials.Channel]
});

// Per-guild state
const queues = new Map(); // guildId -> GuildQueue
const controllers = new Map(); // guildId -> GuildAudioController

function getQueue(guildId) {
  if (!queues.has(guildId)) queues.set(guildId, new GuildQueue());
  return queues.get(guildId);
}

function getController(guild) {
  const q = getQueue(guild.id);
  if (!controllers.has(guild.id)) controllers.set(guild.id, new GuildAudioController(guild.id, q));
  return controllers.get(guild.id);
}

client.on("ready", async () => {
  console.log(`Logged in as ${client.user.tag}`);
  // Prepare play-dl for YouTube & Spotify
  try {
    if (await playdl.is_spotify_playable()) {
      console.log("[Spotify] play-dl is ready");
    }
  } catch (e) {
    console.warn("[Spotify] play-dl not fully ready:", e.message);
  }
});

// Message command handling
client.on("messageCreate", async (msg) => {
  if (msg.author.bot || !msg.guild) return;
  if (!msg.content.startsWith(PREFIX)) return;

  const [cmd, ...args] = msg.content.slice(PREFIX.length).trim().split(/\s+/);
  const command = cmd.toLowerCase();

  try {
    if (command === "play") {
      await handlePlay(msg, args);
    } else if (command === "pause") {
      await handlePause(msg);
    } else if (command === "resume") {
      await handleResume(msg);
    } else if (command === "skip") {
      await handleSkip(msg);
    } else if (command === "queue") {
      await handleQueue(msg);
    } else if (command === "loop") {
      await handleLoop(msg);
    } else if (command === "leave") {
      await handleLeave(msg);
    } else if (command === "stop") {
      await handleStop(msg);
    }
  } catch (e) {
    console.error(e);
    msg.channel.send(`Error: ${e.message}`);
  }
});

async function handlePlay(msg, args) {
  const query = args.join(" ");
  if (!query) {
    msg.channel.send("Usage: !play <YouTube/Spotify link or search terms>");
    return;
  }

  const member = await msg.guild.members.fetch(msg.author.id);
  const voice = member.voice?.channel;
  if (!voice) {
    msg.channel.send("Join a voice channel first.");
    return;
  }

  const guildId = msg.guild.id;
  const queue = getQueue(guildId);

  // Resolve input to tracks (YouTube or Spotify)
  let tracks = [];
  const isSpotify = query.includes("spotify.com");
  const isYouTube = query.includes("youtube.com") || query.includes("youtu.be");

  if (isSpotify) {
    tracks = await resolveSpotify(query, msg.author.tag);
  } else if (isYouTube) {
    tracks = await resolveYouTube(query, msg.author.tag);
  } else {
    // Try YouTube search first; if no result, fallback to Spotify
    tracks = await resolveYouTube(query, msg.author.tag);
    if (tracks.length === 0) {
      tracks = await resolveSpotify(query, msg.author.tag);
    }
  }

  if (tracks.length === 0) {
    msg.channel.send("No matching tracks found.");
    return;
  }

  // Enforce queue limit
  if (queue.length() + tracks.length > MAX_QUEUE_LENGTH) {
    msg.channel.send(`Queue limit (${MAX_QUEUE_LENGTH}) exceeded.`);
    return;
  }

  queue.addMany(tracks);

  if (tracks.length === 1) {
    msg.channel.send(`Queued: ${tracks[0].title}`);
  } else {
    msg.channel.send(`Queued ${tracks.length} tracks.`);
  }

  const controller = getController(msg.guild);
  // If nothing is playing, start
  const isPlaying =
    controller.player.state.status !== "idle" &&
    controller.player.state.status !== "autopaused";

  if (!isPlaying) {
    const started = await controller.start(voice);
    if (started) {
      msg.channel.send(`Now playing: ${controller.queue.current.title}`);
    } else {
      msg.channel.send("Queue is empty.");
    }
  }
}

async function handlePause(msg) {
  const controller = controllers.get(msg.guild.id);
  if (!controller) return msg.channel.send("Nothing is playing.");
  controller.pause();
  msg.channel.send("Paused.");
}

async function handleResume(msg) {
  const controller = controllers.get(msg.guild.id);
  if (!controller) return msg.channel.send("Nothing is playing.");
  controller.resume();
  msg.channel.send("Resumed.");
}

async function handleSkip(msg) {
  const controller = controllers.get(msg.guild.id);
  if (!controller) return msg.channel.send("Nothing is playing.");
  controller.skip();
  msg.channel.send("Skipped.");
}

async function handleQueue(msg) {
  const queue = queues.get(msg.guild.id);
  if (!queue || (!queue.current && queue.length() === 0)) {
    msg.channel.send("Queue is empty.");
    return;
  }
  const lines = [];
  if (queue.current) {
    lines.push(`Now: ${queue.current.title} [${queue.current.duration}]`);
  }
  queue.tracks.slice(0, 10).forEach((t, i) => {
    lines.push(`${i + 1}. ${t.title} [${t.duration}]`);
  });
  const more = queue.tracks.length - 10;
  if (more > 0) lines.push(`...and ${more} more`);
  msg.channel.send(lines.join("\n"));
}

async function handleLoop(msg) {
  const queue = getQueue(msg.guild.id);
  const newVal = !queue.getLoop();
  queue.setLoop(newVal);
  msg.channel.send(`Loop is now ${newVal ? "enabled" : "disabled"}.`);
}

async function handleLeave(msg) {
  const controller = controllers.get(msg.guild.id);
  if (!controller) return msg.channel.send("Not connected.");
  controller.leave();
  msg.channel.send("Left the voice channel.");
}

async function handleStop(msg) {
  const controller = controllers.get(msg.guild.id);
  if (!controller) return msg.channel.send("Nothing is playing.");
  controller.stop();
  msg.channel.send("Stopped and cleared the queue.");
}

client.login(process.env.DISCORD_TOKEN);
