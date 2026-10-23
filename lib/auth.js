"use strict";

// Multi-tenant auth: the x-api-key header both authenticates (is this a valid key?)
// and identifies (whose data is this?). Each user gets their own key, mapped to an
// `owner` string. Every save is stamped with its owner and only ever read back by
// that same owner.
//
// Keys are configured via the API_KEYS env var as a comma-separated list of
// "owner:key" pairs, e.g.  API_KEYS="alice:6f1a...,bob:9c22..."
// (API keys are long random hex, so they never contain ":" or ",".)

let keyToOwner = null; // cached lookup, built once per process

function loadKeys() {
  if (keyToOwner) return keyToOwner;
  keyToOwner = new Map();
  const raw = process.env.API_KEYS || "";
  for (const pair of raw.split(",")) {
    const idx = pair.indexOf(":");
    if (idx === -1) continue;
    const owner = pair.slice(0, idx).trim();
    const key = pair.slice(idx + 1).trim();
    if (owner && key) keyToOwner.set(key, owner);
  }
  return keyToOwner;
}

// Return the owner this request's x-api-key belongs to, or null if the key is
// missing or unknown. A null return means 401.
function identifyOwner(req) {
  const provided = req.headers["x-api-key"];
  if (typeof provided !== "string" || provided.length === 0) return null;
  return loadKeys().get(provided) || null;
}

module.exports = { identifyOwner };
