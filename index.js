javascript
if (!g) return message.reply('Nothing to stop');
g.queue = [];
g.loop = false;
g.player.stop(true);
const conn = getVoiceConnection(message.guild.id);
if (conn) conn.destroy();
guildMap.delete(message.guild.id);
message.reply('Stopped and left the channel');
return;
}


if (cmd === 'queue') {
const g = guildMap.get(message.guild.id);
if (!g || (g.queue.length === 0 && !g.nowPlaying)) return message.reply('Queue is empty');
let text = '';
if (g.nowPlaying) text += `Now: ${g.nowPlaying.title || g.nowPlaying.url}\n`;
for (let i = 0; i < Math.min(g.queue.length, 10); i++) {
text += `${i+1}. ${g.queue[i].title || g.queue[i].url}\n`;
}
message.reply(text);
return;
}


if (cmd === 'loop') {
const g = guildMap.get(message.guild.id);
if (!g) return message.reply('Nothing playing to loop');
g.loop = !g.loop;
message.reply(`Loop is now ${g.loop ? 'enabled' : 'disabled'}`);
return;
}


if (cmd === 'leave' || cmd === 'disconnect') {
const conn = getVoiceConnection(message.guild.id);
if (conn) conn.destroy();
guildMap.delete(message.guild.id);
message.reply('Left the voice channel');
return;
}


if (cmd === 'now') {
const g = guildMap.get(message.guild.id);
if (!g || !g.nowPlaying) return message.reply('Nothing is playing');
return message.reply(`Now playing: ${g.nowPlaying.title || g.nowPlaying.url}`);
}


});


function isValidURL(s) {
try {
new URL(s);
return true;
} catch (e) {
return false;
}
}


// simple webserver so Render keeps the service alive and provides a health endpoint
app.get('/', (req, res) => {
res.send('Discord Music Bot is running');
});


app.listen(PORT, () => {
console.log(`Webserver running on port ${PORT}`);
});


client.login(process.env.DISCORD_TOKEN).catch(err => {
console.error('Failed to login:', err);
process.exit(1);
});
