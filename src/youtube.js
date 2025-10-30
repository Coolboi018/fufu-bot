import ytdl from "ytdl-core";
import playdl from "play-dl";
import { Track } from "./queue.js";
import YoutubeSR from "youtube-sr";

// Extract QueryType from the default export (since youtube-sr is CommonJS)
const { QueryType } = YoutubeSR;

export async function resolveYouTube(input, requestedBy) {
  // Input could be: direct YouTube URL, playlist URL, or search query
  if (ytdl.validateURL(input)) {
    const info = await ytdl.getInfo(input);
    const details = info.videoDetails;
    return [new Track({
      title: details.title,
      url: details.video_url,
      duration: formatDuration(details.lengthSeconds),
      source: "youtube",
      requestedBy
    })];
  }

  // Playlist URL (play-dl handles many formats)
  if (await playdl.playlist_validate(input) === "yt_playlist") {
    const playlist = await playdl.playlist_info(input, { incomplete: true });
    const videos = await playlist.all_videos();
    const tracks = videos.map(v => new Track({
      title: v.title,
      url: `https://www.youtube.com/watch?v=${v.id}`,
      duration: v.durationInSec ? formatDuration(v.durationInSec) : "Unknown",
      source: "youtube",
      requestedBy
    }));
    return tracks;
  }

  // Search query
  const results = await YoutubeSR.search(input, { type: QueryType.VIDEO, limit: 1 });
  if (results.length === 0) {
    return [];
  }
  const v = results[0];
  return [new Track({
    title: v.title,
    url: `https://www.youtube.com/watch?v=${v.id}`,
    duration: v.durationFormatted || "Unknown",
    source: "youtube",
    requestedBy
  })];
}

export function makeYouTubeStream(url) {
  // Use play-dl if possible (better resilience), fallback to ytdl-core
  return playdl.stream(url).catch(() => null);
}

function formatDuration(seconds) {
  const s = Number(seconds) || 0;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}
