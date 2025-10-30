import express from "express";
const app = express();
app.all("/", (req, res) => res.send("✅ Fufu bot is alive!"));
app.listen(3000, () => console.log("KeepAlive server started"));
export default function keep_alive() {}
