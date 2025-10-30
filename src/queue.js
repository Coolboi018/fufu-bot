export class Track {
  constructor({ title, url, duration, source, requestedBy }) {
    this.title = title;
    this.url = url;
    this.duration = duration; // string like "3:45" or number seconds
    this.source = source; // "youtube" | "spotify"
    this.requestedBy = requestedBy;
  }
}

export class GuildQueue {
  constructor() {
    this.tracks = [];
    this.current = null;
    this.loop = false; // loop single track
    this.idleTimer = null;
  }

  add(track) {
    this.tracks.push(track);
  }

  addMany(tracks) {
    this.tracks.push(...tracks);
  }

  next() {
    if (this.loop && this.current) {
      return this.current;
    }
    this.current = this.tracks.shift() || null;
    return this.current;
  }

  clear() {
    this.tracks = [];
  }

  length() {
    return this.tracks.length;
  }

  setLoop(value) {
    this.loop = !!value;
  }

  getLoop() {
    return this.loop;
  }
}
