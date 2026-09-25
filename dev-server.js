"use strict";

// Local dev server — NOT used by Vercel (Vercel runs api/ directly). This mounts the
// exact same handler over Node's built-in http so you can test end-to-end locally
// without the Vercel CLI. Run it with:  npm run local
//
// It reads env from .env via `node --env-file=.env` (Node >= 20.6), so no dotenv needed.

const http = require("http");
const handler = require("./api/saves");

const PORT = process.env.PORT || 3000;

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url, `http://${req.headers.host || "localhost"}`);

  // Vercel gives handlers req.query; the raw http server doesn't, so populate it.
  req.query = Object.fromEntries(parsed.searchParams.entries());

  // Vercel's res has .status(); add a compatible shim over ServerResponse.
  res.status = (code) => {
    res.statusCode = code;
    return res;
  };

  if (parsed.pathname === "/api/saves") {
    try {
      await handler(req, res);
    } catch (err) {
      console.error(err);
      if (!res.headersSent) {
        res.statusCode = 500;
        res.end(JSON.stringify({ ok: false, msg: "server error" }));
      }
    }
    return;
  }

  res.statusCode = 404;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify({ ok: false, msg: "not found" }));
});

server.listen(PORT, () => {
  console.log(`Reel Saves dev server on http://localhost:${PORT}/api/saves`);
  console.log(`DB_NAME=${process.env.DB_NAME}  MONGODB_URI=${process.env.MONGODB_URI}`);
});
