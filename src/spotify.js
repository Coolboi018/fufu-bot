import SpotifyWebApi from "spotify-web-api-node";
import playdl from "play-dl";
import { Track } from "./queue.js";

// Initialize Spotify API client
const spotify = new SpotifyWebApi({
  clientId: process.env.SPOTIFY_CLIENT_ID,
  clientSecret: process.env.SPOTIFY_CLIENT_SECRET
});

let lastTokenAt = 0;

async function ensureSpotifyToken() {
  const now = Date.now();
  if (now - lastTokenAt < 300000) return; // 5 minutes cache
  const data = await spotify.clientCredentialsGrant();
  spotify.setAccessToken(data.body.access_token);
  lastTokenAt = now;
}

// Resolve Spotify URL or search query into Tracks, playable via YouTube audio
export async function resolveSpotify(input, requestedBy) {
  await ensureSpotifyToken();

  const kind = await playdl.spotify(input).catch(() => null);

  // If it's a Spotify URL (track/album/playlist)
  if (kind) {
    const type = kind.type; // "track" | "album" | "playlist"
    if (type === "track") {
      const t = kind;
      const title = `${t.name} ${t.artists?.map(a => a.name).join(" ")}`;
      const yt = await searchYouTubeBest(title);
      return yt ? [makeTrackFromYT(yt, requestedBy)] : [];
    } else if (type === "album") {
      const tracks = await kind.all_tracks();
      const resolved = [];
      for (const t of tracks) {
        const title = `${t.name} ${t.artists?.map(a => a.name).join(" ")}`;
        const yt = await searchYouTubeBest(title);
        if (yt) resolved.push(makeTrackFromYT(yt, requestedBy));
      }
      return resolved;
    } else if (type === "playlist") {
      const tracks = await kind.all_tracks();
      const resolved = [];
      for (const t of tracks) {
        const title = `${t.name} ${t.artists?.map(a => a.name).join(" ")}`;
        const yt = await searchYouTubeBest(title);
        if (yt) resolved.push(makeTrackFromYT(yt, requestedBy));
      }
      return resolved;
    }
  }

  // Not a Spotify URL: treat as a search; try Spotify then YouTube
  const search = await spotify.searchTracks(input, { limit: 1 });
  if (search.body.tracks.items.length) {
    const t = search.body.tracks.items[0];
    const title = `${t.name} ${t.artists.map(a => a.name).join(" ")}`;
    const yt = await searchYouTubeBest(title);
    return yt ? [makeTrackFromYT(yt, requestedBy)] : [];
  }

  // Fallback: no result
  return [];
}

async function searchYouTubeBest(query) {
  const result = await playdl.search(query, { limit: 1 });
  return result[0] || null;
}

function makeTrackFromYT(yt, requestedBy) {
  return new Track({
    title: yt.title,
    url: yt.url,
    duration: yt.durationInSec ? formatDuration(yt.durationInSec) : "Unknown",
    source: "spotify",
    requestedBy
  });
}

function formatDuration(seconds) {
  const s = Number(seconds) || 0;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}
