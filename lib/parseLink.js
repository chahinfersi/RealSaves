"use strict";

// Parse a shared social link into: which platform it's from, the best content ID we
// can get right now, a stable dedup key, and whether that ID is already canonical.
//
// The iOS Shortcut lives in the share sheet of every app, so the same endpoint receives
// links from Instagram, TikTok and Facebook. They don't behave the same:
//
//   Instagram  → the real reel/post ID is IN the URL  → resolved immediately.
//   TikTok     → share sheet gives a short link (vt.tiktok.com/<code>/) whose real
//                video ID is hidden behind a redirect → provisional, worker resolves later.
//   Facebook   → share sheet gives a share link (facebook.com/share/r/<token>/) whose
//                real reel ID is hidden behind a redirect → provisional, worker resolves later.
//
// We never make a network call here (the POST must answer in well under a second). For
// short/share links we store a provisional ID (the short code) with resolved:false, and
// the later worker follows the redirect to fill in the real canonical ID/URL.

const IG_PATH = /^\/(reel|reels|p|tv)\/([^/?#]+)/;
const TT_VIDEO = /\/video\/(\d+)/;
const FB_CANONICAL = /^\/(?:reel|videos|watch)\/(\d+)/;

function hostOf(u) {
  return u.hostname.toLowerCase().replace(/^www\./, "");
}

function detectPlatform(host) {
  if (host === "instagram.com" || host.endsWith(".instagram.com")) return "instagram";
  if (host === "tiktok.com" || host.endsWith(".tiktok.com")) return "tiktok";
  if (
    host === "facebook.com" ||
    host.endsWith(".facebook.com") ||
    host === "fb.watch" ||
    host === "fb.me"
  ) {
    return "facebook";
  }
  return "unknown";
}

function segments(pathname) {
  return pathname.split("/").filter(Boolean);
}

// A save we couldn't extract an ID from — kept, never rejected. dedupKey is null so the
// unique-sparse index always lets it through (no dedup on unparsed saves).
function unparsed(rawUrl, platform) {
  return {
    platform,
    contentId: null,
    dedupKey: null,
    resolved: false,
    url: rawUrl,
    status: "unparsed",
  };
}

// A parsed save. dedupKey = `${platform}:${contentId}` and is what the unique index
// enforces. `resolved` is true when contentId/url are already canonical; false when
// contentId is a provisional short code the worker still needs to resolve.
function parsed(platform, contentId, url, resolved) {
  return {
    platform,
    contentId,
    dedupKey: `${platform}:${contentId}`,
    resolved,
    url,
    status: "pending",
  };
}

/**
 * @param {string} rawUrl
 * @returns {{platform:string, contentId:string|null, dedupKey:string|null,
 *            resolved:boolean, url:string, status:"pending"|"unparsed"}}
 */
function parseLink(rawUrl) {
  if (typeof rawUrl !== "string" || rawUrl.trim() === "") {
    return unparsed(rawUrl, "unknown");
  }
  const trimmed = rawUrl.trim();

  let u;
  try {
    u = new URL(trimmed);
  } catch (_err) {
    return unparsed(trimmed, "unknown");
  }

  const host = hostOf(u);
  const platform = detectPlatform(host);
  const path = u.pathname;

  if (platform === "instagram") {
    const m = IG_PATH.exec(path);
    if (!m) return unparsed(trimmed, "instagram");
    const type = m[1] === "reels" ? "reel" : m[1];
    const id = m[2];
    return parsed(platform, id, `https://www.instagram.com/${type}/${id}/`, true);
  }

  if (platform === "tiktok") {
    const shortHost = host === "vt.tiktok.com" || host === "vm.tiktok.com";
    const video = TT_VIDEO.exec(path);
    if (!shortHost && video) {
      // Canonical desktop link already carries the numeric video id.
      return parsed(platform, video[1], `${u.origin}${path}`, true);
    }
    // Short link (vt./vm.tiktok.com/<code>/) → provisional; worker resolves the redirect.
    // url is the query-stripped short link (redirect still works via the path code);
    // rawUrl keeps the original with any tracking params.
    const code = segments(path)[0];
    if (!code) return unparsed(trimmed, "tiktok");
    return parsed(platform, code, `${u.origin}${path}`, false);
  }

  if (platform === "facebook") {
    const canonical = FB_CANONICAL.exec(path);
    if (canonical) {
      return parsed(platform, canonical[1], `${u.origin}${path}`, true);
    }
    const watchV = u.searchParams.get("v");
    if (watchV) {
      return parsed(platform, watchV, `https://www.facebook.com/watch/?v=${watchV}`, true);
    }
    // share/r/<token>, share/v/<token>, or fb.watch/<code> → provisional short code.
    // url is query-stripped (drops mibextid etc.); rawUrl keeps the original.
    const parts = segments(path);
    const code = parts[0] === "share" && parts.length >= 3 ? parts[2] : parts[0];
    if (!code) return unparsed(trimmed, "facebook");
    return parsed(platform, code, `${u.origin}${path}`, false);
  }

  return unparsed(trimmed, "unknown");
}

module.exports = { parseLink };

// Quick self-test: `node lib/parseLink.js` (npm run test:parse).
if (require.main === module) {
  const cases = [
    // The three real share-sheet payloads captured via webhook.site:
    "https://vt.tiktok.com/ZSabc1234/",
    "https://www.instagram.com/reel/DAbc123xYz/?stkn=ZXhhbXBsZQ==",
    "https://www.facebook.com/share/r/1AbCdEfGhI/?mibextid=wwXIfr",
    // A few extra shapes:
    "https://www.instagram.com/p/CoolPost99/?utm_source=ig_web",
    "https://www.tiktok.com/@someone/video/7412345678901234567",
    "https://www.facebook.com/reel/1234567890",
    "https://www.youtube.com/shorts/abc123", // unknown platform → kept, unparsed
    "not a url",
    "",
  ];
  for (const c of cases) {
    console.log(JSON.stringify(c), "=>", JSON.stringify(parseLink(c)));
  }

  let fails = 0;
  const expect = (label, got, want) => {
    if (got !== want) { console.error(`FAIL ${label}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); fails++; }
  };

  const tt = parseLink("https://vt.tiktok.com/ZSabc1234/");
  expect("tiktok platform", tt.platform, "tiktok");
  expect("tiktok provisional id", tt.contentId, "ZSabc1234");
  expect("tiktok dedupKey", tt.dedupKey, "tiktok:ZSabc1234");
  expect("tiktok resolved", tt.resolved, false);
  expect("tiktok status", tt.status, "pending");

  const ig = parseLink("https://www.instagram.com/reel/DAbc123xYz/?stkn=ZXhhbXBsZQ==");
  expect("ig platform", ig.platform, "instagram");
  expect("ig id", ig.contentId, "DAbc123xYz");
  expect("ig clean url", ig.url, "https://www.instagram.com/reel/DAbc123xYz/");
  expect("ig resolved", ig.resolved, true);

  const fb = parseLink("https://www.facebook.com/share/r/1AbCdEfGhI/?mibextid=wwXIfr");
  expect("fb platform", fb.platform, "facebook");
  expect("fb token", fb.contentId, "1AbCdEfGhI");
  expect("fb dedupKey", fb.dedupKey, "facebook:1AbCdEfGhI");
  expect("fb resolved", fb.resolved, false);

  const yt = parseLink("https://www.youtube.com/shorts/abc123");
  expect("unknown platform", yt.platform, "unknown");
  expect("unknown status", yt.status, "unparsed");
  expect("unknown dedupKey", yt.dedupKey, null);

  console.log(fails === 0 ? "\nALL PASS" : `\n${fails} FAILURE(S)`);
  process.exit(fails === 0 ? 0 : 1);
}
