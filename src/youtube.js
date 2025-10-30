import ytdl from "ytdl-core";
import playdl from "play-dl";
import { Track } from "./queue.js";
import YoutubeSR from "youtube-sr";

const { QueryType } = YoutubeSR;

// If YT_COOKIE is set in Render env vars, pass it to play-dl
if (process.env.YT_COOKIE) {
  try {
    await playdl.setToken({
      youtube: { cookie: process.env.YT_COOKIE }
    });
    console.log("[YouTube] Cookie set for play-dl");
  } catch (e) {
    console.warn("[YouTube] Failed to set cookie:", e.message);
  }
}

export async function resolveYouTube(input, requestedBy) {
  // Direct YouTube URL
  if (ytdl.validateURL(input)) {
    const info = await ytdl.getInfo(input);
    const details = info.videoDetails;
    return [
      new Track({
        title: details.title,
        url: details.video_url,
        duration: formatDuration(details.lengthSeconds),
        source: "youtube",
        requestedBy
      })
    ];
  }

  // Playlist URL
  const playlistType = await playdl.playlist_validate(input).catch(() => "error");
  if (playlistType === "yt_playlist") {
    const playlist = await playdl.playlist_info(input, { incomplete: true });
    const videos = await playlist.all_videos();
    return videos.map(v => new Track({
      title: v.title,
      url: `https://www.youtube.com/watch?v=${v.id}`,
      duration: v.durationInSec ? formatDuration(v.durationInSec) : "Unknown",
      source: "youtube",
      requestedBy
    }));
  }

  // Search query
  const results = await YoutubeSR.search(input, { type: QueryType.VIDEO, limit: 1 });
  if (!results.length) return [];
  const v = results[0];
  return [
    new Track({
      title: v.title,
      url: `https://www.youtube.com/watch?v=${v.id}`,
      duration: v.durationFormatted || "Unknown",
      source: "youtube",
      requestedBy
    })
  ];
}

// Create a stream for playback
export async function makeYouTubeStream(url) {
  // Try play-dl first
  try {
    const s = await playdl.stream(url);
    return { stream: s.stream, type: s.type, via: "playdl" };
  } catch (e) {
    console.warn(`[Stream] play-dl failed: ${e.message}`);
  }

  // Fallback to ytdl-core with cookie
  try {
    const ytdlStream = ytdl(url, {
      quality: "highestaudio",
      filter: "audioonly",
      requestOptions: {
        headers: {
          cookie: process.env.YT_COOKIE || ""
        }
      },
      dlChunkSize: 0,
      highWaterMark: 1 << 25
    });
    return { stream: ytdlStream, type: null, via: "ytdl" };
  } catch (e) {
    console.error("[Stream] ytdl-core fallback failed:", e);
    return null;
  }
}

function formatDuration(seconds) {
  const s = Number(seconds) || 0;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}
