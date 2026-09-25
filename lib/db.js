"use strict";

const { MongoClient } = require("mongodb");

const COLLECTION = "saves";

// Cache the client (and the in-flight connect promise) on the global object so it
// survives across warm serverless invocations. Opening a new connection per request
// would quickly exhaust the small connection pool on an Atlas M0 cluster.
let cached = global.__reelSavesMongo;
if (!cached) {
  cached = global.__reelSavesMongo = {
    client: null,
    promise: null,
    indexEnsured: false,
  };
}

function getUri() {
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error("MONGODB_URI is not set");
  return uri;
}

async function getClient() {
  if (cached.client) return cached.client;
  if (!cached.promise) {
    cached.promise = MongoClient.connect(getUri(), {
      // Keep the pool small — serverless functions are single-request; a big pool
      // multiplied across concurrent lambdas can blow past the M0 connection limit.
      maxPoolSize: 5,
    });
  }
  cached.client = await cached.promise;
  return cached.client;
}

// Ensure the multi-tenant unique index exists: unique on (owner, dedupKey) so each
// user dedups within their own reels — two people can each save the same reel.
//
// We use a PARTIAL index (only where dedupKey is a string), not a sparse one. Why:
// on a compound index, `sparse` only skips a document when ALL indexed fields are
// missing. Since `owner` is always present, sparse would still index the unparsed
// rows (dedupKey:null) and then two unparsed rows for one owner would collide on
// (owner, null). The partial filter indexes only rows that actually have a dedupKey,
// so any number of unparsed rows per owner are allowed.
//
// Runs at most once per process; createIndex is idempotent.
async function ensureIndexes(collection) {
  if (cached.indexEnsured) return;
  await collection.createIndex(
    { owner: 1, dedupKey: 1 },
    {
      unique: true,
      partialFilterExpression: { dedupKey: { $type: "string" } },
      name: "owner_dedupKey_unique",
    }
  );
  cached.indexEnsured = true;
}

async function getCollection() {
  const dbName = process.env.DB_NAME;
  if (!dbName) throw new Error("DB_NAME is not set");
  const client = await getClient();
  const collection = client.db(dbName).collection(COLLECTION);
  await ensureIndexes(collection);
  return collection;
}

module.exports = { getCollection };
