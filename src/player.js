import {
  joinVoiceChannel,
  createAudioPlayer,
  NoSubscriberBehavior,
  createAudioResource,
  AudioPlayerStatus,
  VoiceConnectionStatus,
  entersState,
  demuxProbe
} from "@discordjs/voice";
import { makeYouTubeStream } from "./youtube.js";
import { AUTO_LEAVE_MS } from "./config.js";

export class GuildAudioController {
  constructor(guildId, queue) {
    this.guildId = guildId;
    this.queue = queue;
    this.connection = null;
    this.player = createAudioPlayer({
      behaviors: { noSubscriber: NoSubscriberBehavior.Pause }
    });
    this.currentResource = null;

    this.player.on(AudioPlayerStatus.Idle, () => this.onIdle());
    this.player.on("error", (e) => {
      console.error(`[Audio] Player error: ${e.message}`);
      this.onIdle();
    });
  }

  async connect(channel) {
    this.connection = joinVoiceChannel({
      channelId: channel.id,
      guildId: channel.guild.id,
      adapterCreator: channel.guild.voiceAdapterCreator,
      selfDeaf: true
    });

    this.connection.on(VoiceConnectionStatus.Disconnected, async () => {
      try {
        await Promise.race([
          entersState(this.connection, VoiceConnectionStatus.Signalling, 5000),
          entersState(this.connection, VoiceConnectionStatus.Connecting, 5000)
        ]);
      } catch {
        this.connection.destroy();
        this.connection = null;
      }
    });

    this.connection.subscribe(this.player);
  }

  async play(track) {
    const streamObj = await makeYouTubeStream(track.url);
    if (!streamObj) throw new Error("Failed to create stream");

    let resource;
    if (streamObj.type) {
      // play-dl provides type (usually StreamType.Opus)
      resource = createAudioResource(streamObj.stream, {
        inputType: streamObj.type
      });
    } else {
      // ytdl-core fallback: probe the stream to detect type
      const probe = await demuxProbe(streamObj.stream);
      resource = createAudioResource(probe.stream, {
        inputType: probe.type
      });
    }

    this.currentResource = resource;
    this.player.play(resource);
  }

  async start(channel) {
    if (!this.connection) await this.connect(channel);
    const track = this.queue.next();
    if (!track) {
      this.scheduleAutoLeave();
      return false;
    }
    await this.play(track);
    return true;
  }

  scheduleAutoLeave() {
    this.clearAutoLeave();
    this.queue.idleTimer = setTimeout(() => this.leave(), AUTO_LEAVE_MS);
  }

  clearAutoLeave() {
    if (this.queue.idleTimer) {
      clearTimeout(this.queue.idleTimer);
      this.queue.idleTimer = null;
    }
  }

  async onIdle() {
    if (this.queue.getLoop() && this.queue.current) {
      try {
        await this.play(this.queue.current);
        return;
      } catch (e) {
        console.error("[Audio] Replay failed:", e.message);
      }
    }
    const next = this.queue.next();
    if (!next) {
      this.scheduleAutoLeave();
      return;
    }
    try {
      await this.play(next);
    } catch (e) {
      console.error("[Audio] Next track failed:", e.message);
      this.onIdle(); // try advancing again if a track fails
    }
  }

  pause() {
    return this.player.pause(true);
  }

  resume() {
    return this.player.unpause();
  }

  stop() {
    this.queue.clear();
    this.player.stop(true);
    this.scheduleAutoLeave();
  }

  skip() {
    this.player.stop(true); // triggers onIdle -> plays next
  }

  leave() {
    try {
      this.player.stop(true);
    } catch {}
    this.clearAutoLeave();
    if (this.connection) {
      this.connection.destroy();
      this.connection
