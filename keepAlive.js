const express = require('express');
const app = express();

app.get('/', (req, res) => res.send('Bot is alive!'));

function keepAlive() {
  app.listen(3000, () => console.log('Server ready.'));
}

module.exports = keepAlive;
