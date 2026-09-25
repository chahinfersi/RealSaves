"use strict";

const { getCollection } = require("../lib/db");
const { identifyOwner } = require("../lib/auth");
const { parseLink } = require("../lib/parseLink");

const SOURCE = "ios-shortcut";
const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;

// Small JSON responder that always sets a clear content type.
function send(res, status, payload) {
  res.status(status).setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

// Read and JSON-parse the request body defensively. Vercel usually pre-parses
// application/json into req.body, but we handle string / raw-stream / missing
// cases too so a malformed body returns null instead of throwing.
async function readJsonBody(req) {
  if (req.body && typeof req.body === "object") return req.body;

  let raw = req.body;
  if (typeof raw !== "string") {
    raw = await new Promise((resolve) => {
      let data = "";
      req.on("data", (chunk) => (data += chunk));
      req.on("end", () => resolve(data));
      req.on("error", () => resolve(""));
    });
  }
  if (!raw || raw.trim() === "") return null;
  try {
    return JSON.parse(raw);
  } catch (_err) {
    return null;
  }
}

// Handlers return { status, body, log } — the top-level handler does the actual
// sending and logging, so every outcome is logged in one consistent place.

async function handlePost(req, owner) {
  const body = await readJsonBody(req);
  if (!body || typeof body !== "object") {
    return { status: 400, body: { ok: false, msg: "invalid JSON body" }, log: "rejected: invalid JSON body" };
  }

  const { link, note } = body;
  if (typeof link !== "string" || link.trim() === "") {
    return { status: 400, body: { ok: false, msg: "link required" }, log: "rejected: missing/empty link" };
  }
  if (note !== undefined && typeof note !== "string") {
    return { status: 400, body: { ok: false, msg: "note must be a string" }, log: "rejected: note not a string" };
  }
  const cleanNote = typeof note === "string" && note.trim() !== "" ? note.trim() : null;

  const { platform, contentId, dedupKey, resolved, url, status } = parseLink(link);
  console.log(
    `[saves]   parsed owner=${owner} link=${link} -> platform=${platform} dedupKey=${dedupKey} resolved=${resolved} status=${status} note=${cleanNote ? "yes" : "no"}`
  );

  const doc = {
    owner, // which user this reel belongs to (from their API key)
    platform, // "instagram" | "tiktok" | "facebook" | "unknown"
    contentId, // canonical ID (instagram) or provisional short code (tiktok/facebook)
    dedupKey, // `${platform}:${contentId}` — unique per owner; null for unparsed saves
    resolved, // true when contentId/url are canonical; false = worker must follow redirect
    url, // cleaned URL; provisional (== rawUrl) for unresolved short links
    rawUrl: link,
    note: cleanNote,
    source: SOURCE,
    capturedAt: new Date(),
    status, // "pending" for parsed saves, "unparsed" otherwise
    read: false,
    transcript: null,
  };

  const collection = await getCollection();

  try {
    const result = await collection.insertOne(doc);
    return {
      status: 200,
      body: { ok: true, msg: "saved", platform },
      log: `SAVED owner=${owner} platform=${platform} dedupKey=${dedupKey} resolved=${resolved} _id=${result.insertedId}`,
    };
  } catch (err) {
    // Duplicate (owner, dedupKey) — this owner already saved this reel.
    if (err && err.code === 11000) {
      // Only fill in a note if one was sent and THIS OWNER's existing record has
      // none; never overwrite an existing note, never touch another owner's row.
      let noteFilled = false;
      if (cleanNote) {
        const upd = await collection.updateOne(
          { owner, dedupKey, note: { $in: [null, ""] } },
          { $set: { note: cleanNote } }
        );
        noteFilled = upd.modifiedCount > 0;
      }
      return {
        status: 200,
        body: { ok: true, msg: "already saved", duplicate: true, platform },
        log: `DUPLICATE owner=${owner} platform=${platform} dedupKey=${dedupKey} noteFilled=${noteFilled}`,
      };
    }
    throw err;
  }
}

async function handleGet(req, owner) {
  // Parse limit: default 20, clamp to [1, MAX_LIMIT].
  let limit = parseInt(req.query.limit, 10);
  if (!Number.isFinite(limit) || limit < 1) limit = DEFAULT_LIMIT;
  if (limit > MAX_LIMIT) limit = MAX_LIMIT;

  const unreadOnly =
    req.query.unreadOnly === "true" || req.query.unreadOnly === "1";
  // Always scope to this owner — a user only ever sees their own reels.
  const filter = { owner };
  if (unreadOnly) filter.read = false;

  const collection = await getCollection();
  const saves = await collection
    .find(filter)
    .sort({ capturedAt: -1 })
    .limit(limit)
    .toArray();

  return {
    status: 200,
    body: { ok: true, count: saves.length, saves },
    log: `LIST owner=${owner} returned=${saves.length} unreadOnly=${unreadOnly} limit=${limit}`,
  };
}

module.exports = async function handler(req, res) {
  const started = Date.now();
  const id = Math.random().toString(36).slice(2, 8); // short id to correlate the two log lines
  const method = req.method;

  // One consistent place to log the outcome and send the response.
  function finish(status, payload, summary) {
    const ms = Date.now() - started;
    const mark = status < 400 ? "✓" : "✗"; // ✓ / ✗
    console.log(`[saves] ${mark} #${id} ${method} ${status} ${summary} (${ms}ms)`);
    return send(res, status, payload);
  }

  console.log(`[saves] → #${id} ${method} received`); // →

  try {
    const owner = identifyOwner(req);
    if (!owner) {
      return finish(401, { ok: false, msg: "unauthorized" }, "unauthorized (bad/missing x-api-key)");
    }

    let result;
    if (method === "POST") result = await handlePost(req, owner);
    else if (method === "GET") result = await handleGet(req, owner);
    else {
      res.setHeader("Allow", "GET, POST");
      result = { status: 405, body: { ok: false, msg: "method not allowed" }, log: `method not allowed (${method})` };
    }
    return finish(result.status, result.body, result.log);
  } catch (err) {
    console.error(`[saves] ✗ #${id} ${method} server error:`, err);
    return finish(500, { ok: false, msg: "server error" }, "server error (see stack above)");
  }
};
