import express from "express";

export function makeWeb() {
  const app = express();
  app.get("/", (_req, res) => {
    res.status(200).send("Discord Music Bot is running.");
  });

  const port = process.env.PORT || 3000;
  app.listen(port, () => {
    console.log(`[Web] Listening on port ${port}`);
  });
}
