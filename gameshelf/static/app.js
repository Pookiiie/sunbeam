/* The app's UI — plain JavaScript, no build step. */

// The app's name comes from gameshelf/brand.py (the server puts it on the page): rename it there.
const APP = document.documentElement.dataset.app || "Sunbeam";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Platform icons from Simple Icons (pinned version). Simple Icons has no Xbox logo, so Xbox
// (and XGP, which uses the same icon) comes from Bootstrap Icons (MIT licence).
const ICON_CDN = "https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/";
const XBOX_ICON = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/icons/xbox.svg";
const PLATFORM_ICON = { steam: "steam", playstation: "playstation", switch2: "nintendoswitch", switch: "nintendoswitch",
  epic: "epicgames", battlenet: "battledotnet", ubisoft: "ubisoft", gog: "gogdotcom", xbox: XBOX_ICON };
const iconUrl = (icon) => icon.startsWith("https:") ? icon : `${ICON_CDN}${icon}.svg`;
const PLATFORM_NAME = { steam: "Steam", playstation: "PlayStation", switch2: "Nintendo Switch 2", switch: "Nintendo Switch",
  xbox: "Xbox", epic: "Epic Games Store", battlenet: "Battle.net", ubisoft: "Ubisoft Connect", gog: "GOG", other: "Other" };
function platformBadge(p) {
  const icon = PLATFORM_ICON[p];
  const name = PLATFORM_NAME[p] || p;
  if (!icon) return `<span class="badge" title="${esc(name)}">${esc(PLATFORM_SHORT[p] || p)}</span>`;
  return `<span class="badge icon" title="${esc(name)}" aria-label="${esc(name)}"><i style="--icon:url('${iconUrl(icon)}')"></i>${p === "switch2" ? "<b>2</b>" : ""}<span class="badge-text">${esc(PLATFORM_SHORT[p] || p)}</span></span>`;
}
// A platform with its icon and full name (the game page's header).
function platformTag(p) {
  const icon = PLATFORM_ICON[p];
  return `<span class="badge ptag">${icon ? `<i style="--icon:url('${iconUrl(icon)}')"></i>` : ""}${esc(state.platforms?.[p] || PLATFORM_NAME[p] || p)}</span>`;
}
// If the icons can't load (offline), fall back to text tags.
(() => { const probe = new Image(); probe.onerror = () => document.documentElement.classList.add("no-icons"); probe.src = ICON_CDN + "steam.svg"; })();

const PLATFORM_SHORT = { steam: "Steam", playstation: "PS", switch2: "Switch 2", switch: "Switch", xbox: "Xbox", epic: "Epic", battlenet: "Battle.net", ubisoft: "Ubisoft", gog: "GOG", other: "Other" };

const DEFAULT_PREFS = {
  sort: "last_played", dir: "desc", view: "grid", size: 170, types: null,
  hiddenPlatforms: [], status: ["", "completed", "dnf"], played: ["played", "never"], genres: [], interests: ["more", "less", "skip", ""],
  subTags: [], wishSort: "release", wishStore: "all", wishSale: false, installed: ["yes", "no"], shelves: [],
  length: [], reviews: null, players: [],
};

const state = {
  games: [],
  wishGames: [],  // games that are only on a wishlist (not in the library)
  platforms: {},
  q: "",
  prefs: loadPrefs(),
  settings: null,
  sync: null,
  openId: null,
};

function loadPrefs() {
  try {
    const p = { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem("gameshelf.prefs") || "{}") };
    if (p.genre) { p.genres = [p.genre]; }  // older single-genre filter
    delete p.genre;
    if (!Array.isArray(p.genres)) p.genres = [];
    if (!Array.isArray(p.subTags)) p.subTags = [];
    // On XGP / On PS+ are now part of the Xbox and PlayStation platforms.
    p.subTags = p.subTags.filter((t) => t === "new" || t === "leaving");
    // Played used to be one choice: all / played / unplayed.
    if (!Array.isArray(p.played)) p.played = p.played === "played" ? ["played"] : p.played === "unplayed" ? ["never"] : ["played", "never"];
    delete p.subs; delete p.catalog;
    // VR / early access / hidden games: the Type filter now (null = what Settings › Hidden game types allows).
    delete p.vr; delete p.showVR; delete p.showEA; delete p.onlyEA; delete p.showHidden;
    if (!Array.isArray(p.types)) p.types = null;
    if (!Array.isArray(p.interests)) p.interests = ["more", "less", "skip", ""];
    if (!Array.isArray(p.installed)) p.installed = ["yes", "no"];
    if (!Array.isArray(p.shelves)) p.shelves = [];
    if (!Array.isArray(p.length)) p.length = [];
    if (!Array.isArray(p.players)) p.players = [];
    if (typeof p.reviews !== "number") p.reviews = null;
  if (!["steam", "psn", "gog", "epic"].includes(p.wishStore)) p.wishStore = "all";
    return p;
  } catch { return { ...DEFAULT_PREFS }; }
}
function savePrefs() {
  try { localStorage.setItem("gameshelf.prefs", JSON.stringify(state.prefs)); } catch { /* storage unavailable */ }
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { "X-GameShelf": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
// undo: a function that takes the change back; the toast then shows an Undo button for a while.
function toast(msg, isErr = false, undo = null) {
  const t = $("#toast");
  t.textContent = msg;
  if (undo) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "toast-undo"; b.textContent = "Undo";
    b.onclick = () => { t.classList.remove("on", "has-undo"); undo(); };
    t.append(b);
  }
  t.classList.toggle("err", isErr);
  t.classList.toggle("has-undo", !!undo);
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on", "has-undo"), undo ? 6500 : isErr ? 5200 : 2600);
}

// Saves a change to a game, then shows it everywhere it appears.
async function patchGame(id, body) {
  const g = await api(`/api/games/${id}`, { method: "PATCH", body });
  upsertLocal(g);
  if (state.openId === id) renderDetail(derive(g));
  return g;
}
async function undoPatch(id, body, msg) {
  try { await patchGame(id, body); toast(msg); } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ formatting */

const hours = (min) => (min || 0) / 60;
function fmtHours(h, { empty = "—" } = {}) {
  if (h == null || isNaN(h) || h <= 0) return empty;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h < 10) return `${(Math.round(h * 10) / 10).toLocaleString()} h`;
  return `${Math.round(h).toLocaleString()} h`;
}
function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function relDate(ts) {
  if (!ts) return "Never";
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (days < 1) return "Today";
  if (days < 2) return "Yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  if (days < 365) return `${Math.round(days / 30)} months ago`;
  const y = Math.round(days / 365 * 10) / 10;
  return `${y} ${y === 1 ? "year" : "years"} ago`;
}
const year = (ts) => (ts ? new Date(ts * 1000).getUTCFullYear() : null);
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }

/* ------------------------------------------------------------ derived values */

// Time to beat, matched to how you play: Main + Extras. Once you've played well past
// that (25% more), stretch towards Completionist: halfway there, but never more than
// 50% above Main + Extras.
function blendTtb(g) {
  if (g.ttb_custom) return g.ttb_custom;  // set with the slider on the game's page
  const base = g.ttb_extra || g.ttb_main || null;
  const full = g.ttb_complete;
  const played = (g.playtime_min || 0) / 60;
  if (!base) return full || null;
  if (full && full > base && played > base * 1.25) return base + Math.min(0.5 * (full - base), 0.5 * base);
  return base;
}

// Games without a real ending (each game can override this from its page).
// IGDB's tags are loose ("Sandbox" is on Skyrim and GTA; "Sport" on story games),
// so a tag only counts when the genres around it agree.
function autoEndless(g) {
  if (g.endless_auto !== undefined) return Boolean(g.endless_auto);  // the server's rules (gameshelf/rules.py)
  const modes = g.modes || [], genres = g.genres || [], themes = g.themes || [];
  const has = (list, re) => list.some((x) => re.test(x));
  const story = has(genres, /role-playing|adventure/i);
  // Always-online and battle royale games, MOBAs, 4X strategy.
  if (has(modes, /massively multiplayer|battle royale/i)) return true;
  if (has(genres, /^moba$/i) || has(themes, /^4x/i)) return true;
  // Sports and fighting games, unless they're also adventures or RPGs (Cairn, Yakuza).
  if (has(genres, /^sport$/i) && !story && !has(genres, /platform/i)) return true;
  if (has(genres, /^fighting$/i) && !story) return true;
  const narrative = has(themes, /comedy|drama|mystery|horror|thriller|romance/i);
  if (has(themes, /sandbox/i) && has(genres, /simulator/i)) {
    // Builders and toys (Factorio, Cities: Skylines, Garry's Mod), but not ones with a story to tell.
    if (!narrative && !story && !has(genres, /shooter|platform|puzzle/i)) return true;
    // Grand strategy (Crusader Kings III).
    if (!narrative && has(genres, /^strategy$/i) && !has(genres, /adventure|puzzle/i)) return true;
    // Open-world survival sandboxes (Minecraft, Terraria, No Man's Sky).
    if (has(themes, /survival/i) && has(themes, /open world/i)) return true;
    // Life sims (The Sims, Stardew Valley).
    if (has(themes, /romance/i)) return true;
  }
  // Management and driving simulators (Euro Truck Simulator, Football Manager), not puzzle-level games.
  if (has(genres, /simulator/i) && has(themes, /business|non-fiction/i) && !story && !has(genres, /puzzle/i)) return true;
  // Party games played in rounds with friends: trivia and drawing collections (Jackbox, Drawful),
  // and board and card games (Mario Party, Monopoly, Uno).
  if (/jackbox/i.test(g.title || "")) return true;
  if (has(themes, /^party$/i) && has(modes, /multiplayer/i) && has(genres, /quiz|card & board/i) && !has(genres, /role-playing|adventure|platform/i)) return true;
  // Time-to-beat numbers no story could produce (players logging endless play).
  if ((g.ttb_main && g.ttb_main > 400) || (g.ttb_extra && g.ttb_extra > 600)) return true;
  // Multiplayer-only shooters, fighters, racers and sports (Counter-Strike, ARC Raiders, The Finals).
  const competitive = has(genres, /shooter|tactical|fighting|moba|racing|sport/i);
  if (modes.length && !has(modes, /single player/i) && competitive) return true;
  // Live-service multiplayer: a "100%" time many times the main one (Overwatch, Destiny 2, Deep Rock).
  if (g.ttb_main && g.ttb_complete >= 8 * g.ttb_main && has(modes, /multiplayer/i) && competitive
      && !has(themes, /stealth/i)) return true;
  const ttb = blendTtb(g), played = (g.playtime_min || 0) / 60;
  // Played far beyond the story's length: the multiplayer is clearly the main event.
  if (has(modes, /multiplayer/i) && ttb && played >= 20 && played > ttb * 3) return true;
  // Otherwise a known time to beat means there's an ending.
  if (ttb) return false;
  // Multiplayer-only with no time to beat at all.
  return modes.length > 0 && !has(modes, /single player/i) && has(modes, /multiplayer|co-op/i);
}

// Not really games: playtests, betas, demos, test servers, tools.
// Playtests, betas, demos, servers, tools and add-on passes: in the library but hidden unless you say otherwise.
const NOT_A_GAME = /\b(play ?test|(open|closed|public|online|early access) beta|beta( version| test)?$|public test|test server|demo|dedicated server|sdk|soundtrack|benchmark|expansion pass|season pass|redkit|mod ?(kit|tools)|modding tools|level editor|art ?book)\b|\((open |closed |public |online )?beta\)/i;
const autoHidden = (g) => NOT_A_GAME.test(g.title) || (g.entries || []).some((e) => NOT_A_GAME.test(e.name || ""));

// VR games: IGDB's "Virtual Reality" perspective on a game that only exists on PC and
// VR headsets. Flat games with a VR mode (No Man's Sky) are on consoles too, so they stay.
// VR headsets as IGDB names them (Apple's is "visionOS"; PlayStation's "PSVR"/"PSVR2" match "vr").
const VR_PLATFORM = /vr\d?\b|virtual reality|oculus|meta quest|rift|vive|mixed reality|daydream|visionos|vision pro|pico|valve index|gear vr/i;
function isVR(g) {
  // Steam's own "VR Only" category when the game is on Steam (read with the early access check),
  // else a title with "VR", or IGDB's VR perspective on a game that only exists on headsets (and PC).
  if (g.vr_only === 1 || g.vr_only === 0) return g.vr_only === 1;
  if (/\bVR\b/.test(g.title)) return true;
  if (!(g.perspectives || []).some((x) => /virtual reality/i.test(x))) return false;
  const plats = g.igdb_platforms || [];
  return plats.some((x) => VR_PLATFORM.test(x)) && plats.every((x) => VR_PLATFORM.test(x) || /^pc\b|windows|linux|mac/i.test(x));
}

// XGP / PS+: what's included, new (first seen in the last 30 days) or leaving.
const SUB_NAME = { gamepass: "XGP (Xbox PC Game Pass)", psplus: "PS+ (PlayStation Plus Extra)" };
const SUB_SHORT = { gamepass: "XGP", psplus: "PS+" };
const SUB_PLATFORM = { gamepass: "xbox", psplus: "playstation" };
// Subscribed unless you said otherwise (Settings › Subscriptions; asked during setup).
const subscribed = (sv) => state.settings?.settings?.[`sub_${sv}`] !== false;
const subsLabel = () => ["gamepass", "psplus"].filter(subscribed).map((x) => SUB_SHORT[x]).join(" / ");  // "XGP / PS+", "" with neither
function subPill(g) {
  const subs = g.subs || [];
  const leaving = subs.find((x) => x.leaving), fresh = subs.find((x) => x.new);
  if (leaving) return `<span class="sub-pill leaving" title="Leaving ${SUB_NAME[leaving.s]} soon">Leaving ${SUB_SHORT[leaving.s]}</span>`;
  if (fresh) return `<span class="sub-pill new" title="Recently added to ${SUB_NAME[fresh.s]}">New on ${SUB_SHORT[fresh.s]}</span>`;
  return "";
}
function subMatch(g, tags) {
  const subs = g.subs || [];
  return tags.some((t) => t === "leaving" ? subs.some((x) => x.leaving) : t === "new" ? subs.some((x) => x.new) : false);
}

// Two different games with the same name (Dead Space 2008 and its 2023 remake) get their
// release year added, so they can be told apart everywhere.
state.sameNames = new Set();
function derive(g) {
  // Xbox's "(Game Preview)" is early access: the cover's tag says it, so the name doesn't need to.
  if (g.base_title === undefined) g.base_title = g.title.replace(/\s*\(Game Preview\)\s*$/i, "");
  // XGP / PS+: only the services you're subscribed to count (Settings › Subscriptions).
  if (g.subsAll === undefined) g.subsAll = g.subs || [];
  g.subs = g.subsAll.filter((x) => subscribed(x.s));
  g.title = state.sameNames.has(g.base_title.toLowerCase()) && year(g.release_date) ? `${g.base_title} (${year(g.release_date)})` : g.base_title;
  g.played_h = hours(g.playtime_min);
  g.ttb = blendTtb(g);
  g.critic = g.oc_score != null ? g.oc_score : null;
  g.hiddenAuto = autoHidden(g);
  g.vr = isVR(g);
  g.installed = g.installed || [];
  g.canPlay = g.installed.some((x) => x.can_play);
  g.shelves = g.shelves || [];
  g.isHidden = g.hidden === 1 ? true : g.hidden === 0 ? false : g.hiddenAuto;
  g.endlessAuto = autoEndless(g);
  g.isEndless = g.endless === 1 ? true : g.endless === 0 ? false : g.endlessAuto;
  g.progress = g.status === "completed" ? 1 : g.isEndless ? null : g.ttb ? Math.min(g.played_h / g.ttb, 1) : null;
  g.remaining = g.status || g.isEndless ? null : g.ttb ? Math.max(g.ttb - g.played_h, 0) : null;
  g.isCatalog = !!g.catalog_only;  // not yours: you can play it through XGP or PS+
  g.isWishOnly = !!g.wishlist_only;  // only on a wishlist: shown on the Wishlist page, not in the library
  // Only playable through a service you're not subscribed to: out of the library (still on the Wishlist page if it's there).
  g.unsubOnly = g.isCatalog && !g.subs.length;
  if (g.unsubOnly && g.wish) g.isWishOnly = true;
  // Early access: still being made (Steam, else IGDB; on a Steam wishlist, Steam's own flag).
  g.isEA = g.early_access === 1 || !!g.wish?.early_access;
  g.eaFull = g.isEA ? (g.wish?.ea_full_ts || g.ea_full_date || null) : null;
  g.rel = g.wish ? wishRelease(g) : null;
  // XGP counts as Xbox and PS+ as PlayStation: same icon, same platform filter.
  // A wishlist-only game shows the stores whose wishlist it's on.
  const plats = new Set(g.isWishOnly ? (g.wish.stores || []).map((x) => WISH_PLATFORM[x.store]) : g.platforms);
  for (const x of g.subs || []) plats.add(SUB_PLATFORM[x.s]);
  g.platforms = [...plats].filter(Boolean).sort();
  // Never played: no time and no launch date anywhere. Xbox knows when you last played
  // many older games but not for how long: those are played, with hours unknown.
  g.neverPlayed = !(g.playtime_min > 0) && !g.last_played;
  g.hoursUnknown = !(g.playtime_min > 0) && !!g.last_played;
  g.covers = [g.cover_url, g.igdb_cover, g.psn_image].filter(Boolean);
  g.heroes = [g.hero_url, g.igdb_artwork, ...(g.screenshots || []).map(bigShot)].filter(Boolean);
  g._search = `${g.title} ${(g.developers || []).join(" ")} ${(g.franchises || []).join(" ")}`.toLowerCase();
  return g;
}

/* ------------------------------------------------------------ images with fallbacks */

function img(urls, alt, cls = "") {
  const list = urls.filter(Boolean);
  if (!list.length) return "";
  return `<img class="${cls}" src="${esc(list[0])}" data-fallbacks="${esc(list.slice(1).join("|"))}" alt="${esc(alt)}" loading="lazy" decoding="async">`;
}
function coverHTML(g) {
  return `${img(g.covers, "")}<div class="fallback" style="--h:${hue(g.title)}" ${g.covers.length ? "hidden" : ""}>${esc(g.title)}</div>`;
}
// Re-render a container without the covers blinking: images that were already loaded are
// moved into the new markup instead of being fetched and decoded again.
function setHTML(container, html) {
  const old = new Map();
  for (const im of container.querySelectorAll("img")) {
    if (!im.complete || !im.naturalWidth) continue;
    const box = im.closest(".cover, .thumb, .hero-cover");
    const list = old.get(im.getAttribute("src")) || [];
    list.push({ im, wide: box && box.classList.contains("wide-art"), art: box && box.style.getPropertyValue("--art") });
    old.set(im.getAttribute("src"), list);
  }
  container.innerHTML = html;
  if (!old.size) return;
  for (const im of container.querySelectorAll("img")) {
    const prev = (old.get(im.getAttribute("src")) || []).shift();
    if (!prev) continue;
    prev.im.className = im.className;
    im.replaceWith(prev.im);
    const box = prev.im.closest(".cover, .thumb, .hero-cover");
    if (box && prev.wide) { box.classList.add("wide-art"); if (prev.art) box.style.setProperty("--art", prev.art); }
    const fb = box && box.querySelector(":scope > .fallback");
    if (fb) fb.hidden = true;
  }
}

document.addEventListener("load", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLImageElement)) return;
  const box = el.closest(".cover, .thumb, .hero-cover");
  if (!box || !el.naturalWidth) return;
  const wide = el.naturalWidth / el.naturalHeight > 0.78;  // 2:3 is 0.67
  box.classList.toggle("wide-art", wide);
  if (wide) box.style.setProperty("--art", `url("${el.currentSrc.replace(/"/g, "%22")}")`);
}, true);

document.addEventListener("error", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLImageElement) || !el.dataset) return;
  const rest = (el.dataset.fallbacks || "").split("|").filter(Boolean);
  if (rest.length) {
    el.dataset.fallbacks = rest.slice(1).join("|");
    el.src = rest[0];
  } else {
    const fb = el.parentElement && el.parentElement.querySelector(":scope > .fallback");
    if (fb) fb.hidden = false;
    el.remove();
  }
}, true);

/* ------------------------------------------------------------ filter + sort */

// The Library's filters as one test, used by the Library itself and by smart shelves (whose
// rules are a saved copy of these filters). `f` may lack newer filters: they count as "any".
function filterTest(f, q = "", { manualShelves = false } = {}) {
  const d = FILTER_DEFAULTS();
  f = { ...d, ...f };
  q = (q || "").trim().toLowerCase();
  const hidden = new Set(f.hiddenPlatforms);
  const statuses = new Set(f.status);
  const types = new Set(f.types || defaultTypes());
  const installedOn = anyInstalled();
  return (g) => {
    // Type: regular / early access / VR / hidden (by default what Settings › Hidden game types allows).
    if (!types.has(gameType(g))) return false;
    if (!f.interests.includes(g.interest || "")) return false;  // Curator filter
    if (q && !g._search.includes(q)) return false;
    if (hidden.size && !g.platforms.some((x) => !hidden.has(x))) return false;
    if (!statuses.has(g.status || "")) return false;
    if (!f.played.includes(g.neverPlayed ? "never" : "played")) return false;
    if (f.genres.length && !(g.genres || []).some((x) => f.genres.includes(x))) return false;
    if (f.subTags.length && subsLabel() && !subMatch(g, f.subTags)) return false;  // (no menu without XGP or PS+)
    if (installedOn && !f.installed.includes(g.canPlay ? "yes" : "no")) return false;
    if (f.shelves.length && !(manualShelves ? g.shelves : onShelves(g)).some((s) => f.shelves.includes(s))) return false;
    if (f.length.length && !f.length.includes(lengthKey(g))) return false;
    if (f.reviews != null && !(reviewOf(g) >= f.reviews)) return false;
    if (f.players.length && !playerKeys(g).some((k) => f.players.includes(k))) return false;
    return true;
  };
}

function visibleGames() {
  const p = state.prefs;
  const list = state.games.filter(filterTest(p, state.q));
  const key = p.sort;
  const val = {
    // Dated games by date; then games with hours but no date (Steam before 2009, Epic);
    // never-played games last. Hours-only games sort among themselves by hours.
    last_played: (g) => g.last_played ? g.last_played : g.playtime_min > 0 ? g.playtime_min / 1e7 : null,
    playtime: (g) => g.playtime_min,
    title: (g) => g.title.toLowerCase(),
    release_date: (g) => g.release_date,
    ttb_main: (g) => g.ttb,
    remaining: (g) => g.remaining,
    progress: (g) => g.progress,
    critic_score: (g) => g.critic,
    steam_review_pct: (g) => g.steam_review_pct,
    added_at: (g) => g.added_at,
    // Curator's score; games you've started get a small lead.
    recommended: (g) => { const sc = recScore(g); return sc == null ? null : sc + (g.neverPlayed ? 0 : 5); },
    // Curator mark: Keen, Default, Meh, Skip (then by title)
    interest: (g) => ({ more: 3, "": 2, less: 1, skip: 0 })[g.interest || ""],
  }[key] || ((g) => g.title);
  const dir = p.dir === "asc" ? 1 : -1;
  list.sort((a, b) => {
    const va = val(a), vb = val(b);
    const na = va == null || va === "", nb = vb == null || vb === "";
    if (na && nb) return a.title.localeCompare(b.title);
    if (na) return 1;   // missing values always sink to the bottom
    if (nb) return -1;
    if (typeof va === "string") return dir * va.localeCompare(vb);
    return dir * (va - vb) || a.title.localeCompare(b.title);
  });
  return list;
}

/* ------------------------------------------------------------ Curator (recommendations) */

// A friendly pick, not a to-do list. Score out of 100:
//   reviews 70% — Steam's rating leads (70/30 with OpenCritic), leaning on OpenCritic
//                 when Steam has few reviews; no score at all counts as middling (60);
//   time 30%    — fewer hours to the credits is more inviting (5 h or less is best);
//   your say    — "Keen" +10, "Meh" −10, "Skip" leaves it out.
// Completed, will-not-finish, hidden and open-ended games aren't suggested.
const INTEREST = { more: "Keen", less: "Meh", skip: "Skip" };
// Thumbs up / down and a skip mark, drawn in the same line style as the other icons
// (svgIcon is defined further down; these are only used once the page runs).
const THUMB = '<path d="M7 10.5V20H4.5a1 1 0 0 1-1-1v-7.5a1 1 0 0 1 1-1zM7 10.5l3.6-6.6a1.9 1.9 0 0 1 3.5 1.3L13.4 9h5.1a2 2 0 0 1 2 2.4l-1.4 6.9A2.2 2.2 0 0 1 17 20H7"/>';
const INTEREST_ICON = {
  more: (c = "ico") => `<svg class="${c}" viewBox="0 0 24 24" aria-hidden="true">${THUMB}</svg>`,
  less: (c = "ico") => `<svg class="${c}" viewBox="0 0 24 24" aria-hidden="true"><g transform="matrix(1 0 0 -1 0 24)">${THUMB}</g></svg>`,
  skip: (c = "ico") => `<svg class="${c}" viewBox="0 0 24 24" aria-hidden="true"><path d="M5.5 5.5v13l9-6.5zM18.5 5.5v13"/></svg>`,
};
const INTEREST_NUDGE = { more: 10, less: -10 };

// Not played for over a year: you'd start over, so the whole game counts, not what's left.
const recRestart = (g) => !g.neverPlayed && !!g.last_played && Date.now() / 1000 - g.last_played > 365 * 86400;
function recHoursLeft(g) {
  if (!g.ttb) return null;
  if (g.neverPlayed) return g.ttb;
  if (recRestart(g)) return g.ttb_custom || g.ttb_extra || g.ttb_main || g.ttb;
  // Played past its usual length: likely seen the credits or replaying, so no time bonus.
  return g.remaining > 0.25 ? g.remaining : null;
}

const recEligible = (g) => !(g.isHidden || (g.vr && hideVR()) || g.status || g.isEndless || g.interest === "skip" || (g.isEA && hideEA()));

// Reviews out of 100: Steam leads; few Steam reviews lean on OpenCritic, or towards a middling 65.
function recReview(g) {
  const steam = g.steam_review_pct, oc = g.oc_score;
  if (steam != null) {
    const trust = Math.min(1, (g.steam_review_count || 0) / 50);
    return oc != null ? 0.7 * trust * steam + (1 - 0.7 * trust) * oc : trust * steam + (1 - trust) * 65;
  }
  return oc != null ? oc : null;
}

function recScore(g) {
  if (!recEligible(g)) return null;
  const review = recReview(g);
  const left = recHoursLeft(g);
  if (review == null && left == null) return null;  // nothing to go on
  const time = left == null ? 50 : Math.max(10, Math.min(100, 100 - 21 * Math.log2(Math.max(left, 0.5) / 5)));
  return 0.7 * (review ?? 60) + 0.3 * time + (INTEREST_NUDGE[g.interest] || 0);
}

function recReason(g) {
  const bits = [];
  const desc = g.steam_review_desc;
  if (g.steam_review_pct != null && desc) bits.push(`${desc} on Steam`);
  else if (g.oc_tier && g.oc_score != null) bits.push(`${g.oc_tier} on OpenCritic`);
  else if (g.steam_review_pct != null) bits.push(`${Math.round(g.steam_review_pct)}% on Steam`);
  else if (g.oc_score != null) bits.push(`${Math.round(g.oc_score)}% on OpenCritic`);
  const left = recHoursLeft(g);
  if (left != null) bits.push(g.neverPlayed ? `about ${fmtHours(left)} to beat`
    : recRestart(g) ? `about ${fmtHours(left)} from the start` : `about ${fmtHours(left)} to the credits`);
  if (g.interest === "more") bits.push("you're keen");
  const t = bits.join(" · ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// Four lists. Quick wins: the score above, under COMMIT_HOURS to go (games untouched for over a
// year count their full length, as a restart). Worthwhile commitments: COMMIT_HOURS or more,
// ranked on reviews and your interest only, and only great ones (COMMIT_MIN). Each game lands
// in one list at most.
const COMMIT_HOURS = 20, COMMIT_MIN = 85;
function recLists(n = 5) {
  const quick = [], commit = [];
  for (const g of state.games) {
    if (!recEligible(g)) continue;
    const left = recHoursLeft(g);
    if (left != null && left >= COMMIT_HOURS) {
      const r = recReview(g);
      if (r != null && r >= COMMIT_MIN) commit.push([r + (INTEREST_NUDGE[g.interest] || 0), g]);
    } else {
      const sc = recScore(g);
      if (sc != null) quick.push([sc, g]);
    }
  }
  const order = (a, b) => b[0] - a[0] || a[1].title.localeCompare(b[1].title);
  quick.sort(order); commit.sort(order);
  // Started = real hours on record (Xbox's "launched once, hours unknown" doesn't count).
  const started = (g) => g.playtime_min > 0;
  const pick = (list, test) => list.filter(([, g]) => test(g)).slice(0, n).map(([, g]) => g);
  return {
    keepQuick: pick(quick, started),
    keepCommit: pick(commit, started),
    freshQuick: pick(quick, (g) => g.neverPlayed),
    freshCommit: pick(commit, (g) => g.neverPlayed),
  };
}

// The recommendations page. Its name lives here only: change CURATOR to rename it everywhere.
const CURATOR = "Curator";

function renderUpNext() {
  const el = $("#upnext");
  const L = recLists();
  const card = (g, i) => `
    <button class="rec" data-id="${g.id}" data-why="picks">
      <div class="cover">${coverHTML(g)}<span class="rec-n">${i + 1}</span>${interestMark(g)}${instMark(g)}</div>
      <div class="rec-title">${esc(g.title)}</div>
      <div class="rec-why" title="Why this pick?">${recLines(g)}</div>
    </button>`;
  const row = (title, hint, list, empty) => `
    <div class="cur-sub">
      <h3 class="cur-sh">${title}<span>${hint}</span></h3>
      ${list.length ? `<div class="cur-row">${list.map(card).join("")}</div>` : `<p class="source-note">${empty}</p>`}
    </div>`;
  setHTML(el, `
    <section class="cur-section">
      <div class="cur-title"><h2 class="cur-h">Keep playing</h2></div>
      ${row("Quick wins", "a few evenings from the credits", L.keepQuick, "Started games close to the credits show up here.")}
      ${row("Worthwhile commitments", "long, and worth every hour", L.keepCommit, "Long, great games you've started show up here.")}
    </section>
    <section class="cur-section">
      <div class="cur-title"><h2 class="cur-h">Try something new</h2></div>
      ${row("Quick wins", "short and well loved", L.freshQuick, "Short games you haven't played show up here.")}
      ${row("Worthwhile commitments", "big, and among the best", L.freshCommit, "Long, great games you haven't played show up here.")}
    </section>`);
  renderWishRows();
  renderCuratorExtras();
  renderRecent();
  renderShelfRows();
  applyCuratorLayout();
}

/* ------------------------------------------------------------ Curator: Your shelves, and arranging the sections */

// The sections, in their default order. "Customize" (top right of Curator) reorders them and
// switches them off; saved in settings (curator_layout).
const CUR_SECTIONS = [
  ["picks", "Picks", "Keep playing and Try something new"],
  ["season", "In season", "A theme for each month, like horror in October"],
  ["wishlist", "From your wishlist", "Coming out soon, Recently released, On sale"],
  ["gems", "Hidden gems", "Loved by the few who've played them"],
  ["classics", "Classics worth it", "Ten years old or more and still great"],
  ["recent", "Recently played", "Your latest games"],
  ["shelves", "Your shelves", "The lists you make"],
];
function curatorLayout() {
  const saved = state.settings?.settings?.curator_layout || {};
  const keys = CUR_SECTIONS.map(([k]) => k);
  const order = [...(saved.order || []).filter((k) => keys.includes(k)), ...keys.filter((k) => !(saved.order || []).includes(k))];
  return { order, hidden: (saved.hidden || []).filter((k) => keys.includes(k)) };
}
function applyCuratorLayout() {
  const { order, hidden } = curatorLayout();
  for (const el of $$("#page-curator [data-cur]")) {
    el.style.order = String(order.indexOf(el.dataset.cur) + 2);
    el.hidden = hidden.includes(el.dataset.cur) || !!state.curEdit;
  }
  $("#cur-edit").hidden = !state.curEdit;
  $("#cur-tools").hidden = !!state.curEdit;
}
async function saveCuratorLayout(layout) {
  state.settings.settings.curator_layout = layout;  // at once, so the list redraws without waiting
  renderCuratorEdit(); applyCuratorLayout();
  try { await api("/api/settings", { method: "POST", body: { curator_layout: layout } }); } catch (e) { toast(e.message, true); }
}
function renderCuratorEdit() {
  const { order, hidden } = curatorLayout();
  const info = Object.fromEntries(CUR_SECTIONS.map(([k, n, d]) => [k, [n, d]]));
  const n = (state.shelves || []).length;
  $("#cur-edit").innerHTML = `<div class="cur-edit-head"><h2 class="cur-h">Arrange ${esc(CURATOR)}</h2>
      <span>Drag or use the arrows; switch off what you don't need.</span><span class="spacer"></span>
      <button type="button" class="btn" data-cedit="done">Done</button></div>
    ${order.map((k, i) => `<div class="cur-bar ${hidden.includes(k) ? "off" : ""}" draggable="true" data-ckey="${k}">
      <span class="grip" aria-hidden="true">⋮⋮</span>
      <div><b>${esc(info[k][0])}</b><span>${k === "shelves" ? (n ? `${n} shel${n === 1 ? "f" : "ves"}` : "No shelves yet: add games to a shelf from their page") : esc(info[k][1])}</span></div>
      <span class="spacer"></span>
      <button type="button" class="btn icon ghost" data-cedit="up" ${i ? "" : "disabled"} aria-label="Move ${esc(info[k][0])} up">↑</button>
      <button type="button" class="btn icon ghost" data-cedit="down" ${i < order.length - 1 ? "" : "disabled"} aria-label="Move ${esc(info[k][0])} down">↓</button>
      <label class="cur-switch"><input type="checkbox" data-cedit="show" ${hidden.includes(k) ? "" : "checked"}><i></i><span>${hidden.includes(k) ? "Hidden" : "Shown"}</span></label>
    </div>`).join("")}`;
}
$("#cur-customize").addEventListener("click", () => { state.curEdit = true; renderCuratorEdit(); applyCuratorLayout(); scrollTo({ top: 0 }); });
$("#cur-edit").addEventListener("click", (e) => {
  const b = e.target.closest("[data-cedit]");
  if (!b || b.dataset.cedit === "show") return;
  if (b.dataset.cedit === "done") { state.curEdit = false; applyCuratorLayout(); renderUpNext(); return; }
  const { order, hidden } = curatorLayout(), k = b.closest("[data-ckey]").dataset.ckey, i = order.indexOf(k);
  const j = b.dataset.cedit === "up" ? i - 1 : i + 1;
  if (j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  saveCuratorLayout({ order, hidden });
});
$("#cur-edit").addEventListener("change", (e) => {
  if (e.target.dataset.cedit !== "show") return;
  const { order, hidden } = curatorLayout(), k = e.target.closest("[data-ckey]").dataset.ckey;
  saveCuratorLayout({ order, hidden: e.target.checked ? hidden.filter((x) => x !== k) : [...hidden, k] });
});
// Drag a bar onto another to put it there.
$("#cur-edit").addEventListener("dragstart", (e) => {
  const bar = e.target.closest("[data-ckey]");
  if (!bar) return;
  state.curDrag = bar.dataset.ckey; bar.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", bar.dataset.ckey);
});
$("#cur-edit").addEventListener("dragover", (e) => {
  const bar = e.target.closest("[data-ckey]");
  if (!bar || !state.curDrag) return;
  e.preventDefault();
  $$("#cur-edit .cur-bar").forEach((x) => x.classList.toggle("drop-here", x === bar && x.dataset.ckey !== state.curDrag));
});
$("#cur-edit").addEventListener("dragend", () => { state.curDrag = null; $$("#cur-edit .cur-bar").forEach((x) => x.classList.remove("dragging", "drop-here")); });
$("#cur-edit").addEventListener("drop", (e) => {
  const bar = e.target.closest("[data-ckey]");
  if (!bar || !state.curDrag) return;
  e.preventDefault();
  const { order, hidden } = curatorLayout(), from = state.curDrag;
  state.curDrag = null;
  const rest = order.filter((k) => k !== from);
  rest.splice(rest.indexOf(bar.dataset.ckey) + (order.indexOf(from) < order.indexOf(bar.dataset.ckey) ? 1 : 0), 0, from);
  saveCuratorLayout({ order: rest, hidden });
});

// Your shelves: a card per shelf with a mosaic of its covers (default), or a row per shelf
// (Settings › Display). A card or "See all" opens the Library on that shelf.
function renderShelfRows() {
  const el = $("#shelfrows");
  const shelves = (state.shelves || []).map((s) => ({ ...s, games: state.games.filter((g) => onShelves(g).includes(s.id) && !g.isHidden) }))
    .filter((s) => s.games.length);
  if (!shelves.length) { setHTML(el, ""); return; }
  const rows = (state.settings?.settings?.curator_shelves || "cards") === "rows";
  const body = rows
    ? shelves.map((s) => `<div class="shelf-line">
        <div class="shelf-line-name"><b>${shelfLabel(s.id)}</b><span>${s.games.length} game${s.games.length === 1 ? "" : "s"}</span><button type="button" class="link" data-shelf-open="${s.id}">See all ›</button></div>
        <div class="shelf-strip" data-count="${s.games.length}">${s.games.map((g) => `<button class="shelf-cover" data-id="${g.id}" title="${esc(g.title)}"><div class="cover">${coverHTML(g)}${instMark(g)}</div></button>`).join("")}</div>
      </div>`).join("")
    : `<div class="shelf-cards">${shelves.map((s) => `<button type="button" class="shelf-card" data-shelf-open="${s.id}" title="Open ${esc(s.name)} in the Library">
        <div class="shelf-mosaic">${s.games.slice(0, 8).map((g) => `<div class="cover">${coverHTML(g)}</div>`).join("")}</div>
        <b>${shelfLabel(s.id)}</b><span>${s.games.length} game${s.games.length === 1 ? "" : "s"}</span></button>`).join("")}</div>`;
  setHTML(el, `<section class="cur-section"><div class="cur-title"><h2 class="cur-h">Your shelves</h2></div>${body}</section>`);
  if (rows) requestAnimationFrame(fitShelfStrips);
}
// A row shows the covers that fit; when some don't, the last place says "+N".
function fitShelfStrips() {
  for (const strip of $$("#shelfrows .shelf-strip")) {
    strip.querySelector(".shelf-more")?.remove();
    const items = [...strip.querySelectorAll(".shelf-cover")];
    items.forEach((x) => { x.hidden = false; });
    const w = items[0]?.getBoundingClientRect().width || 84, gap = 10;
    const fit = Math.max(1, Math.floor((strip.clientWidth + gap) / (w + gap)));
    if (items.length <= fit) continue;
    items.forEach((x, i) => { x.hidden = i >= fit - 1; });
    const id = strip.closest(".shelf-line").querySelector("[data-shelf-open]").dataset.shelfOpen;
    strip.insertAdjacentHTML("beforeend", `<button type="button" class="shelf-more" data-shelf-open="${id}">+${items.length - fit + 1}</button>`);
  }
}
addEventListener("resize", () => { if (state.page === "curator") fitShelfStrips(); });
function openShelf(id) {
  state.prefs.shelves = [id];
  savePrefs();
  showPage("library");
  renderFilters(); render();
}
$("#shelfrows").addEventListener("click", (e) => {
  const sh = e.target.closest("[data-shelf-open]");
  if (sh) { e.stopPropagation(); openShelf(Number(sh.dataset.shelfOpen)); }
}, true);

// The last games you played, as library covers, under the picks.
function renderRecent() {
  // Follows Settings › Hidden game types, like the rest of Curator.
  const types = defaultTypes();
  const list = state.games.filter((g) => types.includes(gameType(g)) && !g.isCatalog && g.last_played)
    .sort((a, b) => b.last_played - a.last_played).slice(0, 18);
  setHTML($("#recent"), list.length
    ? `<section class="cur-section"><div class="cur-title"><h2 class="cur-h">Recently played</h2></div><div class="grid cur-recent">${renderGrid(list)}</div></section>` : "");
}

/* ------------------------------------------------------------ Curator: In season, Hidden gems, Classics worth it */

// In season: a theme for each month (IGDB genres and themes), ranked like the picks.
const SEASONS = [
  { months: [0], title: "Long winter nights", hint: "big RPGs to sink into", test: (g) => has(g.genres, "Role-playing (RPG)") && (g.ttb || 0) >= 20 },
  { months: [1], title: "Love stories", hint: "romance, for February", test: (g) => has(g.themes, "Romance") || has(g.genres, "Visual Novel") },
  { months: [2], title: "Brain teasers", hint: "puzzle games", test: (g) => has(g.genres, "Puzzle") },
  { months: [3], title: "Spring jumps", hint: "platformers", test: (g) => has(g.genres, "Platform") },
  { months: [4], title: "Mysteries", hint: "something to solve", test: (g) => has(g.themes, "Mystery") || has(g.genres, "Point-and-click") },
  { months: [5, 6, 7], title: "Summer adventures", hint: "big worlds to roam", test: (g) => has(g.genres, "Adventure") && has(g.themes, "Open world") },
  { months: [8], title: "Back to strategy", hint: "for September", test: (g) => has(g.genres, "Strategy", "Tactical", "Turn-based strategy (TBS)", "Real Time Strategy (RTS)") },
  { months: [9], title: "Spooky season", hint: "horror, for October", test: (g) => has(g.themes, "Horror") },
  { months: [10], title: "Stories to curl up with", hint: "drama and narrative games", test: (g) => has(g.themes, "Drama") },
  { months: [11], title: "Cozy games", hint: "gentle ones for December",
    test: (g) => (has(g.themes, "Comedy", "Kids", "Party") || has(g.genres, "Simulator", "Puzzle")) && !has(g.themes, "Horror", "Warfare", "Thriller", "Survival", "Erotic") },
];
const has = (list, ...names) => (list || []).some((x) => names.includes(x));
const season = () => SEASONS.find((s) => s.months.includes(new Date().getMonth()));
const playedLittle = (g) => g.neverPlayed || (g.playtime_min || 0) < 120;
const yearOf = (g) => g.release_date ? new Date(g.release_date * 1000).getFullYear() : null;
const CUR_STRIP = 18;  // rendered per row; the row shows as many as fit the window

// The extra rows don't repeat what the picks above already show (or each other).
function curatorExtras() {
  const L = recLists();
  const seen = new Set([...L.keepQuick, ...L.keepCommit, ...L.freshQuick, ...L.freshCommit].map((g) => g.id));
  const take = (list) => { const out = list.filter((g) => !seen.has(g.id)).slice(0, CUR_STRIP); out.forEach((g) => seen.add(g.id)); return out; };
  const ok = state.games.filter((g) => recEligible(g) && !g.isWishOnly);
  const s = season();
  const byScore = (a, b) => (recScore(b) ?? -1) - (recScore(a) ?? -1) || a.title.localeCompare(b.title);
  const seasonList = s ? take(ok.filter((g) => s.test(g) && recScore(g) != null).sort(byScore)) : [];
  // Hidden gems: 90%+ on Steam from a small crowd (20 to 3,000 reviews), little or no play yet.
  const gems = take(ok.filter((g) => playedLittle(g) && g.steam_review_pct >= 90 && g.steam_review_count >= 20 && g.steam_review_count <= 3000)
    .sort((a, b) => b.steam_review_pct - a.steam_review_pct || b.steam_review_count - a.steam_review_count || a.title.localeCompare(b.title)));
  // Classics: out for ten years or more and still loved (85%+), little or no play yet.
  const tenYears = Date.now() / 1000 - 10 * 365.25 * 86400;
  const classics = take(ok.filter((g) => playedLittle(g) && g.release_date && g.release_date < tenYears && (recReview(g) ?? 0) >= 85)
    .sort((a, b) => recReview(b) - recReview(a) || a.title.localeCompare(b.title)));
  return { season: s, seasonList, gems, classics };
}
function stripCard(g, why, kind) {
  return `<button class="rec" data-id="${g.id}" data-why="${kind}">
      <div class="cover">${coverHTML(g)}${interestMark(g)}${instMark(g)}</div>
      <div class="rec-title">${esc(g.title)}</div>
      <div class="rec-why" title="Why this pick?">${why}</div>
    </button>`;
}
const scoreTag = (g) => g.steam_review_pct != null ? `<span class="score ${steamClass(g)}">${Math.round(g.steam_review_pct)}%</span>`
  : g.oc_score != null ? `<span class="score ${criticClass(g)}">${Math.round(g.oc_score)}%</span>` : "";
function renderCuratorExtras() {
  const x = curatorExtras();
  const strip = (el, title, hint, list, why, kind) => setHTML($(el), list.length
    ? `<section class="cur-section"><div class="cur-title"><h2 class="cur-h">${title}</h2><span class="cur-hint">${hint}</span></div>
       <div class="cur-strip">${list.map((g) => stripCard(g, why(g), kind)).join("")}</div></section>` : "");
  strip("#cur-season", x.season ? `In season: ${esc(x.season.title)}` : "In season", x.season ? esc(x.season.hint) : "", x.seasonList, (g) => recLines(g), "season");
  strip("#cur-gems", "Hidden gems", "loved by the few who've played them", x.gems,
    (g) => `<span class="why-l1">${scoreTag(g)} Steam</span><span class="why-l2">${g.steam_review_count.toLocaleString()} reviews</span>`, "gems");
  strip("#cur-classics", "Classics worth it", "ten years or more, and still great", x.classics,
    (g) => `${yearOf(g)} · ${scoreTag(g) || `${Math.round(recReview(g))}%`}`, "classics");
}

/* ------------------------------------------------------------ Curator: Why this pick? */

// Click a pick's reason line: what went into it, and what would move it.
function whyHTML(g, kind) {
  const steam = g.steam_review_pct, n = g.steam_review_count || 0, oc = g.oc_score;
  const review = recReview(g), left = recHoursLeft(g), sc = recScore(g);
  const time = left == null ? 50 : Math.max(10, Math.min(100, 100 - 21 * Math.log2(Math.max(left, 0.5) / 5)));
  const nudge = INTEREST_NUDGE[g.interest] || 0;
  const row = (label, value, note = "") => `<div class="why-row"><span>${label}</span><b>${value}</b>${note ? `<small>${note}</small>` : ""}</div>`;
  const revNote = steam != null
    ? `Steam ${Math.round(steam)}% from ${n.toLocaleString()} review${n === 1 ? "" : "s"}${n < 50 ? " (few, so it leans on " + (oc != null ? "OpenCritic" : "a middling 65") + ")" : ""}${oc != null ? ` · OpenCritic ${Math.round(oc)}` : ""}`
    : oc != null ? `OpenCritic ${Math.round(oc)}` : "No reviews yet: counts as 60";
  const lenNote = left == null ? "No time-to-beat data: counts as middling"
    : `${g.neverPlayed ? "About" : recRestart(g) ? "From the start (untouched for over a year):" : "Left to the credits: about"} ${fmtHours(left)}; shorter scores higher`;
  const list = kind === "season" ? `In season: ${esc(season()?.title || "")}. Fits this month's theme, ranked like the picks.`
    : kind === "gems" ? "Hidden gems: 90%+ on Steam from 20 to 3,000 reviews, and you've played it little or not at all."
    : kind === "classics" ? `Classics worth it: out in ${yearOf(g)}, reviews 85%+, and you've played it little or not at all.`
    : left != null && left >= COMMIT_HOURS ? `Worthwhile commitments: ${COMMIT_HOURS} h or more and reviews ${COMMIT_MIN}%+, ranked on reviews and your mark only (length doesn't count against it).`
    : `Quick wins: under ${COMMIT_HOURS} h ${g.neverPlayed ? "to beat" : "to go"}, ranked on the score below.`;
  const commit = !kind || kind === "picks" ? left != null && left >= COMMIT_HOURS : false;
  return `<h3>${esc(g.title)}</h3>
    <p class="why-list">${list}</p>
    ${row("Reviews", review == null ? "—" : Math.round(review), revNote)}
    ${commit ? "" : row("Length", Math.round(time), lenNote)}
    ${row(`Your mark`, nudge ? (nudge > 0 ? "+10" : "−10") : "0", g.interest ? `${INTEREST[g.interest]}` : "Not marked: Keen adds 10, Meh takes 10 off, Skip leaves it out")}
    ${sc != null ? row("Score", Math.round(commit ? (review ?? 0) + nudge : sc), commit ? "Reviews + your mark" : "70% reviews + 30% length + your mark") : ""}`;
}
function showWhy(g, kind, anchor) {
  let pop = $("#why-pop");
  if (!pop) { pop = document.createElement("div"); pop.id = "why-pop"; pop.className = "why-pop"; pop.setAttribute("role", "dialog"); document.body.append(pop); }
  pop.innerHTML = whyHTML(g, kind);
  pop.hidden = false;
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  pop.style.top = (r.bottom + 8 + h > innerHeight ? Math.max(8, r.top - h - 8) : r.bottom + 8) + "px";
  hidePeek(true);
}
const hideWhy = () => { const p = $("#why-pop"); if (p) p.hidden = true; };
document.addEventListener("click", (e) => { if (!e.composedPath().some((x) => x.id === "why-pop" || x.classList?.contains("rec-why"))) hideWhy(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideWhy(); });
addEventListener("scroll", hideWhy, { passive: true });
// (in the capture phase, before the card opens its game)
$("#page-curator").addEventListener("click", (e) => {
  const why = e.target.closest(".rec-why");
  const card = why?.closest(".rec[data-id]");
  if (!why || !card || !card.closest("#upnext, #cur-season, #cur-gems, #cur-classics")) return;
  e.preventDefault(); e.stopPropagation();
  showWhy(byId(Number(card.dataset.id)), card.dataset.why || "picks", why);
}, true);

/* ------------------------------------------------------------ Curator: hover previews */

// Point at a cover in Curator: a small card with a screenshot, the summary, and Play or Install.
let peekTimer = null, peekHide = null, peekId = null;
const previewsOn = () => state.settings?.settings?.curator_previews !== false;  // Settings › Display (on unless switched off)
// What the preview says about you and this game: where you have it, and your time with it.
function peekYou(g) {
  const subs = (g.subs || []).map((x) => SUB_SHORT[x.s]).filter(Boolean);
  // XGP and PS+ games are yours too (Xbox / PlayStation icon): the service is just how you have them.
  const own = `<span class="peek-k">Yours on</span><span class="peek-plats">${g.platforms.map(platformBadge).join("")}</span>`
    + (subs.length ? `<span class="peek-sub">${g.isCatalog ? "with" : "also on"} ${esc(subs.join(" and "))}</span>` : "");
  const played = g.played_h > 0 ? fmtHours(g.played_h) : null;
  let time;
  if (g.status === "completed") time = `Completed${played ? ` · ${played} played` : ""}`;
  else if (g.isEndless) time = played ? `${played} played · open-ended` : "Open-ended · not played yet";
  else if (!played) time = g.ttb ? `Not played yet · about <b>${fmtHours(g.ttb)}</b> to beat` : "Not played yet";
  else if (recRestart(g)) time = `${played} played, over a year ago · about <b>${fmtHours(g.ttb_extra || g.ttb_main || g.ttb)}</b> from the start`;
  else if (g.remaining > 0.25) time = `${played} played · about <b>${fmtHours(g.remaining)}</b> left`;
  else if (g.ttb) time = `${played} played · past the usual ${fmtHours(g.ttb)}`;
  else time = `${played} played`;
  return `<div class="peek-you">
      <div class="peek-own">${own}</div>
      ${!g.isEndless && g.progress != null && played ? spine(g, "peek-spine") : ""}
      <div class="peek-time">${time}</div>
    </div>`;
}
function peekHTML(g) {
  const shots = (g.screenshots || []).length ? g.screenshots.slice(0, 5) : g.hero_url ? [g.hero_url] : [];
  const genres = (g.genres || []).slice(0, 3).map(genreLabel).join(", ");
  const chips = [
    g.steam_review_pct != null ? `<span class="peek-chip"><span class="score ${steamClass(g)}">${Math.round(g.steam_review_pct)}%</span> Steam</span>` : "",
    g.oc_score != null ? `<span class="peek-chip"><span class="score ${criticClass(g)}">${Math.round(g.oc_score)}%</span> OpenCritic${g.oc_tier ? ` · ${esc(g.oc_tier)}` : ""}</span>` : "",
  ].join("");
  const inst = !g.canPlay ? installOrder(g)[0] : null;
  // The first screenshot large, the next ones as thumbnails (point at one to see it large; click to open them full size).
  // Shown whole (never cropped), on a dark backing when a shot isn't 16:9.
  return `${shots.length ? `<div class="peek-shots">
      <button type="button" class="peek-main" data-peek="shots" data-i="0" title="Open the screenshots"><img src="${esc(shots[0])}" alt=""></button>
      ${shots.length > 1 ? `<div class="peek-thumbs">${shots.slice(1).map((u, i) => `<button type="button" data-peek="shots" data-i="${i + 1}"><img src="${esc(u)}" alt="" loading="lazy"></button>`).join("")}</div>` : ""}
    </div>` : ""}
    <div class="peek-body">
      <div class="peek-head"><b>${esc(g.title)}</b>${yearOf(g) ? `<span>${yearOf(g)}</span>` : ""}</div>
      ${genres ? `<small class="peek-genres">${esc(genres)}</small>` : ""}
      ${chips ? `<div class="peek-chips">${chips}</div>` : ""}
      ${peekYou(g)}
      ${g.summary ? `<p>${esc(g.summary)}</p>` : ""}
      <div class="peek-actions">
        ${g.canPlay ? `<button type="button" class="btn small" data-peek="play">${PLAY_SVG} Play</button>`
          : inst ? `<button type="button" class="btn small ghost" data-peek="install" data-v="${inst}">${DL_SVG} Install on ${esc(PLATFORM_SHORT[inst] || inst)}</button>` : ""}
        <button type="button" class="btn small ghost" data-peek="open">Open</button>
      </div>
    </div>`;
}
function showPeek(card) {
  const g = byId(Number(card.dataset.id));
  if (!g || g.isWishOnly) return;
  let pk = $("#peek");
  if (!pk) {
    pk = document.createElement("div"); pk.id = "peek"; pk.className = "peek"; document.body.append(pk);
    pk.addEventListener("mouseenter", () => clearTimeout(peekHide));
    pk.addEventListener("mouseleave", () => hidePeek());
    pk.addEventListener("mouseover", (e) => {  // a thumbnail shows large while you point at it
      const t = e.target.closest(".peek-thumbs [data-i]");
      if (t) $(".peek-main img", pk).src = $("img", t).src;
    });
    pk.addEventListener("mouseleave", () => { const m = $(".peek-main img", pk), first = $(".peek-thumbs", pk) && byId(peekId)?.screenshots?.[0]; if (m && first) m.src = first; });
    pk.addEventListener("click", (e) => {
      const b = e.target.closest("[data-peek]");
      if (!b) return;
      const id = peekId;
      if (b.dataset.peek === "shots") {
        const g = byId(id); hidePeek(true);
        openLightbox((g.screenshots || []).length ? g.screenshots : [g.hero_url], Number(b.dataset.i) || 0,
          (g.screenshots || []).length ? id : null);
        return;
      }
      hidePeek(true);
      if (b.dataset.peek === "play") playGame(id);
      else if (b.dataset.peek === "install") installGame(id, b.dataset.v);
      else openGame(id);
    });
  }
  peekId = g.id;
  pk.innerHTML = peekHTML(g);
  pk.hidden = false;
  const r = card.querySelector(".cover").getBoundingClientRect(), w = pk.offsetWidth, h = pk.offsetHeight;
  const right = r.right + 12 + w <= innerWidth;
  pk.style.left = (right ? r.right + 12 : Math.max(8, r.left - w - 12)) + "px";
  pk.style.top = Math.max(8, Math.min(r.top, innerHeight - h - 8)) + "px";
}
function hidePeek(now = false) {
  clearTimeout(peekTimer);
  clearTimeout(peekHide);
  const go = () => { const pk = $("#peek"); if (pk) pk.hidden = true; peekId = null; };
  if (now) go(); else peekHide = setTimeout(go, 180);
}
$("#page-curator").addEventListener("mouseover", (e) => {
  const card = e.target.closest(".rec[data-id], .card[data-id], .shelf-cover[data-id]");
  if (!card || card.contains(e.relatedTarget) || !previewsOn()) return;
  clearTimeout(peekTimer); clearTimeout(peekHide);
  if ($("#why-pop") && !$("#why-pop").hidden) return;
  peekTimer = setTimeout(() => showPeek(card), Number(card.dataset.id) === peekId ? 0 : 450);
});
$("#page-curator").addEventListener("mouseout", (e) => {
  const card = e.target.closest(".rec[data-id], .card[data-id], .shelf-cover[data-id]");
  if (!card || card.contains(e.relatedTarget)) return;
  clearTimeout(peekTimer);
  hidePeek();
});
addEventListener("scroll", () => hidePeek(true), { passive: true });
document.addEventListener("click", (e) => { if (!e.composedPath().some((x) => x.id === "peek")) hidePeek(true); }, true);

/* ------------------------------------------------------------ Keen or not? */

// Games you haven't marked, one at a time, best first: Keen (1), Meh (2), Skip (3), not sure (→).
const kon = { list: [], i: 0, done: {} };
function konQueue() {
  return state.games.filter((g) => recEligible(g) && !g.interest && !g.isWishOnly)
    .sort((a, b) => (recScore(b) ?? -1) - (recScore(a) ?? -1) || a.title.localeCompare(b.title));
}
function openKon() {
  kon.list = konQueue().map((g) => g.id); kon.i = 0; kon.done = {};
  renderKon();
  $("#kon-dialog").showModal();
}
function renderKon() {
  const body = $("#kon-body");
  const counts = Object.values(kon.done).reduce((n, v) => { n[v] = (n[v] || 0) + 1; return n; }, {});
  const tally = Object.keys(counts).length ? `You marked ${["more", "less", "skip"].filter((k) => counts[k]).map((k) => `${counts[k]} ${INTEREST[k]}`).join(", ")}.` : "";
  if (kon.i >= kon.list.length) {
    body.innerHTML = `<div class="kon-end"><h3>${kon.list.length ? "That's all of them" : "Nothing left to mark"}</h3>
      <p>${tally || `Every game ${CURATOR} could suggest already has a mark.`} ${CURATOR}'s picks are up to date.</p>
      <div class="dialog-actions"><button type="button" class="btn" data-close-dialog>Done</button></div></div>`;
    return;
  }
  const g = byId(kon.list[kon.i]);
  if (!g) { kon.i++; renderKon(); return; }
  const cur = kon.done[g.id] || g.interest || "";
  const facts = [yearOf(g), (g.genres || []).slice(0, 3).map(genreLabel).join(", "),
    g.ttb ? `about ${fmtHours(g.ttb)} to beat` : g.isEndless ? "open-ended" : "",
    g.neverPlayed ? "not played yet" : `${fmtHours(g.played_h)} played`].filter(Boolean).join(" · ");
  const btn = (k, key) => `<button type="button" class="btn ${cur === k ? "" : "ghost"} kon-mark ${k}" data-kon="${k}">${INTEREST_ICON[k]()}${INTEREST[k]}<kbd>${key}</kbd></button>`;
  body.innerHTML = `<div class="kon">
      <div class="kon-cover cover">${coverHTML(g)}</div>
      <div class="kon-info">
        <h3>${esc(g.title)}</h3>
        <p class="kon-facts">${esc(facts)}</p>
        ${scoreTag(g) ? `<p class="kon-facts">${scoreTag(g)} ${g.steam_review_pct != null ? `on Steam${g.steam_review_count ? ` (${g.steam_review_count.toLocaleString()} reviews)` : ""}` : "on OpenCritic"}</p>` : ""}
        ${g.summary ? `<p class="kon-sum">${esc(g.summary)}</p>` : ""}
        <div class="badges kon-plats">${badgesHTML(g)}</div>
      </div>
    </div>
    <div class="kon-bar">
      <button type="button" class="btn icon ghost" data-kon="back" ${kon.i ? "" : "disabled"} aria-label="Back">←</button>
      ${btn("more", "1")}${btn("less", "2")}${btn("skip", "3")}
      <button type="button" class="btn ghost" data-kon="next">Not sure<kbd>→</kbd></button>
      <span class="spacer"></span>
      <span class="kon-count">${(kon.i + 1).toLocaleString()} of ${kon.list.length.toLocaleString()}</span>
    </div>
    <p class="source-note kon-note">${tally || `Games ${CURATOR} might suggest, best first. Stop whenever you like.`}</p>`;
}
async function konMark(v) {
  const id = kon.list[kon.i];
  if (!id) return;
  kon.done[id] = v;
  kon.i++;
  renderKon();
  try { await patchGame(id, { interest: v }); } catch (e) { toast(e.message, true); }
}
$("#cur-kon").addEventListener("click", openKon);
$("#kon-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-kon]");
  if (!b) return;
  const k = b.dataset.kon;
  if (k === "back") { kon.i = Math.max(0, kon.i - 1); renderKon(); return; }
  if (k === "next") { kon.i++; renderKon(); return; }
  konMark(k);
});
$("#kon-dialog").addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea")) return;
  const k = { 1: "more", 2: "less", 3: "skip" }[e.key];
  if (k) { e.preventDefault(); konMark(k); }
  else if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); kon.i++; renderKon(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); kon.i = Math.max(0, kon.i - 1); renderKon(); }
});

// Two pages: CURATOR (the default) and Library. The address remembers which (#library).
function showPage(page, { push = true } = {}) {
  if (!state.games.length && page === "curator") page = "library";  // nothing to recommend yet: setup lives there
  state.page = page;
  document.body.dataset.page = page;
  $$(".page-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.page === page)));
  $$(".page-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.page === page));
  if (push) history.replaceState(null, "", "#" + page);  // a reload stays on this tab; a fresh start uses Settings › Display
  scrollTo({ top: 0 });
  if (page === "year") renderYear();
}

// The same on two lines under a Curator cover: the score, then the hours ("4.3h to beat").
function recLines(g) {
  const [a = "", b = ""] = recShort(g).split(" · ");
  return `<span class="why-l1">${a}</span><span class="why-l2">${b}</span>`;
}
// The to-the-point version of recReason for Curator: "97% Steam · 4.3h to beat".
function recShort(g) {
  const bits = [];
  if (g.steam_review_pct != null) bits.push(`<span class="score ${steamClass(g)}">${Math.round(g.steam_review_pct)}%</span> Steam`);
  else if (g.oc_score != null) bits.push(`<span class="score ${criticClass(g)}">${Math.round(g.oc_score)}%</span> OpenCritic`);
  const left = recHoursLeft(g);
  if (left != null) bits.push(g.neverPlayed ? `${shortH(left)} to beat` : recRestart(g) ? `${shortH(left)} restart` : `${shortH(left)} left`);
  return bits.join(" · ");
}

const interestMark = (g) => INTEREST_ICON[g.interest]
  ? `<span class="imark ${g.interest}" title="${INTEREST[g.interest]}">${INTEREST_ICON[g.interest]("ico")}</span>` : "";

async function setInterest(id, v) {
  const g0 = byId(id);
  const next = g0 && g0.interest === v ? null : v;  // picking the same again clears it
  const prev = g0 ? g0.interest || null : null;
  try {
    const g = await patchGame(id, { interest: next });
    toast(next ? `${INTEREST[next]}: ${g.title}` : `${CURATOR} treats ${g.title} normally again`, false,
      () => undoPatch(id, { interest: prev }, "Put back"));
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ Wishlist */

// Your Steam and PlayStation wishlists (the app reads them; it can't change them), priced on GOG and Epic too.
const WISH_PLATFORM = { steam: "steam", psn: "playstation", gog: "gog", epic: "epic" };
const WISH_STORE = { steam: "Steam", psn: "PlayStation", gog: "GOG", epic: "Epic Games Store" };
const WISH_SHORT = { steam: "Steam", psn: "PlayStation", gog: "GOG", epic: "Epic" };
const WISH_LETTER = { steam: "S", psn: "PS", gog: "G", epic: "E" };  // shown when the store icons can't load
const WISH_DAYS = 60;  // Curator's "Recently released" and "Coming out soon" windows
const nowS = () => Date.now() / 1000;

// When it comes out: {state: out | soon | tba, ts, prec: day | month | quarter | year, text}.
// The stores' own dates first (a console game coming to PC is "soon" on Steam), else IGDB's.
function wishRelease(g) {
  const w = g.wish || {}, igdb = g.release_date, now = nowS();
  if (g.isEA) return { state: "ea", ts: g.eaFull && g.eaFull > now ? g.eaFull : null, prec: "day", text: "In early access" };
  if (w.released === true) return { state: "out", ts: w.release_ts || igdb || null, prec: "day" };
  if (w.release_ts) return { state: w.release_ts <= now && w.precision === "day" ? "out" : "soon", ts: w.release_ts, prec: w.precision || "day" };
  if (igdb && (w.released == null || igdb > now)) return { state: igdb <= now ? "out" : "soon", ts: igdb, prec: "day" };
  return { state: "tba", ts: null, prec: null, text: w.release_text || "No date yet" };
}
function fmtRelease(r) {
  if (!r || !r.ts) return (r && r.text) || "No date yet";
  const d = new Date(r.ts * 1000);
  if (r.prec === "year") return String(d.getUTCFullYear());
  if (r.prec === "quarter") return `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${d.getUTCFullYear()}`;
  if (r.prec === "month") return d.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}
function inDays(ts) {
  const days = Math.round((ts - nowS()) / 86400);
  return days <= 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
}
function wishWhen(g, { soonDays = false } = {}) {
  const r = g.rel;
  if (r.state === "out") return r.ts ? `Out ${fmtRelease(r)}` : "Out now";
  if (r.state === "soon") return fmtRelease(r) + (soonDays && r.prec === "day" && r.ts - nowS() < 45 * 86400 ? ` · ${inDays(r.ts)}` : "");
  if (r.state === "ea") return r.ts ? `Full release ${fmtRelease(r)}${soonDays && r.ts - nowS() < 45 * 86400 ? ` · ${inDays(r.ts)}` : ""}` : "In early access";
  return r.text || "No date yet";
}
// Already playable: yours on another store ("Owned"), or on XGP / PS+. Short, for card lines.
function playableTags(g) {
  const owned = (g.wish?.owned_on || []).some((p) => p !== "xbox");  // see wishlist.by_game
  return [...(owned ? ["Owned"] : []), ...[...new Set((g.subs || []).map((x) => x.s))].map((s) => SUB_SHORT[s])];
}
const isPlayable = (g) => playableTags(g).length > 0;
function saleText(x) {
  return `${x.discount_pct}% off${x.sale_note ? ` ${x.sale_note}` : ""}${x.sale_ends ? ` until ${fmtDate(x.sale_ends)}` : ""}: ${x.price_text}${x.base_text ? `, usually ${x.base_text}` : ""}`;
}
// The sale badge on the cover ("On XGP" and the like sit next to the price instead).
function wishPills(g, { sale = true } = {}) {
  // You can already play it: no sale badge, except on the Wishlist page while "On sale" is picked.
  const x = isPlayable(g) && !(state.prefs.wishSale && state.page === "wishlist") ? null : g.wish?.sale;
  const pills = (sale && x ? `<span class="wpill sale" title="${esc(`${WISH_STORE[x.store]}: ${saleText(x)}`)}">-${x.discount_pct}%</span>` : "") + eaPill(g);
  return pills ? `<div class="wpills">${pills}</div>` : "";
}
// "Early access" on a cover (top left).
const eaPill = (g) => g.isEA ? `<span class="ea-pill" title="In early access${g.eaFull ? `: full release ${fmtDate(g.eaFull)}` : ": still being made"}">Early access</span>` : "";
// Settings › Subscriptions (also a setup step).
const subSwitches = (on = subscribed) => `
  <label class="switch"><input type="checkbox" data-sub="gamepass" ${on("gamepass") ? "checked" : ""}><span>I have <b>XGP</b>: PC Game Pass, or Game Pass Ultimate</span></label>
  <label class="switch"><input type="checkbox" data-sub="psplus" ${on("psplus") ? "checked" : ""}><span>I have <b>PS+</b> Extra or Premium (Essential doesn't include the game catalog)</span></label>`;
async function saveSub(input) {
  await api("/api/settings", { method: "POST", body: { [`sub_${input.dataset.sub}`]: input.checked } });
  await loadSettings();
  await reloadLibrary();
}
// Settings › Display: VR games are hidden unless you say otherwise; early access games are shown.
const hideEA = () => !!state.settings?.settings?.hide_early_access;
const hideVR = () => state.settings?.settings?.hide_vr !== false;
// The price to show: the sale if there is one, else the lowest listed price.
// Followed by where you can already play it: "C$ 38.99 · On XGP".
// The lowest price across Steam and PlayStation (wishlist.py looks up the store whose wishlist
// doesn't have it), with that store's plain icon in front (the hearts on the cover say which wishlists).
// Already playable (owned, XGP, PS+): just that, no price.
function wishPrice(g) {
  const tags = playableTags(g);
  if (tags.length) return `<span class="wtags">${tags.map((t) => `<span class="wtag ${t === "Owned" ? "owned" : "sub"}">${esc(t)}</span>`).join("")}</span>`;
  const x = g.wish?.best;
  if (!x) return "";
  if (x.price_cents === 0) return `<span class="wtags"><span class="wtag free" title="Free on ${esc(WISH_STORE[x.store] || x.store)}">Free</span></span>`;
  return `<span class="wp-store">${storeMark(x.store, false)}</span><span class="wp${x.discount_pct ? " on-sale" : ""}">${esc(x.price_text)}</span>${x.base_text ? ` <s>${esc(x.base_text)}</s>` : ""}`;
}
// XGP / PS+ marks for the game page boxes: the platform's regular icon (Xbox, PlayStation).
function subMark(sv) {
  const p = SUB_PLATFORM[sv];
  return `<span class="smark" title="${esc(SUB_SHORT[sv])}"><i style="--icon:url('${iconUrl(PLATFORM_ICON[p])}')"></i><b>${p === "xbox" ? "X" : "PS"}</b></span>`;
}
// A store's mark: the heart version when the game is on that store's wishlist, else its plain icon.
function storeMark(store, wished) {
  if (wished) return wishBadge(store);
  const name = WISH_STORE[store];
  return `<span class="smark" title="${esc(name)}" aria-label="${esc(name)}"><i style="--icon:url('${iconUrl(PLATFORM_ICON[WISH_PLATFORM[store]])}')"></i><b>${WISH_LETTER[store] || "?"}</b></span>`;
}

// Wishlist marker: a heart with the store's icon inside, so it never reads as a copy you own.
function wishBadge(store) {
  const p = WISH_PLATFORM[store], name = `On your ${WISH_STORE[store]} wishlist`;
  return `<span class="wheart" title="${esc(name)}" aria-label="${esc(name)}"><i style="--icon:url('${iconUrl(PLATFORM_ICON[p])}')"></i><b>${WISH_LETTER[store] || "?"}</b></span>`;
}
// Bottom-left, like library covers: the platforms you can already play it on (owned, or on XGP / PS+).
function playableBadges(g) {
  const plats = new Set([...(g.wish?.owned_on || []).filter((p) => p !== "xbox"), ...(g.subs || []).map((x) => SUB_PLATFORM[x.s])]);
  return plats.size ? `<div class="badges">${[...plats].filter(Boolean).sort().map(platformBadge).join("")}</div>` : "";
}
const wishBadges = (g) => `<div class="whearts">${(g.wish?.stores || []).map((x) => wishBadge(x.store)).join("")}</div>`;

const allWish = () => [...state.games, ...state.wishGames].filter((g) => g.wish);

function wishVisible() {
  const p = state.prefs, q = state.q.trim().toLowerCase();
  return allWish().filter((g) => (!g.isHidden || typesSel().includes("hidden"))
    && (!q || g._search.includes(q))
    && (p.wishStore === "all" || g.wish.stores.some((x) => x.store === p.wishStore))
    && (!p.wishSale || g.wish.sale));  // the On sale filter includes games you can already play
}

const PREC_RANK = { day: 0, month: 1, quarter: 2, year: 3 };
function wishSorter(group) {
  const key = state.prefs.wishSort;
  const t = (a, b) => a.title.localeCompare(b.title);
  // Coming soon is always soonest first, whatever the sort menu says.
  if (group === "soon") return (a, b) => a.rel.ts - b.rel.ts || PREC_RANK[a.rel.prec] - PREC_RANK[b.rel.prec] || t(a, b);
  if (key === "added") return (a, b) => (b.wish.added_at || 0) - (a.wish.added_at || 0) || t(a, b);
  if (key === "title") return t;
  if (key === "discount") return (a, b) => (b.wish.sale?.discount_pct || 0) - (a.wish.sale?.discount_pct || 0) || t(a, b);
  if (key === "reviews") return (a, b) => (recReview(b) ?? -1) - (recReview(a) ?? -1) || t(a, b);
  // Release date: newest first among games that are out, soonest first among upcoming ones.
  if (group === "out") return (a, b) => (b.rel.ts || 0) - (a.rel.ts || 0) || t(a, b);
  return (a, b) => (b.wish.added_at || 0) - (a.wish.added_at || 0) || t(a, b);
}

// Wishlist › My order: your ranking (wish_order). Before you've moved anything it starts from the
// reviews; once you have, games added to a wishlist since go on top, newest first.
function wishOrdered(list) {
  const order = state.wishOrder || [];
  if (!order.length) return list.slice().sort(wishByReviews);
  const pos = new Map(order.map((id, i) => [id, i]));
  const fresh = list.filter((g) => !pos.has(g.id)).sort((a, b) => (b.wish.added_at || 0) - (a.wish.added_at || 0) || wishByReviews(a, b));
  return [...fresh, ...list.filter((g) => pos.has(g.id)).sort((a, b) => pos.get(a.id) - pos.get(b.id))];
}
const wishByReviews = (a, b) => (recReview(b) ?? -1) - (recReview(a) ?? -1) || (a.rel.ts || Infinity) - (b.rel.ts || Infinity) || a.title.localeCompare(b.title);
async function saveWishOrder(ids) {
  state.wishOrder = ids;
  renderWishlist();
  try { await api("/api/wishlist/order", { method: "POST", body: { ids } }); } catch (e) { toast(e.message, true); }
}
// Moves a game within the whole order (games filtered out of view keep their places).
function moveInWishOrder(id, targetId, after) {
  const ids = wishOrdered(allWish().filter((g) => !g.isHidden || typesSel().includes("hidden"))).map((g) => g.id).filter((x) => x !== id);
  let i = targetId == null ? (after ? ids.length : 0) : ids.indexOf(targetId) + (after ? 1 : 0);
  if (i < 0) i = ids.length;
  ids.splice(i, 0, id);
  saveWishOrder(ids);
}

function wishCards(list, { ranked = false } = {}) {
  return list.map((g, i) => `
    <button class="card wcard" data-id="${g.id}" ${ranked ? `draggable="true"` : ""}>
      <div class="cover">
        ${coverHTML(g)}
        ${wishPills(g)}
        ${g.isHidden ? `<span class="stamp hidden-stamp">Hidden</span>` : ""}
        ${wishBadges(g)}
        ${playableBadges(g)}
      </div>
      <div class="card-title">${ranked ? `<span class="wrank">${i + 1}</span>` : ""}${esc(g.title)}</div>
      ${g.rel.state === "out"  // already out: no date, just the review score (if any)
        ? (recReview(g) != null ? `<div class="card-meta">${recScoreTag(g)} on ${g.steam_review_pct != null ? "Steam" : "OpenCritic"}</div>` : "")
        : `<div class="card-meta">${esc(wishWhen(g, { soonDays: true }))}</div>`}
      ${wishPrice(g) ? `<div class="wprice">${wishPrice(g)}</div>` : ""}
    </button>`).join("");
}
// The review score in a card line: Steam's, else OpenCritic's.
function recScoreTag(g) {
  return g.steam_review_pct != null ? `<span class="score ${steamClass(g)}">${Math.round(g.steam_review_pct)}%</span>`
    : g.oc_score != null ? `<span class="score ${criticClass(g)}">${Math.round(g.oc_score)}%</span>` : "";
}

const WISH_GROUPS = [["out", "Out now"], ["ea", "In early access"], ["soon", "Coming soon"], ["tba", "No date yet"]];
function renderWishlist() {
  const el = $("#wishlist");
  if (!el) return;
  const all = allWish().filter((g) => !g.isHidden || typesSel().includes("hidden"));
  if (!all.length) {
    el.innerHTML = `<div class="empty"><h2>${state.wishlistAt ? "Your wishlists are empty" : "Your wishlists will show up here"}</h2>
      <p>${state.wishlistAt
        ? "Nothing on your wishlists right now. If that's not right, check that your Steam profile's game details are public."
        : "The next sync reads your wishlists: Steam (from your Steam profile; its game details need to be public) and PlayStation (with your NPSSO token from Settings)."}</p>
      <button class="btn" id="wish-sync">Sync now</button></div>`;
    return;
  }
  const p = state.prefs;
  const list = wishVisible();
  const counts = Object.fromEntries(Object.keys(WISH_STORE).map((st) => [st, all.filter((g) => g.wish.stores.some((x) => x.store === st)).length]));
  const withGames = Object.keys(WISH_STORE).filter((st) => counts[st]);
  const onSale = all.filter((g) => g.wish.sale && (p.wishStore === "all" || g.wish.stores.some((x) => x.store === p.wishStore))).length;
  const mine = p.wishSort === "mine";
  const groups = mine
    ? (list.length ? `<section class="wish-group wish-mine"><p class="source-note wish-mine-hint">Drag a cover to move it, or right-click › Move to top / Move to bottom. Games you add to a wishlist start at the top.</p>
        <div class="grid">${wishCards(wishOrdered(list), { ranked: true })}</div></section>` : "")
    : WISH_GROUPS.map(([k, label]) => {
      const gs = list.filter((g) => g.rel.state === k).sort(wishSorter(k));
      return gs.length ? `<section class="wish-group" id="wish-sec-${k}" data-wsec="${k}"><h2 class="wish-h">${label} <span>${gs.length}</span></h2><div class="grid">${wishCards(gs)}</div></section>` : "";
    }).join("");
  // The sections at a glance, in a bar that stays at the top: click one to jump to it.
  const present = mine ? [] : WISH_GROUPS.filter(([k]) => list.some((g) => g.rel.state === k));
  const jump = present.length > 1 ? `<nav class="wish-jump" aria-label="Wishlist sections">${present.map(([k, label]) =>
    `<button type="button" data-wjump="${k}">${label}<em>${list.filter((g) => g.rel.state === k).length}</em></button>`).join("")}</nav>` : "";
  const seg = (v, label, n) => `<button data-wf="wishStore" data-v="${v}" class="${p.wishStore === v ? "on" : ""}" aria-pressed="${p.wishStore === v}">${label}${n != null ? ` <em>${n}</em>` : ""}</button>`;
  setHTML(el, `
    <div class="toolbar wish-toolbar">
      <div class="result-count"><b>${list.length.toLocaleString()}</b> ${list.length === all.length ? `game${all.length === 1 ? "" : "s"} on your wishlists` : `of ${all.length.toLocaleString()} wishlist games`}${state.wishlistAt ? ` <span class="source-note">· checked ${esc(relDate(state.wishlistAt).toLowerCase())}</span>` : ""}</div>
      <div class="toolbar-right">
        ${withGames.length > 1 ? `<div class="segmented wish-stores" role="group" aria-label="Wishlist">${seg("all", "All")}${withGames.map((st) => seg(st, WISH_SHORT[st], counts[st])).join("")}</div>` : ""}
        <button class="chip ${p.wishSale ? "on" : ""}" data-wf="sale" aria-pressed="${p.wishSale}">On sale <em>${onSale}</em></button>
        <label class="sort"><span>Sort by</span>
          <select id="wish-sort">
            ${[["mine", "My order"], ["release", "Release date"], ["added", "Date added"], ["discount", "Biggest discount"], ["reviews", "Reviews"], ["title", "Title"]]
              .map(([k, l]) => `<option value="${k}" ${p.wishSort === k ? "selected" : ""}>${l}</option>`).join("")}
          </select></label>
        <input type="range" class="wish-size" min="120" max="260" step="10" value="${p.size}" aria-label="Cover size" title="Cover size">
      </div>
    </div>
    ${jump}
    ${groups || `<div class="empty"><h2>Nothing matches</h2><p>No wishlist games fit ${p.wishSale ? "“On sale”" : "these choices"}${state.q ? ` and the search “${esc(state.q)}”` : ""}.</p></div>`}`);
}

// Curator: what's new or close from your wishlists, and what's on sale.
function renderWishRows() {
  const el = $("#wishrows");
  // VR follows Settings › Hidden game types; early access stays (its full release is news).
  const gs = allWish().filter((g) => !g.isHidden && !(g.vr && hideVR()));
  if (!gs.length) { el.innerHTML = ""; return; }
  const now = nowS(), win = WISH_DAYS * 86400;
  const recent = gs.filter((g) => g.rel.state === "out" && g.rel.ts && now - g.rel.ts <= win && g.rel.ts <= now)
    .sort((a, b) => b.rel.ts - a.rel.ts || a.title.localeCompare(b.title));
  // (an early access game whose full release is announced counts too)
  const soon = gs.filter((g) => (g.rel.state === "soon" || (g.rel.state === "ea" && g.rel.ts)) && g.rel.prec === "day" && g.rel.ts - now <= win && g.rel.ts > now - 86400)
    .sort((a, b) => a.rel.ts - b.rel.ts || a.title.localeCompare(b.title));
  const sale = gs.filter((g) => g.wish.sale && !isPlayable(g))
    .sort((a, b) => b.wish.sale.discount_pct - a.wish.sale.discount_pct || (recReview(b) ?? -1) - (recReview(a) ?? -1) || a.title.localeCompare(b.title));
  const mini = (g, line, opts) => `
    <button class="rec mini" data-id="${g.id}" title="${esc(g.title)}">
      <div class="cover">${coverHTML(g)}${wishPills(g, opts)}${wishBadges(g)}${playableBadges(g)}</div>
      <div class="rec-title">${esc(g.title)}</div>
      <div class="rec-why">${line}</div>
    </button>`;
  const row = (title, hint, list, line, empty, opts = { sale: false }) => `
    <div class="cur-sub">
      <h3 class="cur-sh">${title}<span>${hint}</span></h3>
      ${list.length ? `<div class="mini-row">${list.slice(0, 16).map((g) => mini(g, line(g), opts)).join("")}</div>` : `<p class="source-note">${empty}</p>`}
    </div>`;
  setHTML(el, `
    <section class="cur-section">
      <div class="cur-title"><h2 class="cur-h">From your wishlist</h2></div>
      <div class="cur-wish-split" style="--soon-w: ${soon.length ? `${Math.min(soon.length, 16) * 126 - 14}px` : "300px"}">
        ${row("Coming out soon", `the next ${WISH_DAYS} days`, soon, (g) => esc(wishWhen(g, { soonDays: true })),
          `Nothing on your wishlist has a release date in the next ${WISH_DAYS} days.`)}
        ${row("Recently released", `the last ${WISH_DAYS} days`, recent, (g) => `${esc(fmtRelease(g.rel))}${recReview(g) != null ? ` · ${recScoreTag(g)}` : ""}`,
          `Nothing from your wishlist came out in the last ${WISH_DAYS} days.`)}
      </div>
      ${row("On sale", "biggest discount first", sale, (g) => `${wishPrice(g)}${recReview(g) != null ? `<span class="rec-why-line">${recScoreTag(g)} on ${g.steam_review_pct != null ? "Steam" : "OpenCritic"}</span>` : ""}`,
        "Nothing on your wishlist is on sale right now.", { sale: true })}
    </section>`);
}

// On a game's page: one box per store with its price, the cheapest outlined. The store's mark
// says whether it's on that store's wishlist (heart, with the date it was added) or not (plain icon).
function wishBox(g) {
  if (!g.wish) return "";
  const w = g.wish;
  // Owned on Steam / PlayStation: that store's box says so. Other stores (Switch, Epic…) are listed below the boxes.
  const ownedStores = Object.keys(WISH_STORE).filter((st) => (w.owned_on || []).includes(WISH_PLATFORM[st]));
  const tags = (w.owned_on || []).filter((p) => p !== "xbox" && !["steam", "playstation", "gog", "epic"].includes(p)).map((p) => `Owned on ${PLATFORM_SHORT[p] || p}`);
  const when = (x) => {
    if (!x || x.released === 1 || g.rel.state === "out") return "";  // out already: the date doesn't matter
    if (x.release_ts) return `Coming ${fmtRelease({ ts: x.release_ts, prec: x.precision || "day" })}`;
    return x.release_text || (g.rel.state === "soon" ? `Coming ${fmtRelease(g.rel)}` : "");
  };
  const listed = Object.fromEntries((w.stores || []).map((x) => [x.store, x]));  // on that store's wishlist
  // One row of compact boxes, in this order: where you own it (green), services it's included in
  // (green), prices cheapest first (the cheapest outlined), then the rest. Details that don't fit
  // (the usual price, when you wishlisted it) are in the box's tooltip.
  const short = (ts) => new Date(ts * 1000).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
  const wishedTip = (st) => listed[st] ? (listed[st].added_at
    ? `On your ${WISH_STORE[st]} wishlist since ${fmtDate(listed[st].added_at)}${st === "psn" ? ` (when ${APP} first saw it)` : ""}`
    : `On your ${WISH_STORE[st]} wishlist`) : "";
  const shell = ({ cls = "", url, tip = [], mark, name, price, small = "" }) => {
    const t = [...tip, url ? "Open the store page" : ""].filter(Boolean).join(". ");
    const tag = url ? `a href="${esc(url)}" target="_blank" rel="noopener"` : "div";
    return `<${tag} class="pbox ${cls}"${t ? ` title="${esc(t)}"` : ""}>
      <span class="pbox-top">${mark}<b>${esc(name)}</b></span>
      <span class="pbox-price">${price}</span><small>${small}</small></${url ? "a" : "div"}>`;
  };
  const prices = w.prices || [];
  const cheapest = prices.length > 1 && prices[0].price_cents < prices[1].price_cents ? prices[0].store : null;
  const items = [];  // [rank, html]
  for (const st of ownedStores) items.push([0, shell({ cls: "owned", url: listed[st]?.url, tip: [wishedTip(st)], mark: storeMark(st, !!listed[st]),
    name: WISH_SHORT[st], price: "Owned", small: "In your library" })]);
  for (const sv of ["gamepass", "psplus"]) {
    const name = SUB_SHORT[sv], what = sv === "gamepass" ? "PC Game Pass" : "Extra catalog";
    if (!subscribed(sv)) { items.push([6, shell({ cls: "sub none", tip: ["Settings › Subscriptions"], mark: subMark(sv), name, price: `<span class="dim">Not subscribed</span>`, small: "See Settings" })]); continue; }
    const x = (g.subs || []).find((t) => t.s === sv);
    const link = (g.store_links || []).find((l) => l.s === sv);
    if (!x) { items.push([5, shell({ cls: "sub", mark: subMark(sv), name, price: `<span class="dim">Not included</span>`, small: what })]); continue; }
    const small = x.leaving ? `<span class="leaving">Leaving soon</span>` : x.new && x.since ? `Added ${esc(relDate(x.since).toLowerCase())}` : what;
    items.push([1, shell({ cls: "sub in", url: link?.url, mark: subMark(sv), name, price: "Included", small })]);
  }
  prices.forEach((x, i) => {
    const tip = [x.base_text ? `Usually ${x.base_text}` : "", x.sale_note ? `Sale price ${x.sale_note}` : "", wishedTip(x.store)];
    const small = x.discount_pct ? (x.sale_ends ? `Until ${short(x.sale_ends)}` : `Sale${x.sale_note ? ` ${x.sale_note}` : ""}`) : "Regular price";
    items.push([2 + i / 100, shell({ cls: x.store === cheapest ? "best" : "", url: x.url, tip, mark: storeMark(x.store, !!listed[x.store]), name: WISH_SHORT[x.store],
      price: `<span class="${x.discount_pct ? "on-sale" : ""}">${esc(x.price_text)}</span>${x.discount_pct ? `<i class="poff">-${x.discount_pct}%</i>` : ""}`,
      small: esc(small) })]);
  });
  for (const s of (w.stores || []).filter((s) => !prices.some((x) => x.store === s.store) && !ownedStores.includes(s.store))) {
    items.push([3, shell({ url: s.url, tip: [wishedTip(s.store)], mark: storeMark(s.store, true), name: WISH_SHORT[s.store], price: "—", small: esc(when(s) || "No price yet") })]);
  }
  // Not sold there: said for Steam and PlayStation; GOG and Epic only show up when they sell it
  for (const st of (w.missing || []).filter((st) => st === "steam" || st === "psn")) {
    items.push([4, shell({ cls: "none", tip: [`Not sold on ${st === "psn" ? "the PlayStation Store" : "Steam"}`], mark: storeMark(st, false), name: WISH_SHORT[st], price: `<span class="dim">Not sold</span>`, small: "Not on this store" })]);
  }
  const boxes = items.sort((a, b) => a[0] - b[0]).map((x) => x[1]);
  return `<section class="wish-box">
    <h2>Wishlist and prices</h2>
    <div class="pboxes">${boxes.join("")}</div>
    ${tags.length ? `<p class="source-note">You can already play it: ${esc(tags.join(" · "))}.</p>` : ""}
  </section>`;
}

/* ------------------------------------------------------------ rendering: library */

function spine(g, extra = "") {
  if (g.status === "completed") return `<div class="spine completed ${extra}" title="Completed"><i style="width:100%"></i></div>`;
  if (g.isEndless) return `<div class="spine endless ${extra}" title="Open-ended: no finish line"></div>`;
  if (g.progress == null) return `<div class="spine unknown ${extra}" title="No time-to-beat data"></div>`;
  const pct = Math.max(g.progress * 100, g.played_h > 0 ? 3 : 0);
  const title = `${fmtHours(g.played_h, { empty: "0 h" })} of about ${fmtHours(g.ttb)}`;
  return `<div class="spine ${g.status === "dnf" ? "dnf" : ""} ${extra}" title="${esc(title)}"><i style="width:${pct.toFixed(1)}%"></i></div>`;
}

// Compact hours for the ends of a progress bar: 45m, 3.5h, 12h.
function shortH(h) {
  if (h == null || isNaN(h)) return "";
  if (h <= 0) return "0h";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 10) return `${Math.round(h * 10) / 10}h`;
  return `${Math.round(h).toLocaleString()}h`;
}

// Progress bar with hours played on the left and hours to go on the right.
function meter(g) {
  if (g.neverPlayed && !g.status) {
    // Never played: the whole length ahead, drawn as a dashed line.
    const len = g.isEndless ? `<span class="m-r inf">∞</span>` : g.ttb ? `<span class="m-r" title="About ${fmtHours(g.ttb)} to beat">${shortH(g.ttb)}</span>` : `<span class="m-r" title="No time-to-beat data yet">?</span>`;
    return `<div class="meter fresh" title="Not played yet${g.ttb && !g.isEndless ? `, about ${fmtHours(g.ttb)} to beat` : ""}"><span class="m-l">Unplayed</span><div class="spine fresh"></div>${len}</div>`;
  }
  const played = g.hoursUnknown
    ? `<span class="m-l" title="Launched, but the store doesn't say for how long">?h</span>`
    : `<span class="m-l" title="Played">${shortH(g.played_h)}</span>`;
  if (g.status === "completed") {
    return `<div class="meter">${played}${spine(g)}<span class="m-r done" title="Completed">✓</span></div>`;
  }
  if (g.isEndless) {
    return `<div class="meter endless" title="Open-ended: no finish line">${played}<div class="inf-line"></div><span class="m-r inf">∞</span></div>`;
  }
  if (g.progress == null) {
    return `<div class="meter">${played}${spine(g)}<span class="m-r" title="No time-to-beat data yet">?</span></div>`;
  }
  if (g.status === "dnf") return `<div class="meter">${played}${spine(g)}<span class="m-r"></span></div>`;
  const left = g.remaining > 0.25
    ? `<span class="m-r" title="About ${fmtHours(g.remaining)} to the credits">${shortH(g.remaining)}<em> left</em></span>`
    : `<span class="m-r" title="Past the expected length">almost</span>`;
  return `<div class="meter">${played}${spine(g)}${left}</div>`;
}

function cardMeta(g) {
  switch (state.prefs.sort) {
    case "playtime": return g.hoursUnknown ? "Hours unknown" : fmtHours(g.played_h, { empty: "Not played" });
    case "release_date": return year(g.release_date) || "Release date unknown";
    case "ttb_main": return g.isEndless ? "Open-ended" : g.ttb ? `About ${fmtHours(g.ttb)} to beat` : "Time to beat unknown";
    case "remaining": return g.status === "completed" ? "Completed" : g.isEndless ? "Open-ended" : g.remaining != null ? (g.remaining > 0 ? `About ${fmtHours(g.remaining)} to the credits` : "Credits are close") : "Time to beat unknown";
    case "progress": return g.isEndless ? "Open-ended" : g.progress != null ? `${Math.round(g.progress * 100)}% through` : "Time to beat unknown";
    case "critic_score": return g.critic ? `OpenCritic ${Math.round(g.critic)}%` : "No OpenCritic score";
    case "steam_review_pct": return g.steam_review_pct != null ? `${Math.round(g.steam_review_pct)}% positive on Steam` : "No Steam reviews";
    case "added_at": return `Added ${relDate(g.added_at).toLowerCase()}`;
    case "recommended": return recScore(g) != null ? recReason(g) : g.interest === "skip" ? "Skipped" : "Not suggested";
    default: return g.last_played ? relDate(g.last_played) : (g.playtime_min ? `${fmtHours(g.played_h)}, date unknown` : "Not played");
  }
}
// Review scores on a green-to-red scale. OpenCritic's tier and Steam's own wording
// decide the colour when there is one; the number decides otherwise.
function scoreClass(v) {
  if (v == null) return "";
  return v >= 85 ? "s-great" : v >= 75 ? "s-good" : v >= 60 ? "s-mixed" : v >= 45 ? "s-meh" : "s-poor";
}
const OC_TIER = { mighty: "s-great", strong: "s-good", fair: "s-mixed", weak: "s-poor" };
const STEAM_DESC = {
  "overwhelmingly positive": "s-great", "very positive": "s-good", "positive": "s-good",
  "mostly positive": "s-mixed", "mixed": "s-meh", "mostly negative": "s-poor",
  "negative": "s-poor", "very negative": "s-poor", "overwhelmingly negative": "s-poor",
};
const criticClass = (g) => OC_TIER[(g.oc_tier || "").toLowerCase()] || scoreClass(g.oc_score);
const steamClass = (g) => STEAM_DESC[(g.steam_review_desc || "").toLowerCase()] || scoreClass(g.steam_review_pct);
function scoreHTML(v, empty = "—", cls = null) {
  return v == null || v === 0 ? empty : `<span class="score ${cls || scoreClass(v)}">${Math.round(v)}%</span>`;
}
const statusLabel = (s) => ({ completed: "Completed", dnf: "Will not finish" }[s] || "No status");
// Little icons on the game page's controls (drawn here, in the text colour).
const svgIcon = (d) => `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const STATUS_ICON = { "": "", completed: svgIcon('<path d="M5 12.5l4.5 4.5L19 7.5"/>'), dnf: svgIcon('<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>') };
const INTEREST_SHORT = { more: INTEREST_ICON.more() + "Keen", less: INTEREST_ICON.less() + "Meh", skip: INTEREST_ICON.skip() + "Skip" };
const ACTION_ICON = {
  first: svgIcon('<path d="M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8-4.3-4.1 5.9-.9z"/>'),
  more: svgIcon('<circle class="dot" cx="5" cy="12" r="1.7"/><circle class="dot" cx="12" cy="12" r="1.7"/><circle class="dot" cx="19" cy="12" r="1.7"/>'),
  fix: svgIcon('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>'),
  hide: svgIcon('<path d="M3 3l18 18M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 9 6 9 6a16 16 0 0 1-2.6 3.2M6.6 7.6A16 16 0 0 0 3 12s4 6 9 6a9 9 0 0 0 4.4-1.2M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
  art: svgIcon('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>'),
  refresh: svgIcon('<path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4"/>'),
};

// The right-click menu's icons: the game page's set plus a few more, same line style.
const MENU_ICON = {
  open: svgIcon('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle class="dot" cx="12" cy="7.8" r="1.2"/>'),
  play: svgIcon('<path d="M8 5.5v13l10.5-6.5z"/>'),
  completed: STATUS_ICON.completed,
  dnf: STATUS_ICON.dnf,
  more: INTEREST_ICON.more(),
  less: INTEREST_ICON.less(),
  skip: INTEREST_ICON.skip(),
  shelf: svgIcon('<path d="M4 20h16M6 16V5M10 16V7M14.5 16.2l-1.9-8.7M18 16V6"/>'),
  top: svgIcon('<path d="M5 4h14M12 20V9M7 13l5-5 5 5"/>'),
  bottom: svgIcon('<path d="M5 20h14M12 4v11M7 11l5 5 5-5"/>'),
  art: ACTION_ICON.art,
  fix: ACTION_ICON.fix,
  identify: svgIcon('<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.4-4.4"/>'),
  hide: ACTION_ICON.hide,
  unhide: svgIcon('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'),
  check: STATUS_ICON.completed,
};

const badgesHTML = (g) => g.platforms.map(platformBadge).join("") + interestMark(g);

function renderGrid(list) {
  return list.map((g) => `
    <button class="card ${g.status === "dnf" ? "is-dnf" : ""}" data-id="${g.id}">
      <div class="cover">
        ${coverHTML(g)}
        ${g.isHidden ? `<span class="stamp hidden-stamp">Hidden</span>` : ""}
        ${subPill(g)}
        ${g.isEA ? eaPill(g).replace('class="ea-pill"', `class="ea-pill corner${subPill(g) ? " below" : ""}"`) : ""}
        ${g.status === "completed" ? `<span class="stamp completed check" title="Completed" aria-label="Completed"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg></span>` : g.status === "dnf" ? `<span class="stamp dnf" title="Will not finish">WNF</span>` : ""}
        <div class="badges">${badgesHTML(g)}</div>
        ${instMark(g)}
      </div>
      ${meter(g)}
      <div class="card-title">${esc(g.title)}</div>
      <div class="card-meta">${esc(cardMeta(g))}</div>
    </button>`).join("");
}

const LIST_COLS = [
  { key: "title", label: "Game" },
  { key: null, label: "Platforms", cls: "hide-sm" },
  { key: "playtime", label: "Played", num: true },
  { key: "last_played", label: "Last played", cls: "hide-sm" },
  { key: "ttb_main", label: "To beat", num: true },
  { key: "progress", label: "Progress", cls: "hide-sm" },
  { key: "critic_score", label: "Critics", num: true, cls: "hide-sm" },
  { key: "steam_review_pct", label: "Steam", num: true, cls: "hide-sm" },
  { key: "release_date", label: "Released", num: true, cls: "hide-sm" },
  { key: null, label: "Status" },
];

function renderList(list) {
  const head = LIST_COLS.map((c) => `<th class="${c.num ? "num" : ""} ${c.cls || ""} ${c.key === state.prefs.sort ? "sorted" : ""}" ${c.key ? `data-sort="${c.key}"` : ""}>${c.label}${c.key === state.prefs.sort ? (state.prefs.dir === "asc" ? " ↑" : " ↓") : ""}</th>`).join("");
  const rows = list.map((g) => `
    <tr data-id="${g.id}">
      <td><div style="display:flex;gap:12px;align-items:center"><div class="thumb">${coverHTML(g)}</div><span class="t-title">${esc(g.title)}</span></div></td>
      <td class="hide-sm">${g.platforms.map((p) => esc(PLATFORM_SHORT[p] || p)).join(", ")}</td>
      <td class="num">${g.hoursUnknown ? `<span title="Launched, but the store doesn't say for how long">?</span>` : fmtHours(g.played_h)}</td>
      <td class="hide-sm">${g.last_played ? esc(relDate(g.last_played)) : "—"}</td>
      <td class="num">${g.isEndless ? "∞" : fmtHours(g.ttb)}</td>
      <td class="hide-sm">${g.isEndless ? `<span class="inf small" title="Open-ended">∞</span>` : spine(g, "mini-spine")}</td>
      <td class="num hide-sm">${scoreHTML(g.critic, "—", criticClass(g))}</td>
      <td class="num hide-sm">${scoreHTML(g.steam_review_pct, "—", steamClass(g))}</td>
      <td class="num hide-sm">${year(g.release_date) || "—"}</td>
      <td>${g.status ? `<span class="pill ${g.status}">${statusLabel(g.status)}</span>` : ""}</td>
    </tr>`).join("");
  return `<table class="list"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

function renderStats() {
  const gs = state.games.filter((g) => !g.isHidden);
  if (!gs.length) { $("#stats").innerHTML = ""; return; }
  const year = new Date().getFullYear();
  const yearStart = new Date(year, 0, 1).getTime() / 1000;
  const playedThisYear = gs.filter((g) => g.last_played && g.last_played >= yearStart).length;
  const extra = gs.filter((g) => g.isCatalog).length;
  $("#stats").innerHTML = `
    <div class="stat"><b>${(gs.length - extra).toLocaleString()}</b><span>games you own</span></div>
    ${extra ? `<div class="stat"><b>${extra.toLocaleString()}</b><span>more on ${["gamepass", "psplus"].filter(subscribed).map((x) => SUB_SHORT[x]).join(" and ")}</span></div>` : ""}
    <div class="stat"><b>${playedThisYear.toLocaleString()}</b><span>played in ${year}</span></div>
`;
}

/* ------------------------------------------------------------ Library filters: a bar of dropdowns */

// Each filter is a dropdown chip above the covers. Changes apply at once; a pink tag per
// active filter clears it in one click. In a menu, tick boxes to combine, or "only" for one.
const STATUS_NAMES = { "": "No status", completed: "Completed", dnf: "Will not finish" };
const PLAYED_NAMES = { played: "Played", never: "Never played" };
const SUBTAG_NAMES = { new: "Recently added", leaving: "Leaving soon" };
const INSTALLED_NAMES = { yes: "Installed", no: "Not installed" };
const FILTER_DEFAULTS = () => ({ hiddenPlatforms: [], status: ["", "completed", "dnf"], played: ["played", "never"], genres: [], types: null, interests: [...INTEREST_KEYS], subTags: [], installed: ["yes", "no"], shelves: [], length: [], reviews: null, players: [] });
const filterKeys = () => Object.keys(FILTER_DEFAULTS());  // (a function: INTEREST_KEYS comes later in the file)
// The Installed filter shows once Sunbeam has found an installed game (Windows, a launcher it knows).
const anyInstalled = () => state.games.some((g) => g.canPlay);
const shelfName = (id) => (state.shelves || []).find((s) => s.id === id)?.name || "";
const shelfById = (id) => (state.shelves || []).find((s) => s.id === id);
const isSmart = (id) => !!shelfById(id)?.rules;
// Smart shelves fill themselves: which games each one has right now, worked out once per task.
let smartCache = null;
function smartMembers() {
  if (smartCache) return smartCache;
  smartCache = new Map();
  for (const s of state.shelves || []) {
    if (!s.rules) continue;
    const test = filterTest(s.rules, s.rules.q, { manualShelves: true });
    for (const g of state.games) if (test(g)) (smartCache.get(g.id) || smartCache.set(g.id, []).get(g.id)).push(s.id);
  }
  queueMicrotask(() => { smartCache = null; });
  return smartCache;
}
// Every shelf a game is on: the ones you put it on, then the smart ones it fits.
const onShelves = (g) => [...g.shelves, ...(smartMembers().get(g.id) || [])];
const SPARK = `<svg class="spark" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l2.1 6.2 6.4 2.3-6.4 2.3L12 19.5l-2.1-6.2L3.5 11l6.4-2.3z"/></svg>`;
const shelfLabel = (id) => `${isSmart(id) ? SPARK : ""}${esc(shelfName(id))}`;
// Length: Main + Extras (what the progress bars use); open-ended games and games without data are their own group.
const LENGTH_NAMES = { u5: "Under 5 h", u10: "5 to 10 h", u20: "10 to 20 h", u40: "20 to 40 h", long: "40 h or more", none: "Open-ended or unknown" };
const LENGTH_STEPS = ["u5", "u10", "u20", "u40", "long"];
const LENGTH_EDGES = { u5: [0, 5], u10: [5, 10], u20: [10, 20], u40: [20, 40], long: [40, null] };
const lengthKey = (g) => g.isEndless || !g.ttb ? "none" : g.ttb < 5 ? "u5" : g.ttb < 10 ? "u10" : g.ttb < 20 ? "u20" : g.ttb < 40 ? "u40" : "long";
// Reviews: the score the covers show (Steam's, else OpenCritic's).
const REVIEW_STEPS = [90, 85, 80, 75, 60];
const reviewOf = (g) => g.steam_review_pct ?? g.oc_score ?? null;
// Players: from IGDB's game modes.
const PLAYER_NAMES = { single: "Single-player", coop: "Co-op", couch: "Couch co-op (split screen)", multi: "Online multiplayer" };
function playerKeys(g) {
  const m = g.modes || [], out = [];
  if (m.includes("Single player")) out.push("single");
  if (m.includes("Co-operative")) out.push("coop");
  if (m.includes("Split screen")) out.push("couch");
  if (m.some((x) => x === "Multiplayer" || x.startsWith("Massively") || x === "Battle Royale")) out.push("multi");
  return out;
}
function lengthSummary(sel) {
  if (!sel.length) return null;
  if (sel.length === 1) return LENGTH_NAMES[sel[0]];
  const steps = LENGTH_STEPS.filter((k) => sel.includes(k));
  if (steps.length === sel.length) {
    const i0 = LENGTH_STEPS.indexOf(steps[0]), i1 = LENGTH_STEPS.indexOf(steps.at(-1));
    if (i1 - i0 + 1 === steps.length) {
      if (i0 === 0) return `Under ${LENGTH_EDGES[steps.at(-1)][1]} h`;
      if (steps.at(-1) === "long") return `${LENGTH_EDGES[steps[0]][0]} h or more`;
      return `${LENGTH_EDGES[steps[0]][0]} to ${LENGTH_EDGES[steps.at(-1)][1]} h`;
    }
  }
  return `${sel.length} lengths`;
}
// Curator: what you told Curator about a game ("" = nothing).
const INTEREST_KEYS = ["more", "", "less", "skip"];
const INTEREST_FILTER_NAMES = { more: "Keen", "": "Default", less: "Meh", skip: "Skip" };
// Type: every game is one of these (a hidden game counts as Hidden, then VR, then early access).
const TYPE_NAMES = { regular: "Regular games", ea: "Early access", vr: "VR", hidden: "Hidden" };
const gameType = (g) => g.isHidden ? "hidden" : g.vr ? "vr" : g.isEA ? "ea" : "regular";
const defaultTypes = () => ["regular", ...(hideEA() ? [] : ["ea"]), ...(hideVR() ? [] : ["vr"])];
const typesSel = () => state.prefs.types || defaultTypes();
const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
state.openFilter = null;
state.genreQuery = "";

function filterFacts() {
  // Counts cover the games the Type filter lets through (hidden, VR… only when picked there).
  const types = new Set(typesSel());
  const visible = state.games.filter((g) => types.has(gameType(g)));
  const plat = {}, status = {}, played = {}, genres = {}, subs = { new: 0, leaving: 0 }, installed = { yes: 0, no: 0 }, shelves = {}, length = {}, reviews = {}, players = {};
  for (const g of visible) for (const p of g.platforms) plat[p] = (plat[p] || 0) + 1;
  for (const g of visible) {
    status[g.status || ""] = (status[g.status || ""] || 0) + 1;
    played[g.neverPlayed ? "never" : "played"] = (played[g.neverPlayed ? "never" : "played"] || 0) + 1;
    for (const x of g.genres || []) genres[x] = (genres[x] || 0) + 1;
    for (const t of Object.keys(subs)) if (subMatch(g, [t])) subs[t]++;
    installed[g.canPlay ? "yes" : "no"]++;
    for (const s of onShelves(g)) shelves[s] = (shelves[s] || 0) + 1;
    const lk = lengthKey(g); length[lk] = (length[lk] || 0) + 1;
    const r = reviewOf(g);
    if (r != null) for (const t of REVIEW_STEPS) if (r >= t) reviews[t] = (reviews[t] || 0) + 1;
    for (const k of playerKeys(g)) players[k] = (players[k] || 0) + 1;
  }
  const platforms = Object.keys(state.platforms).filter((p) => plat[p]);
  return { plat, platforms, status, played, genres, subs, installed, shelves, length, reviews, players, total: visible.length,
    types: state.games.reduce((n, g) => { const t = gameType(g); n[t] = (n[t] || 0) + 1; return n; }, {}),
    interests: visible.reduce((n, g) => { const k = g.interest || ""; n[k] = (n[k] || 0) + 1; return n; }, {}) };
}

// IGDB calls platformers "Platform", which reads like the Platform filter.
const genreLabel = (x) => (x === "Platform" ? "Platformer" : x);
const short = (p) => ({ "Epic Games Store": "Epic", "Ubisoft Connect": "Ubisoft", "Nintendo Switch": "Switch", "Nintendo Switch 2": "Switch 2" }[state.platforms[p]] || state.platforms[p] || p);

// What each filter is set to, in a few words (null = not filtering).
function filterSummaries(f) {
  const p = state.prefs;
  const shown = f.platforms.filter((x) => !p.hiddenPlatforms.includes(x));
  const plat = shown.length === f.platforms.length ? null
    : !shown.length ? "None"
    : shown.length <= 2 ? shown.map(short).join(", ")
    : f.platforms.length - shown.length <= 2 ? `All but ${f.platforms.filter((x) => !shown.includes(x)).map(short).join(", ")}`
    : `${shown.length} of ${f.platforms.length}`;
  const st = p.status.length === 3 ? null : !p.status.length ? "None" : p.status.map((x) => STATUS_NAMES[x]).join(", ");
  const pl = p.played.length === 2 ? null : !p.played.length ? "None" : PLAYED_NAMES[p.played[0]];
  const gen = !p.genres.length ? null : p.genres.length <= 2 ? p.genres.map(genreLabel).join(", ") : `${p.genres.length} genres`;
  const sel = typesSel();
  const type = sameSet(sel, defaultTypes()) ? null : !sel.length ? "None"
    : sel.length === 1 ? `${TYPE_NAMES[sel[0]]} only` : Object.keys(TYPE_NAMES).filter((x) => sel.includes(x)).map((x) => TYPE_NAMES[x]).join(", ");
  const more = p.subTags.map((t) => SUBTAG_NAMES[t]);
  const ins = p.interests;
  const cur = ins.length === INTEREST_KEYS.length ? null : !ins.length ? "None"
    : ins.length === 1 ? `${INTEREST_FILTER_NAMES[ins[0]]} only` : INTEREST_KEYS.filter((x) => ins.includes(x)).map((x) => INTEREST_FILTER_NAMES[x]).join(", ");
  const inst = !anyInstalled() || p.installed.length === 2 ? null : !p.installed.length ? "None" : INSTALLED_NAMES[p.installed[0]];
  const sh = p.shelves.filter((s) => shelfName(s));
  const shelves = !sh.length ? null : sh.length === 1 ? shelfName(sh[0]) : `${sh.length} shelves`;
  const len = lengthSummary(p.length);
  const rev = p.reviews != null ? `${p.reviews}%+` : null;
  const ply = !p.players.length ? null : p.players.length === 1 ? PLAYER_NAMES[p.players[0]].replace(" (split screen)", "") : `${p.players.length} kinds`;
  return { platform: plat, status: st, played: pl, genre: gen, type, curator: cur, more: more.length ? more.join(", ") : null, installed: inst,
    length: len, reviews: rev, players: ply, shelves };
}

function renderFilters() {
  const bar = $("#fbar");
  if (!bar) return;
  const f = filterFacts(), sum = filterSummaries(f), p = state.prefs;
  const chip = (id, label, empty = "Any") => `<button type="button" class="fchip ${sum[id] ? "on" : ""} ${state.openFilter === id ? "open" : ""}" data-fopen="${id}" aria-expanded="${state.openFilter === id}">
      ${label} <b>${esc(sum[id] || empty)}</b><i aria-hidden="true">▾</i></button>`;
  const any = Object.values(sum).some(Boolean);
  bar.innerHTML = `
    ${chip("platform", "Platform", "All")}${chip("status", "Status")}${chip("played", "Played")}${chip("genre", "Genre")}${chip("type", "Type", "Default")}${chip("curator", CURATOR)}${subsLabel() ? chip("more", subsLabel(), "Any") : ""}${anyInstalled() ? chip("installed", "Installed") : ""}${chip("length", "Length")}${chip("reviews", "Reviews")}${chip("players", "Players")}${chip("shelves", "Shelves")}
    ${smartButton(sum)}
    ${any ? `<button type="button" class="link fclear-all" data-fclear="all">Clear all</button>` : ""}
    ${state.openFilter ? `<div class="fpop ${state.openFilter === "genre" ? "wide" : ""} ${state.openFilter === "shelves" ? "shelves-pop" : ""}" role="dialog" aria-label="Filter">${filterMenu(state.openFilter, f)}</div>` : ""}`;
  const pop = $(".fpop", bar), anchor = $(`[data-fopen="${state.openFilter}"]`, bar);
  if (pop && anchor) {
    const left = Math.min(anchor.offsetLeft, bar.clientWidth - pop.offsetWidth);
    pop.style.left = Math.max(0, left) + "px";
    const q = $(".fpop-search", pop);
    if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }
}

function filterMenu(id, f) {
  const p = state.prefs;
  const row = (group, value, label, n, on) => `<div class="frow">
      <label><input type="checkbox" data-fgroup="${group}" value="${esc(value)}" ${on ? "checked" : ""}><span>${esc(label)}</span></label>
      <button type="button" class="fonly" data-fonly="${group}" data-v="${esc(value)}" aria-label="Only ${esc(label)}">only</button><em>${n ?? ""}</em></div>`;
  const head = (title, group) => `<div class="fpop-head"><span>${title}</span><span><button type="button" class="link" data-fall="${group}">All</button><button type="button" class="link" data-fnone="${group}">None</button></span></div>`;
  if (id === "platform") return head("Platforms", "platform") + (f.platforms.length
    ? f.platforms.map((x) => row("platform", x, state.platforms[x], f.plat[x], !p.hiddenPlatforms.includes(x))).join("")
    : `<p class="source-note">Sync a library to see platforms.</p>`);
  if (id === "status") return head("Status", "status") + Object.entries(STATUS_NAMES).map(([v, l]) => row("status", v, l, f.status[v] || 0, p.status.includes(v))).join("");
  if (id === "played") return head("Played", "played") + Object.entries(PLAYED_NAMES).map(([v, l]) => row("played", v, l, f.played[v] || 0, p.played.includes(v))).join("")
    + `<p class="fpop-note">Never played: no time and no launch date on any store.</p>`;
  if (id === "genre") {
    const q = state.genreQuery.trim().toLowerCase();
    const all = Object.keys(f.genres).sort((a, b) => f.genres[b] - f.genres[a] || a.localeCompare(b));
    const list = all.filter((x) => !q || genreLabel(x).toLowerCase().includes(q));
    return `<input class="fpop-search" type="search" placeholder="Find a genre" value="${esc(state.genreQuery)}" autocomplete="off">
      <div class="fpop-head"><span>${p.genres.length > 1 ? "Games in any of the ticked genres" : "Genres"}</span><span><button type="button" class="link" data-fnone="genre">Clear</button></span></div>
      <div class="fcols">${list.length ? list.map((x) => row("genre", x, genreLabel(x), f.genres[x], p.genres.includes(x))).join("")
        : `<p class="source-note">${all.length ? "No genre matches." : "Genres appear once game details are in."}</p>`}</div>`;
  }
  if (id === "curator") return head(CURATOR, "curator") + INTEREST_KEYS.map((v) => row("curator", v, INTEREST_FILTER_NAMES[v], f.interests[v] || 0, p.interests.includes(v))).join("")
    + `<p class="fpop-note">What you told ${CURATOR} on a game's page.</p>`;
  if (id === "type") {
    const sel = typesSel(), off = ["vr", "ea"].filter((x) => !defaultTypes().includes(x)).map((x) => TYPE_NAMES[x]);
    return head("Type", "type") + Object.entries(TYPE_NAMES).map(([v, l]) => row("type", v, l, f.types[v] || 0, sel.includes(v))).join("")
      + `<p class="fpop-note">${off.length ? `${off.join(" and ")} games are hidden by default (Settings › Hidden game types). ` : ""}Hidden: games you hid, and betas, demos and tools.</p>`;
  }
  if (id === "length") return head("Time to beat", "length") + Object.entries(LENGTH_NAMES).map(([v, l]) => row("length", v, l, f.length[v] || 0, p.length.includes(v))).join("")
    + `<p class="fpop-note">Main story plus extras, the same time the progress bars use.</p>`;
  if (id === "reviews") return `<div class="fpop-head"><span>Review score</span></div>`
    + [null, ...REVIEW_STEPS].map((t) => `<div class="frow"><label><input type="radio" name="freviews" data-freviews="${t ?? ""}" ${p.reviews === t ? "checked" : ""}><span>${t == null ? "Any score" : `${t}% or more`}</span></label><em>${t == null ? "" : f.reviews[t] || 0}</em></div>`).join("")
    + `<p class="fpop-note">The score on the covers: Steam's, else OpenCritic's. Games without one are left out once you pick a score.</p>`;
  if (id === "players") return head("Players", "players") + Object.entries(PLAYER_NAMES).map(([v, l]) => row("players", v, l, f.players[v] || 0, p.players.includes(v))).join("")
    + `<p class="fpop-note">Games with any of the ticked ways to play, from IGDB.</p>`;
  if (id === "installed") return head("On this PC", "installed") + Object.entries(INSTALLED_NAMES).map(([v, l]) => row("installed", v, l, f.installed[v] || 0, p.installed.includes(v))).join("")
    + `<p class="fpop-note">Games installed through Steam, Epic, GOG, Ubisoft Connect, Battle.net or the Xbox app on this PC.</p>`;
  if (id === "shelves") {
    const list = state.shelves || [];
    const rows = list.map((s) => `<div class="frow shelf-frow">
      <label ${s.rules ? `title="A smart shelf: it fills itself from its rules"` : ""}><input type="checkbox" data-fgroup="shelves" value="${s.id}" ${p.shelves.includes(s.id) ? "checked" : ""}><span>${shelfLabel(s.id)}</span></label>
      <span class="shelf-tools"><button type="button" class="fonly" data-fonly="shelves" data-v="${s.id}" aria-label="Only ${esc(s.name)}">only</button>${s.rules ? `<button type="button" class="fonly" data-smart-edit="${s.id}">rules</button>` : ""}<button type="button" class="fonly" data-shelf-rename="${s.id}">rename</button><button type="button" class="fonly" data-shelf-delete="${s.id}">delete</button></span><em>${f.shelves[s.id] || 0}</em></div>`).join("");
    return `<div class="fpop-head"><span>${p.shelves.length > 1 ? "Games on any of the ticked shelves" : "Your shelves"}</span>${list.length ? `<span><button type="button" class="link" data-fnone="shelves">Clear</button></span>` : ""}</div>
      ${rows || `<p class="fpop-note">Shelves are lists you make: "Couch co-op", "Comfort games"… Add games from a game's page or by right-clicking a cover.</p>`}
      <p class="fpop-note">${SPARK} Smart shelves fill themselves: set the filters, then choose <b>Save as smart shelf</b>.</p>
      <form class="shelf-new" data-shelf-form><input type="text" name="name" placeholder="New shelf" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore><button type="submit" class="btn small">Add</button></form>`;
  }
  // (internally still "more": XGP / PS+ catalog changes)
  if (id === "more") return `<div class="fpop-head"><span>${subsLabel()} catalog</span></div>`
    + Object.entries(SUBTAG_NAMES).map(([v, l]) => row("subtags", v, l, f.subs[v], p.subTags.includes(v))).join("");
  return "";
}

function setFilterGroup(group, values) {
  const p = state.prefs, f = filterFacts();
  if (group === "platform") p.hiddenPlatforms = f.platforms.filter((x) => !values.includes(x));
  if (group === "status") p.status = values;
  if (group === "played") p.played = values;
  if (group === "genre") p.genres = values;
  if (group === "subtags") p.subTags = values;
  if (group === "type") p.types = sameSet(values, defaultTypes()) ? null : values;
  if (group === "curator") p.interests = values;
  if (group === "installed") p.installed = values;
  if (group === "shelves") p.shelves = values.map(Number);
  if (group === "length") p.length = values;
  if (group === "players") p.players = values;
  savePrefs(); renderFilters(); render();
}
function groupValues(group) {
  const p = state.prefs, f = filterFacts();
  return { platform: f.platforms.filter((x) => !p.hiddenPlatforms.includes(x)), status: p.status, played: p.played,
    genre: p.genres, subtags: p.subTags, type: typesSel(), curator: p.interests, installed: p.installed, shelves: p.shelves.map(String), length: p.length, players: p.players }[group];
}
function allValues(group) {
  const f = filterFacts();
  return { platform: f.platforms, status: Object.keys(STATUS_NAMES), played: Object.keys(PLAYED_NAMES), genre: [], subtags: [], type: Object.keys(TYPE_NAMES), curator: [...INTEREST_KEYS], installed: ["yes", "no"], shelves: [], length: [], players: [] }[group];
}
function clearFilter(id) {
  const d = FILTER_DEFAULTS(), p = state.prefs;
  if (id === "all") { Object.assign(p, d); state.q = ""; $("#search").value = ""; renderWishlist(); }
  if (id === "platform") p.hiddenPlatforms = d.hiddenPlatforms;
  if (id === "status") p.status = d.status;
  if (id === "played") p.played = d.played;
  if (id === "genre") p.genres = d.genres;
  if (id === "type") p.types = d.types;
  if (id === "curator") p.interests = d.interests;
  if (id === "more") p.subTags = d.subTags;
  if (id === "installed") p.installed = d.installed;
  if (id === "shelves") p.shelves = d.shelves;
  if (id === "length") p.length = d.length;
  if (id === "reviews") p.reviews = d.reviews;
  if (id === "players") p.players = d.players;
  savePrefs(); renderFilters(); render();
}

function bindFilters() {
  const bar = $("#fbar");
  bar.addEventListener("click", (e) => {
    const t = e.target;
    const open = t.closest("[data-fopen]");
    if (open) {
      state.openFilter = state.openFilter === open.dataset.fopen ? null : open.dataset.fopen;
      state.genreQuery = "";
      renderFilters(); return;
    }
    const clr = t.closest("[data-fclear]");
    if (clr) { state.openFilter = null; clearFilter(clr.dataset.fclear); return; }
    const only = t.closest("[data-fonly]");
    if (only) { setFilterGroup(only.dataset.fonly, [only.dataset.v]); return; }
    const all = t.closest("[data-fall]");
    if (all) { setFilterGroup(all.dataset.fall, allValues(all.dataset.fall)); return; }
    const none = t.closest("[data-fnone]");
    if (none) { setFilterGroup(none.dataset.fnone, []); return; }
    const ren = t.closest("[data-shelf-rename]");
    if (ren) { renameShelfInline(ren.closest(".frow"), Number(ren.dataset.shelfRename)); return; }
    const del = t.closest("[data-shelf-delete]");
    if (del) { deleteShelf(Number(del.dataset.shelfDelete)); return; }
    const sedit = t.closest("[data-smart-edit]");
    if (sedit) { editSmartShelf(Number(sedit.dataset.smartEdit)); return; }
    const smart = t.closest("[data-smart]");
    if (smart) { state.openFilter = null; onSmartButton(smart.dataset.smart); return; }
  });
  bar.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if (form.matches("[data-shelf-form]")) {
      await newShelf(form.elements.name.value);  // (not picked: it's empty until you add games)
    }
    if (form.matches("[data-shelf-rename-form]")) {
      await renameShelf(Number(form.dataset.shelfRenameForm), form.elements.name.value);
    }
  });
  bar.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.freviews != null) {
      state.prefs.reviews = t.dataset.freviews === "" ? null : Number(t.dataset.freviews);
      savePrefs(); renderFilters(); render(); return;
    }
    if (t.dataset.fgroup) {
      const g = t.dataset.fgroup;
      const vals = new Set(groupValues(g));
      if (t.checked) vals.add(t.value); else vals.delete(t.value);
      setFilterGroup(g, [...vals]);
    }
  });
  bar.addEventListener("input", (e) => {
    if (e.target.classList.contains("fpop-search")) { state.genreQuery = e.target.value; renderFilters(); }
  });
  document.addEventListener("click", (e) => {
    // composedPath: the clicked element may already have been redrawn (and detached) by then
    if (state.openFilter && !e.composedPath().includes(bar)) { state.openFilter = null; renderFilters(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.openFilter) { e.stopPropagation(); state.openFilter = null; renderFilters(); }
  }, true);
}

function render() {
  document.documentElement.style.setProperty("--cover", state.prefs.size + "px");
  $("#sort-key").value = state.prefs.sort;
  $("#sort-dir").classList.toggle("asc", state.prefs.dir === "asc");
  $$(".view-toggle button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.prefs.view));
  $("#size").value = state.prefs.size;

  const lib = $("#library");
  const empty = $("#empty");
  if (!state.games.length) {
    lib.innerHTML = "";
    $("#result-count").innerHTML = "";
    renderEmpty();
    return;
  }
  const list = visibleGames();
  // Only when filters or a search narrow the list (the totals are in the stats above): "836 of 1,526",
  // out of what the library shows with no filters.
  const narrowed = state.q.trim() || Object.values(filterSummaries(filterFacts())).some(Boolean);
  const base = state.games.filter((g) => defaultTypes().includes(gameType(g))).length;
  $("#result-count").innerHTML = !narrowed ? ""
    : list.length <= base ? `<b>${list.length.toLocaleString()}</b> of ${base.toLocaleString()}` : `<b>${list.length.toLocaleString()}</b> games`;
  if (!list.length) {
    lib.innerHTML = "";
    empty.hidden = false;
    empty.innerHTML = `<h2>Nothing matches</h2><p>No games fit these filters${state.q ? ` and the search “${esc(state.q)}”` : ""}.</p><button class="btn quiet" id="empty-reset">Reset filters</button>`;
    return;
  }
  empty.hidden = true;
  lib.className = state.prefs.view === "list" ? "" : "grid";
  setHTML(lib, state.prefs.view === "list" ? renderList(list) : renderGrid(list));
}

function renderEmpty() {
  const s = state.settings?.settings || {};
  const metaOk = s.igdb_client_id && s.igdb_client_secret?.set;
  const libOk = (s.steam_api_key?.set && s.steam_id) || s.psn_npsso?.set;
  const empty = $("#empty");
  empty.hidden = false;
  empty.innerHTML = `
    <h2>Your shelf is empty</h2>
    <p>Connect your stores once and ${APP} brings every game together with its art, details and your playtime.</p>
    <ol class="steps">
      <li class="${metaOk ? "ok" : ""}"><div><b>Connect game info (IGDB)</b><span>Free. Supplies descriptions, genres, covers and time to beat.</span></div></li>
      <li class="${libOk ? "ok" : ""}"><div><b>Connect Steam and PlayStation</b><span>Imports what you own and how long you've played.</span></div></li>
      <li><div><b>Sync</b><span>The first sync takes a few minutes; covers appear as it goes.</span></div></li>
    </ol>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      ${metaOk && libOk ? `<button class="btn" id="empty-sync">Sync now</button>` : `<button class="btn" id="empty-settings">Open settings</button>`}
      <button class="btn ghost" id="empty-add">Add a game by hand</button>
    </div>`;
}

function renderAll() {
  renderStats();
  renderUpNext();
  renderWishlist();
  renderFilters();
  render();
  if (state.page === "year") renderYear();
}

/* ------------------------------------------------------------ detail panel */

const byId = (id) => state.games.find((g) => g.id === id) || state.wishGames.find((g) => g.id === id);

async function openGame(id) {
  state.openId = id;
  const wrap = $("#detail-wrap");
  const cached = byId(id);
  if (cached) { renderDetail(cached); }
  wrap.hidden = false;
  document.body.style.overflow = "hidden";
  try {
    const g = derive(await api(`/api/games/${id}`));
    if (state.openId === id) renderDetail(g);
  } catch (e) { toast(e.message, true); }
}

function closeGame() {
  state.openId = null;
  $("#detail-wrap").hidden = true;
  document.body.style.overflow = "";
}

function fact(label, value, small = "", extra = "") {
  return `<div class="fact"><span>${label}</span><b>${value}</b>${small ? `<small>${small}</small>` : ""}${extra}</div>`;
}

// Day-by-day play (from Nintendo's Play Activity).
function playHistory(g) {
  const days = g.play_days || [];
  if (!days.length) return "";
  const counted = Math.max(...(g.entries || []).map((e) => e.excluded ? 0 : e.days_played || 0), days.length);
  const fmtDay = (d) => new Date(d + "T12:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  const fmtMin = (m) => m <= 5 ? "A few min" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
  return `<section class="section"><h2>Play history</h2>
    <p class="source-note">Played on ${counted} day${counted === 1 ? "" : "s"}. Most recent sessions from Nintendo's Play History:</p>
    <div class="play-days">${days.slice(0, 12).map((d) => `<div><span>${esc(fmtDay(d.day))}</span><b>${fmtMin(d.minutes)}</b></div>`).join("")}</div>
  </section>`;
}

// Slide between Main and 100% to say how you play this one.
function ttbSlider(g) {
  const lo = g.ttb_main || g.ttb_extra, hi = g.ttb_complete;
  if (g.isEndless || !lo || !hi || hi <= lo * 1.05) return "";
  const pos = (h) => ((Math.min(Math.max(h, lo), hi) - lo) / (hi - lo) * 100).toFixed(1);
  const step = hi - lo > 40 ? 1 : 0.5;
  const min = step === 1 ? Math.floor(lo) : Math.floor(lo * 2) / 2;
  const val = Math.min(Math.max(g.ttb || lo, lo), hi);
  return `<div class="ttb-slider">
    <input type="range" id="ttb-range" min="${min}" max="${hi}" step="${step}" value="${val}" aria-label="Time to beat for how you play">
    <div class="ttb-ticks">
      <i style="left:0%"></i>${g.ttb_extra && g.ttb_main && g.ttb_extra > lo && g.ttb_extra < hi ? `<i style="left:${pos(g.ttb_extra)}%" title="Main + Extras"></i>` : ""}<i style="left:100%"></i>
    </div>
    <div class="ttb-slider-row"><span>How you play it: <strong id="ttb-val">${fmtHours(val)}</strong></span>
      ${g.ttb_custom ? `<button class="link" data-act="ttb-reset">Back to automatic</button>` : ""}</div>
  </div>`;
}

function renderDetail(g) {
  const heroImg = img(g.heroes, "", "");
  const logo = g.logo_url ? `<img class="hero-logo" src="${esc(g.logo_url)}" alt="" data-fallbacks="">` : "";
  const devs = (g.developers || []).slice(0, 2).join(", ");
  const entries = (g.entries || []).filter((e) => e.ownership !== "catalog" && e.ownership !== "wishlist");
  const storeLinks = g.store_links || [];
  const storeLink = (s) => { const l = storeLinks.find((x) => x.s === s); return l ? ` <a href="${esc(l.url)}" target="_blank" rel="noopener">Store page</a>` : ""; };
  const normT = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const showNames = entries.length > 1 || entries.some((e) => normT(e.name) !== normT(g.title));
  const versionCell = (e, extra = "") => {
    const store = e.platform === "steam" && /^\d+$/.test(e.external_id || "")
      ? ` <a class="copy-link" href="https://store.steampowered.com/app/${esc(e.external_id)}" target="_blank" rel="noopener">Store page</a>` : "";
    const inst = (g.installed || []).some((x) => x.platform === e.platform) ? `<span class="copy-inst" title="Installed on this PC"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>Installed</span>` : "";
    return `<td><div class="copy-plat">${esc(state.platforms[e.platform] || e.platform)}${extra}${inst}</div>${showNames && e.name ? `<div class="copy-name">${esc(e.name)}${store}</div>` : ""}</td>`;
  };

  const notOut = g.rel && g.rel.state !== "out";  // a wishlist game that isn't released yet
  const ext = (url, text) => url ? `<a class="fact-link" href="${esc(url)}" target="_blank" rel="noopener">${text}</a>` : text;
  const steamLabel = `${ext(g.steam_appid ? `https://store.steampowered.com/app/${g.steam_appid}` : null, "Steam")} user reviews`;
  const ttbLink = g.ttb_source === "HowLongToBeat"
    ? ext(g.hltb_id ? `https://howlongtobeat.com/game/${g.hltb_id}` : `https://howlongtobeat.com/?q=${encodeURIComponent(g.title)}`, "HowLongToBeat")
    : g.ttb_source === "IGDB" ? ext(g.igdb_url, "IGDB") : "";
  const reviews = g.steam_review_pct != null
    ? fact(steamLabel, scoreHTML(g.steam_review_pct, "—", steamClass(g)), `${esc(g.steam_review_desc || "")}, ${g.steam_review_count.toLocaleString()} reviews`)
    : fact(steamLabel, "—", g.steam_appid ? "No reviews yet" : g.steam_checked_at ? "Not found on Steam" : "Not checked yet. Use Refresh assets.");
  const ttb = g.ttb_main || g.ttb_extra || g.ttb_complete
    ? `<div class="fact"><span>Time to beat${ttbLink ? ` · ${ttbLink}` : ""}</span>
        <div class="ttb-row">
          <div><b>${fmtHours(g.ttb_main)}</b><small>Main</small></div>
          <div><b>${fmtHours(g.ttb_extra)}</b><small>+ Extras</small></div>
          <div><b>${fmtHours(g.ttb_complete)}</b><small>100%</small></div>
        </div>${ttbSlider(g)}</div>`
    : fact("Time to beat", "—", notOut ? "Not out yet" : "No data yet");

  const progressText = g.status === "completed" ? "Completed" : g.isEndless ? "Open-ended" : g.progress == null ? "—" : `${Math.round(g.progress * 100)}%`;
  const progressSmall = g.status === "completed" ? (g.status_at ? `Marked ${relDate(g.status_at).toLowerCase()}` : "")
    : g.isEndless ? "No finish line, play it whenever"
    : g.status === "dnf" ? "Set aside"
    : g.remaining ? `About ${fmtHours(g.remaining)} to the credits` : g.progress === 1 ? "Credits could be close" : "";
  const endlessToggle = g.status === "completed" ? "" : `<button class="link endless-toggle" data-act="endless" data-v="${g.isEndless ? 0 : 1}">${g.isEndless ? "This game has an ending" : "This game doesn't really end"}</button>${g.endless != null ? ` <button class="link endless-toggle" data-act="endless" data-v="auto">Reset</button>` : ""}`;

  const meta = [
    ["Genres", g.genres], ["Themes", g.themes], ["Modes", g.modes], ["Perspective", g.perspectives],
    ["Developer", g.developers], ["Publisher", g.publishers], ["Series", g.franchises],
    ["Platforms", g.igdb_platforms],
  ].filter(([, v]) => v && v.length);

  $("#detail").innerHTML = `
    <div class="hero">
      ${heroImg}
      <button class="btn icon hero-close" data-close aria-label="Close">✕</button>
      <div class="hero-body">
        <div class="hero-cover">${coverHTML(g)}</div>
        <div class="hero-text">
          ${logo}
          <h1 class="hero-title ${logo ? "sr" : ""}" id="detail-title">${esc(g.title)}</h1>
          <div class="hero-sub">
            ${g.isWishOnly ? (g.wish?.stores || []).map((x) => wishBadge(x.store)).join("")
              : g.platforms.map(platformTag).join("")}
            ${g.isEA ? `<span class="badge ea-badge">Early access</span>` : ""}
            ${year(g.release_date) ? `<span>${year(g.release_date)}</span>` : ""}
            ${devs ? `<span>${esc(devs)}</span>` : ""}
          </div>
          ${playBlock(g)}
        </div>
      </div>
    </div>
    <div class="detail-body">
      ${g.isHidden ? `<p class="notice">${g.hidden === 1 ? "You hid this game." : "Hidden automatically: it looks like a playtest, beta, demo or tool rather than a game."} ${g.isWishOnly ? "It doesn't appear on the Wishlist page." : "It's left out of your library, stats and ${CURATOR} unless you tick Hidden in the Library's Type filter."}</p>` : ""}
      <div class="actions">
        ${g.isWishOnly ? "" : `<div class="status-picker" role="group" aria-label="Status">
          <span>Status</span>${["completed", "dnf"].map((st) => `<button data-s="${st}" class="${g.status === st ? "on" : ""}" aria-pressed="${g.status === st}" title="${g.status === st ? "Click again to clear" : ""}">${STATUS_ICON[st]}${statusLabel(st)}</button>`).join("")}
        </div>
        <div class="interest-picker ${g.status || g.isEndless ? "dim" : ""}" role="group" aria-label="${CURATOR}"
          ${g.status || g.isEndless ? `title="${g.status ? "Finished or set-aside games aren't suggested" : "Open-ended games aren't suggested"}"` : ""}>
          <span>${CURATOR}</span>
          ${Object.entries(INTEREST_SHORT).map(([k, label]) => `<button class="${g.interest === k ? "on" : ""}" data-act="interest" data-v="${k}" aria-pressed="${g.interest === k}" title="${INTEREST[k]}">${label}</button>`).join("")}
        </div>`}
        <span class="spacer"></span>
        <div class="more-menu">
          <button class="btn icon ghost" data-act="menu" aria-label="More actions" aria-expanded="false" title="More actions">${ACTION_ICON.more}</button>
          <div class="more-pop" hidden>
            <button data-act="fix">${ACTION_ICON.fix}${g.match_state === "none" ? "Identify game" : "Fix match"}</button>
            <button data-act="hide" data-v="${g.isHidden ? 0 : 1}">${ACTION_ICON.hide}${g.isHidden ? "Unhide" : "Hide"}</button>
            <button data-act="art">${ACTION_ICON.art}Change art</button>
            <button data-act="refresh" title="Re-fetch this game's details, art, reviews and time to beat">${ACTION_ICON.refresh}Refresh assets</button>
          </div>
        </div>
      </div>
      <div class="detail-notes">
        ${shelfRow(g)}
        ${(g.subs || []).length && !g.wish ? `<div class="sub-line">${g.subs.map((x) =>
          `<span title="${esc(SUB_NAME[x.s])}">${x.leaving ? "Leaving" : "Included with"} ${SUB_SHORT[x.s]}${x.leaving ? " soon" : ""}${x.new ? `, added ${relDate(x.since).toLowerCase()}` : ""}${storeLink(x.s)}</span>`).join("")}</div>` : ""}
        ${g.isEA ? `<p class="sub-note">In early access${g.eaFull ? `, full release ${esc(fmtDate(g.eaFull))}` : ""}: time to beat and progress may still change, and it won't be marked completed automatically.</p>` : ""}
        ${g.status === "completed" && g.status_auto ? `<p class="auto-note">✓ Marked completed automatically: ${esc(g.status_auto)}${g.status_at ? `, ${esc(fmtDate(g.status_at))}` : ""}. Not right? Pick another status above.</p>` : ""}
      </div>
      ${wishBox(g)}
      <div class="facts">
        ${g.isWishOnly ? "" : fact("You've played", g.hoursUnknown ? "?" : fmtHours(g.played_h, { empty: "0 h" }), g.last_played ? `Last played ${esc(relDate(g.last_played).toLowerCase())}${g.hoursUnknown ? ". The store doesn't say for how long" : ""}` : g.playtime_min ? "Date unknown" : "Not played yet")}
        ${g.isWishOnly ? "" : fact("Progress", progressText, progressSmall, `<div class="bar">${meter(g)}</div><div class="endless-row">${endlessToggle}</div>`)}
        ${g.isWishOnly ? "" : ttb}
        ${g.oc_score != null
          ? fact(`${ext(g.oc_url, "OpenCritic")} score${g.oc_tier ? ` <i class="tier tier-${esc(g.oc_tier.toLowerCase())}">${esc(g.oc_tier)}</i>` : ""}`, scoreHTML(g.oc_score, "—", criticClass(g)),
              `${g.oc_recommend != null ? `${Math.round(g.oc_recommend)}% of critics recommend it` : "Top critic average"}${g.oc_count ? `, ${g.oc_count} reviews` : ""}`)
          : fact("OpenCritic score", "—", notOut ? "Not out yet" : "No score yet")}
        ${reviews}
        ${g.isWishOnly ? ttb : ""}${/* wishlist games: the reviews first, then time to beat on the right */ ""}
      </div>

      ${entries.length ? `<section class="section">
        <h2>Your copies</h2>
        ${entries.length > 1 && new Set(entries.map((e) => normT(e.name))).size > 1 ? `<p class="source-note">${entries.length} versions combined into one game. Each one's own name, playtime and dates are below.</p>` : ""}
        <div class="copies-wrap"><table class="copies">
          <thead><tr><th>Platform</th><th>Played</th><th title="Hours that shouldn't count: someone else playing on your account, or earlier playthroughs you'd rather leave out (to show only your latest or longest one)">Don't count</th><th>Last played</th><th>First played</th><th title="Untick to leave this copy out of the game's playtime">Counts</th><th></th></tr></thead>
          <tbody>${entries.map((e) => {
            const counts = `<td><input type="checkbox" data-field="counts" ${e.excluded ? "" : "checked"} aria-label="Count this copy"></td>`;
            if (e.manual) return `
            <tr data-entry="${e.id}" class="${e.excluded ? "excluded" : ""}">
              ${versionCell(e, ` <span class="source-note">${String(e.external_id || "").startsWith("nsw:") ? "from Play History" : "added by hand"}</span>`)}
              <td><input type="number" min="0" step="0.5" value="${Math.round(hours(e.playtime_min) * 10) / 10}" data-field="hours" aria-label="Hours played"> h</td>
              <td class="source-note">All yours</td>
              <td><input type="date" value="${e.last_played ? new Date(e.last_played * 1000).toISOString().slice(0, 10) : ""}" data-field="last_played" aria-label="Last played"></td>
              <td>${fmtDate(e.first_played)}</td>
              ${counts}
              <td><button class="btn danger small" data-act="remove-entry">Remove</button></td>
            </tr>`;
            const mine = Math.max(e.playtime_min - (e.adjust_min || 0), 0);
            return `
            <tr data-entry="${e.id}" class="${e.excluded ? "excluded" : ""}">
              ${versionCell(e, e.ownership === "ps_plus" ? ` <span class="source-note">via PS+</span>` : "")}
              <td>${fmtHours(hours(e.playtime_min), { empty: "0 h" })}</td>
              <td><input type="number" min="0" step="0.5" value="${Math.round(hours(e.adjust_min || 0) * 10) / 10}" data-field="not_mine_hours" aria-label="Hours that shouldn't count"> h${e.adjust_min ? ` <span class="source-note">${fmtHours(hours(mine), { empty: "0 h" })} counted</span>` : ""}</td>
              <td>${fmtDate(e.last_played)}</td>
              <td>${fmtDate(e.first_played)}</td>
              ${counts}
              <td></td>
            </tr>`;
          }).join("")}</tbody>
        </table>
      </section>` : ""}

      ${(g.screenshots || []).length ? `<section class="section"><h2>Screenshots</h2><div class="shots">${g.screenshots.map((s, i) => `<img data-shot="${i}" src="${esc(s)}" alt="" loading="lazy" data-fallbacks="">`).join("")}</div></section>` : ""}


      ${g.summary ? `<section class="section"><h2>About</h2><p class="summary clamp" id="summary">${esc(g.summary)}${g.storyline ? "\n\n" + esc(g.storyline) : ""}</p><button class="link" id="more">Read more</button></section>` : ""}

      ${playHistory(g)}

      ${meta.length ? `<section class="section"><h2>Details</h2><dl class="meta-grid">
        ${meta.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v.join(", "))}</dd></div>`).join("")}
        ${g.release_date ? `<div><dt>Released</dt><dd>${fmtDate(g.release_date)}</dd></div>` : ""}
        ${g.igdb_url ? `<div><dt>More</dt><dd><a href="${esc(g.igdb_url)}" target="_blank" rel="noopener">IGDB page</a>${g.steam_appid ? ` and <a href="https://store.steampowered.com/app/${g.steam_appid}" target="_blank" rel="noopener">Steam store page</a>` : ""}</dd></div>` : ""}
      </dl></section>` : ""}

      ${similarSection(g)}
    </div>`;

  const more = $("#more");
  if (more) {
    const s = $("#summary");
    requestAnimationFrame(() => { if (s.scrollHeight <= s.clientHeight + 2) more.remove(); });
    more.onclick = () => { s.classList.toggle("clamp"); more.textContent = s.classList.contains("clamp") ? "Read more" : "Show less"; };
  }
  $("#detail").focus?.();
}

function upsertLocal(g) {
  derive(g);
  const into = g.isWishOnly ? state.wishGames : state.games;
  const other = g.isWishOnly ? state.games : state.wishGames;
  const j = other.findIndex((x) => x.id === g.id);
  if (j >= 0) other.splice(j, 1);
  const i = into.findIndex((x) => x.id === g.id);
  if (i >= 0) into[i] = { ...into[i], ...g };
  else into.push(g);
  renderAll();
  return g;
}

async function setHidden(id, v, { undo = true } = {}) {
  const g0 = byId(id), prev = g0 ? (g0.hidden ?? null) : null;
  try {
    await patchGame(id, { hidden: v });
    const wishOnly = byId(id)?.isWishOnly;
    toast(v ? (wishOnly ? "Hidden from your wishlist here" : "Hidden. Pick Hidden in the Type filter to find it.")
      : (wishOnly ? "Back on your wishlist" : "Back in your library"), false,
      undo && g0 ? () => undoPatch(id, { hidden: prev }, "Put back") : null);
    return true;
  } catch (err) { toast(err.message, true); return false; }
}

async function setTtb(id, hours) {
  try {
    const g = await api(`/api/games/${id}`, { method: "PATCH", body: { ttb_custom: hours } });
    upsertLocal(g);
    if (state.openId === id) renderDetail(derive(g));
    toast(hours == null ? "Time to beat is automatic again" : `Time to beat set to about ${fmtHours(hours)}`);
  } catch (err) { toast(err.message, true); }
}

async function setStatus(id, status) {
  const g0 = byId(id);
  if (status && g0 && g0.status === status) status = "";  // picking the lit one again clears it, as with Curator marks
  const prev = g0 && { status: g0.status || "", status_at: g0.status_at ?? null, status_manual: g0.status_manual ?? null, status_auto: g0.status_auto ?? null };
  try {
    await patchGame(id, { status });
    toast(status === "completed" ? "Marked completed" : status === "dnf" ? "Marked as will not finish" : "Status cleared", false,
      prev ? () => undoPatch(id, { restore_status: prev }, "Status put back") : null);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ Play, Install and shelves */

const PLAY_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l13-7.5z"/></svg>`;
// The ▶ on the cover of a game that's installed on this PC (library, Curator, Your year…).
const instMark = (g) => g.canPlay ? `<span class="inst-mark" title="Installed on this PC" aria-label="Installed">${PLAY_SVG}</span>` : "";
const DL_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-5-5 5 5 5-5M5 20h14"/></svg>`;
const PC_STORES = ["steam", "epic", "gog", "xbox", "ubisoft", "battlenet"];

// The copy Play starts by default: the installed one you played last (then the most played).
function playOrder(g) {
  const can = (g.installed || []).filter((x) => x.can_play).map((x) => x.platform);
  const score = (p) => {
    const es = (g.entries || []).filter((e) => e.platform === p);
    return [Math.max(0, ...es.map((e) => e.last_played || 0)), Math.max(0, ...es.map((e) => e.playtime_min || 0))];
  };
  return [...new Set(can)].sort((a, b) => { const [la, pa] = score(a), [lb, pb] = score(b); return lb - la || pb - pa; });
}
// Copies you have on a PC store but haven't installed (XGP games count when you're subscribed).
function installOrder(g) {
  const have = new Set((g.installed || []).map((x) => x.platform));
  const out = [];
  for (const e of g.entries || []) {
    if (!PC_STORES.includes(e.platform) || have.has(e.platform) || out.includes(e.platform)) continue;
    if (e.ownership === "wishlist") continue;
    if (e.platform === "xbox" && !String(e.external_id || "").startsWith("xgp:")) continue;  // no store id to install from
    if (e.ownership === "catalog" && !(e.platform === "xbox" && subscribed("gamepass"))) continue;
    out.push(e.platform);
  }
  return out;
}
// "Steam" with Steam's icon, for the Play / Install buttons and their menus.
const storeWithIcon = (p, full = false) => {
  const icon = PLATFORM_ICON[p];
  return `${icon ? `<i class="pico" style="--icon:url('${iconUrl(icon)}')" aria-hidden="true"></i>` : ""}${esc((full ? PLATFORM_NAME[p] : PLATFORM_SHORT[p]) || p)}`;
};
function playBlock(g) {
  if (g.isWishOnly) return "";
  const can = playOrder(g);
  const menu = (items) => `<div class="play-pop" hidden>${items}</div>`;
  if (can.length) return `<div class="play-row">
      <span class="play-split"><button class="play-btn" data-act="play" data-v="${can[0]}">${PLAY_SVG}Play <small>on ${storeWithIcon(can[0])}</small></button>${can.length > 1 ? `<button class="play-caret" data-act="play-menu" aria-label="Choose which copy" aria-expanded="false">▾</button>` : ""}</span>
      ${can.length > 1 ? menu(can.map((x) => `<button data-act="play" data-v="${x}">Play on ${storeWithIcon(x, true)}</button>`).join("")) : ""}
    </div>`;
  const inst = installOrder(g);
  if (inst.length) return `<div class="play-row">
      <span class="play-split ghost"><button class="install-btn" data-act="install" data-v="${inst[0]}">${DL_SVG}Install <small>on ${storeWithIcon(inst[0])}</small></button>${inst.length > 1 ? `<button class="play-caret" data-act="play-menu" aria-label="Choose which store" aria-expanded="false">▾</button>` : ""}</span>
      ${inst.length > 1 ? menu(inst.map((x) => `<button data-act="install" data-v="${x}">Install on ${storeWithIcon(x, true)}</button>`).join("")) : ""}
    </div>`;
  return "";
}
async function playGame(id, platform) {
  const g = byId(id);
  try {
    const r = await api(`/api/games/${id}/play`, { method: "POST", body: { platform } });
    toast(`Starting ${g?.title || "the game"} on ${PLATFORM_NAME[r.platform] || r.platform}…`);
  } catch (e) { toast(e.message, true); }
}
async function installGame(id, platform) {
  try {
    await api(`/api/games/${id}/install`, { method: "POST", body: { platform } });
    toast(`Opening ${PLATFORM_NAME[platform] || platform} to install it`);
  } catch (e) { toast(e.message, true); }
}

// Shelves: lists you make. Every change redraws the filter, the open game and any open picker.
function afterShelves(shelves) {
  if (shelves) state.shelves = shelves;
  state.prefs.shelves = state.prefs.shelves.filter((s) => state.shelves.some((x) => x.id === s));
  savePrefs();
  renderFilters(); render(); renderShelfRows();  // (Curator's Your shelves too)
  if (state.openId) { const g = byId(state.openId); const row = $("#detail .shelf-row"); if (g && row) row.outerHTML = shelfRow(g); }
  const pick = $("#ctx .shelf-picker");
  if (pick && state.menuId) pick.outerHTML = shelfPicker(byId(state.menuId));
}
async function newShelf(name, gameId = null) {
  name = (name || "").trim();
  if (!name) return null;
  try {
    const r = await api("/api/shelves", { method: "POST", body: { name, games: gameId ? [gameId] : [] } });
    if (gameId) { const g = byId(gameId); if (g && !g.shelves.includes(r.id)) g.shelves.push(r.id); }
    afterShelves(r.shelves);
    toast(gameId ? `Added to ${name}` : `New shelf: ${name}`);
    return r.shelves.find((s) => s.id === r.id);
  } catch (e) { toast(e.message, true); return null; }
}
async function toggleShelf(gameId, shelfId, on) {
  const g = byId(gameId);
  try {
    const r = await api(`/api/shelves/${shelfId}/games`, { method: "POST", body: on ? { add: [gameId] } : { remove: [gameId] } });
    if (g) g.shelves = on ? [...new Set([...g.shelves, shelfId])] : g.shelves.filter((s) => s !== shelfId);
    afterShelves(r.shelves);
    toast(on ? `Added to ${shelfName(shelfId)}` : `Taken off ${shelfName(shelfId)}`, false,
      () => toggleShelf(gameId, shelfId, !on));
  } catch (e) { toast(e.message, true); }
}
function renameShelfInline(row, id) {
  row.innerHTML = `<form class="shelf-new" data-shelf-rename-form="${id}"><input type="text" name="name" value="${esc(shelfName(id))}" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore><button type="submit" class="btn small">Save</button></form>`;
  const input = $("input", row); input.focus(); input.select();
}
async function renameShelf(id, name) {
  try {
    const r = await api(`/api/shelves/${id}`, { method: "PATCH", body: { name } });
    afterShelves(r.shelves);
  } catch (e) { toast(e.message, true); }
}
async function deleteShelf(id) {
  const name = shelfName(id), rules = shelfById(id)?.rules || null;
  if (state.editSmart === id) state.editSmart = null;
  const games = state.games.filter((g) => g.shelves.includes(id)).map((g) => g.id);
  try {
    const r = await api(`/api/shelves/${id}`, { method: "DELETE" });
    for (const g of state.games) g.shelves = g.shelves.filter((s) => s !== id);
    afterShelves(r.shelves);
    toast(`Deleted the shelf ${name}`, false, async () => {
      // Undo: the shelf comes back with the same games
      try {
        const r2 = await api("/api/shelves", { method: "POST", body: rules ? { name, rules } : { name, games } });
        for (const g of state.games) if (games.includes(g.id)) g.shelves.push(r2.id);
        afterShelves(r2.shelves);
        toast(`${name} is back`);
      } catch (e) { toast(e.message, true); }
    });
  } catch (e) { toast(e.message, true); }
}
/* Smart shelves: the Library's filters, saved. "Save as smart shelf" next to the filters makes
   one; "rules" in the Shelves menu brings its filters back to change them, then "Update". */
const RULE_LABELS = () => ({ platform: "Platform", status: "Status", played: "Played", genre: "Genre", type: "Type", curator: CURATOR,
  more: subsLabel(), installed: "Installed", length: "Length", reviews: "Reviews", players: "Players", shelves: "Shelves" });
function currentRules() {
  const p = state.prefs, rules = {};
  for (const k of filterKeys()) rules[k] = Array.isArray(p[k]) ? [...p[k]] : p[k];
  rules.shelves = rules.shelves.filter((id) => !isSmart(id));  // a smart shelf can't be built on another one
  if (state.q.trim()) rules.q = state.q.trim();
  return rules;
}
function ruleChips() {
  const sum = filterSummaries(filterFacts()), labels = RULE_LABELS();
  const chips = Object.entries(sum).filter(([k, v]) => v && !(k === "shelves" && !currentRules().shelves.length)).map(([k, v]) => `${labels[k]}: ${v}`);
  if (state.q.trim()) chips.push(`Search: “${state.q.trim()}”`);
  return chips;
}
function smartButton(sum) {
  const editing = state.editSmart && shelfById(state.editSmart);
  if (editing) return `<button type="button" class="save-smart" data-smart="update">${SPARK}Update ${esc(editing.name)}</button>
    <button type="button" class="link" data-smart="cancel">Stop editing</button>`;
  const ruled = Object.entries(sum).some(([k, v]) => v && k !== "shelves") || state.q.trim();
  return ruled ? `<button type="button" class="save-smart" data-smart="new" title="A shelf that fills itself with every game that fits these filters">${SPARK}Save as smart shelf</button>` : "";
}
// After saving: the Library shows just that shelf, the way it'll look from now on.
function showOnlyShelf(id) {
  Object.assign(state.prefs, FILTER_DEFAULTS(), { shelves: [id] });
  state.q = ""; $("#search").value = "";
  savePrefs(); showPage("library"); renderFilters(); render();
}
async function onSmartButton(kind) {
  if (kind === "new") { openSmartDialog(); return; }
  const id = state.editSmart;
  if (kind === "cancel") {
    state.editSmart = null;
    if (state.editPrev) { Object.assign(state.prefs, state.editPrev.prefs); state.q = state.editPrev.q; $("#search").value = state.q; savePrefs(); }
    renderFilters(); render(); return;
  }
  if (kind === "update" && id) {
    try {
      const r = await api(`/api/shelves/${id}`, { method: "PATCH", body: { rules: currentRules() } });
      state.editSmart = null;
      afterShelves(r.shelves);
      showOnlyShelf(id);
      toast(`Updated ${shelfName(id)}: ${state.games.filter((g) => onShelves(g).includes(id)).length} games`);
    } catch (e) { toast(e.message, true); }
  }
}
function openSmartDialog() {
  const n = visibleGames().length;
  $("#smart-body").innerHTML = `<p class="source-note" style="margin-top:0">It fills itself with every game that fits these filters, and keeps up as your library changes.</p>
    <form id="smart-form">
      <label class="field"><span>Name</span><input type="text" name="name" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore required></label>
      <div class="rule-chips">${ruleChips().map((c) => `<span>${esc(c)}</span>`).join("")}</div>
      <p class="source-note">${n.toLocaleString()} game${n === 1 ? "" : "s"} right now.</p>
      <div class="dialog-actions"><button type="button" class="btn ghost" data-close-dialog>Cancel</button><button type="submit" class="btn">Save shelf</button></div>
    </form>`;
  $("#smart-dialog").showModal();
  $("#smart-form input").focus();
}
$("#smart-body").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = e.target.elements.name.value.trim();
  if (!name) return;
  try {
    const r = await api("/api/shelves", { method: "POST", body: { name, rules: currentRules() } });
    $("#smart-dialog").close();
    afterShelves(r.shelves);
    showOnlyShelf(r.id);
    const n = state.games.filter((g) => onShelves(g).includes(r.id)).length;
    toast(`New smart shelf: ${name} (${n} game${n === 1 ? "" : "s"})`);
  } catch (err) { toast(err.message, true); }
});
// "rules" in the Shelves menu: its filters come back; change them, then Update (or Stop editing).
function editSmartShelf(id) {
  const s = shelfById(id);
  if (!s?.rules) return;
  const keep = {};
  for (const k of filterKeys()) keep[k] = Array.isArray(state.prefs[k]) ? [...state.prefs[k]] : state.prefs[k];
  state.editPrev = { prefs: keep, q: state.q };
  const { q, ...rules } = s.rules;
  Object.assign(state.prefs, FILTER_DEFAULTS(), rules);
  state.q = q || ""; $("#search").value = state.q;
  state.editSmart = id; state.openFilter = null;
  savePrefs(); showPage("library"); renderFilters(); render();
  toast(`Change the filters, then choose Update ${s.name}`);
}

function shelfPicker(g) {
  if (!g) return "";
  const list = (state.shelves || []).filter((s) => !s.rules);  // smart shelves fill themselves
  return `<div class="shelf-picker" data-game="${g.id}">
    ${list.map((s) => `<label class="shelf-opt"><input type="checkbox" data-shelf-toggle="${s.id}" ${g.shelves.includes(s.id) ? "checked" : ""}><span>${esc(s.name)}</span></label>`).join("")}
    <form class="shelf-new" data-shelf-new><input type="text" name="name" placeholder="${list.length ? "New shelf" : "Name your first shelf"}" maxlength="60" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore><button type="submit" class="btn small">Add</button></form>
  </div>`;
}
function shelfRow(g) {
  if (g.isWishOnly) return "";
  return `<div class="shelf-row">
    ${onShelves(g).length ? `<span class="shelf-label">Shelves</span>` : ""}
    ${g.shelves.map((s) => `<span class="shelf-pill">${esc(shelfName(s))}<button type="button" data-shelf-off="${s}" aria-label="Take it off ${esc(shelfName(s))}">✕</button></span>`).join("")}
    ${onShelves(g).filter((s) => isSmart(s)).map((s) => `<span class="shelf-pill smart" title="A smart shelf: this game fits its rules">${shelfLabel(s)}</span>`).join("")}
    <span class="shelf-add"><button type="button" class="shelf-add-btn" data-act="shelf-add" aria-expanded="false">+ Add to shelf</button><div class="shelf-add-pop" hidden>${shelfPicker(g)}</div></span>
  </div>`;
}
// Clicks and submits inside any shelf picker (game page or right-click menu).
document.addEventListener("change", (e) => {
  const box = e.target.closest("[data-shelf-toggle]");
  if (!box) return;
  const gid = Number(box.closest(".shelf-picker").dataset.game);
  toggleShelf(gid, Number(box.dataset.shelfToggle), box.checked);
});
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-shelf-new]");
  if (!form) return;
  e.preventDefault();
  const gid = Number(form.closest(".shelf-picker").dataset.game);
  await newShelf(form.elements.name.value, gid);
});

// More like this: IGDB's similar games that you own or can play (XGP / PS+ you subscribe to).
function similarSection(g) {
  const list = (g.similar_games || []).map((id) => state.games.find((x) => x.id === id))
    .filter((x) => x && defaultTypes().includes(gameType(x))).slice(0, 6);
  if (!list.length) return "";
  const why = (x) => x.isCatalog ? (x.subs || []).map((s) => SUB_SHORT[s.s] || "").filter(Boolean).map((s) => `On ${s}`).join(" · ")
    : x.status === "completed" ? "Completed" : x.neverPlayed ? "Not played yet" : `${fmtHours(x.played_h)} played`;
  return `<section class="section similar"><h2>More like this</h2>
    <p class="source-note">${subsLabel() ? `Similar games you own or can play on ${esc(subsLabel())}` : "Similar games you own"}, from IGDB.</p>
    <div class="similar-row">${list.map((x) => `<button class="rec" data-open="${x.id}" title="${esc(x.title)}">
      <div class="cover">${coverHTML(x)}<div class="badges">${badgesHTML(x)}</div>${instMark(x)}</div>
      <div class="rec-title">${esc(x.title)}</div><div class="rec-why">${esc(why(x))}</div></button>`).join("")}</div>
  </section>`;
}

/* ------------------------------------------------------------ Your year in games */

// One calendar year, from what the stores report (the server works out which games and when:
// /api/year/<y>). A game counts as finished on its achievement's date when it was marked
// automatically, else on the day you last played it, so games marked during setup land in
// the year you actually played them.
state.yearCache = {};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Your pace (Stats): how your hours on games you finished compare with the usual time to the
// credits (Main + Extras, as on the progress bars). Only a stat: estimates elsewhere don't change.
function paceStats() {
  const rows = [];
  for (const g of state.games) {
    if (g.status !== "completed" || g.isEndless || g.isHidden) continue;
    const usual = g.ttb_extra || g.ttb_main, mine = g.played_h;
    if (!usual || !(mine > 0)) continue;
    const r = mine / usual;
    if (r < 0.25 || r > 4) continue;  // far off: a replay, idle hours, or hours from another copy
    rows.push({ g, usual, mine, r });
  }
  if (rows.length < 5) return null;
  const sorted = rows.map((x) => x.r).sort((a, b) => a - b);
  const mid = sorted.length / 2;
  const median = sorted.length % 2 ? sorted[Math.floor(mid)] : (sorted[mid - 1] + sorted[mid]) / 2;
  rows.sort((a, b) => a.r - b.r);
  return { median, n: rows.length, quicker: rows.filter((x) => x.r < 0.9).length, longer: rows.filter((x) => x.r > 1.1).length,
    fastest: rows.slice(0, 3), slowest: rows.slice(-3).reverse() };
}
function paceHTML() {
  const p = paceStats();
  if (!p) return "";
  const m = Math.round(p.median * 100) / 100;
  const say = Math.abs(m - 1) < 0.05 ? "the usual time to the credits: about the same as most players"
    : m > 1 ? `the usual time to the credits: you take ${Math.round((m - 1) * 100)}% longer` : `the usual time to the credits: you're ${Math.round((1 - m) * 100)}% quicker`;
  const line = (x) => `<li><button type="button" class="link" data-id="${x.g.id}">${esc(x.g.title)}</button><span>${esc(fmtHours(x.mine))} <em>vs about ${esc(fmtHours(x.usual))}</em></span></li>`;
  return `<section class="ysec ypace"><h2>Your pace <small>all time, from ${p.n} games you finished</small></h2>
    <div class="ypace-grid">
      <div class="ytile"><b>${m.toLocaleString(undefined, { maximumFractionDigits: 2 })}×</b><span>${esc(say)}</span></div>
      <div><h3>Took your time with</h3><ul>${p.slowest.map(line).join("")}</ul></div>
      <div><h3>Breezed through</h3><ul>${p.fastest.map(line).join("")}</ul></div>
    </div>
    <p class="source-note">Your hours compared with HowLongToBeat's Main + Extras (or IGDB's). Games far off the usual time aren't counted (a replay, or a game left running). ${p.quicker} were quicker than usual, ${p.longer} took longer.</p>
  </section>`;
}

async function renderYear() {
  const el = $("#year");
  const thisYear = new Date().getFullYear();
  const y = state.yearSel || thisYear;
  let d = state.yearCache[y];
  if (!d) {
    if (!el.innerHTML) el.innerHTML = `<p class="source-note year-loading">Looking back at ${y}…</p>`;
    try { d = await api(`/api/year/${y}`); } catch (e) { el.innerHTML = `<p class="notice">${esc(e.message)}</p>`; return; }
    state.yearCache[y] = d;
  }
  if (state.page !== "year" || (state.yearSel || thisYear) !== y) return;
  const types = defaultTypes();
  const pick = (ids) => ids.map((id) => state.games.find((g) => g.id === id)).filter((g) => g && types.includes(gameType(g)));
  const played = pick(d.played).sort((a, b) => (b.last_played || 0) - (a.last_played || 0));
  const at = Object.fromEntries(d.finished.map((f) => [f.id, f.at]));
  const finished = pick(d.finished.map((f) => f.id));
  const started = pick(d.started);
  const mins = d.minutes || {};
  const genres = {};
  for (const g of played) for (const x of g.genres || []) genres[x] = (genres[x] || 0) + 1;
  const topGenres = Object.entries(genres).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 8);
  const platforms = new Set(played.flatMap((g) => g.platforms));
  const withHours = played.filter((g) => mins[g.id] > 0).sort((a, b) => mins[b.id] - mins[a.id]);
  const most = (withHours.length >= 3 ? withHours : played.slice().sort((a, b) => b.playtime_min - a.playtime_min)).slice(0, 5);
  // dates within the year being shown don't need the year
  const day = (ts) => new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const mostThisYear = withHours.length >= 3;
  const months = Array(12).fill(0);
  for (const g of finished) months[new Date(at[g.id] * 1000).getMonth()]++;
  const mmax = Math.max(1, ...months);
  const card = (g, why) => `<button class="rec mini" data-id="${g.id}" title="${esc(g.title)}"><div class="cover">${coverHTML(g)}${instMark(g)}</div>
    <div class="rec-title">${esc(g.title)}</div><div class="rec-why">${why}</div></button>`;
  const years = d.years || [thisYear];
  const prev = years.includes(y - 1), next = y < thisYear;
  const tile = (n, label) => `<div class="ytile"><b>${n}</b><span>${label}</span></div>`;
  const since = d.log_since ? new Date(d.log_since + "T00:00").toLocaleDateString(undefined, { month: "long", day: "numeric" }) : "";
  setHTML(el, `
    <div class="year-head">
      <button class="btn icon ghost" data-year="${y - 1}" ${prev ? "" : "disabled"} aria-label="${y - 1}">‹</button>
      <h1>Your <span>${y}</span> in games</h1>
      <button class="btn icon ghost" data-year="${y + 1}" ${next ? "" : "disabled"} aria-label="${y + 1}">›</button>
    </div>
    <p class="year-lede">${y === thisYear ? "January to today" : `All of ${y}`}, from what your stores report.</p>
    ${!played.length ? `<p class="notice">Nothing on record for ${y}: your stores don't report any play that year.</p>` : `
    <div class="ytiles">
      ${tile(played.length.toLocaleString(), `game${played.length === 1 ? "" : "s"} played`)}
      ${tile(finished.length.toLocaleString(), "seen to the credits")}
      ${started.length ? tile(started.length.toLocaleString(), "started for the first time") : tile(platforms.size, `platform${platforms.size === 1 ? "" : "s"} played on`)}
      ${topGenres.length ? tile(esc(genreLabel(topGenres[0][0])), "the genre you played most") : ""}
    </div>
    ${finished.length ? `<section class="ysec"><h2>Seen to the credits <small>newest first</small></h2>
      <div class="ygrid">${finished.map((g) => card(g, esc(day(at[g.id])))).join("")}</div></section>` : ""}
    <div class="ycols">
      <div>
        ${finished.length >= 2 ? `<section class="ysec"><h2>Credits rolled, month by month</h2>
          <div class="ymonths" role="img" aria-label="Games finished each month: ${months.map((n, i) => `${MONTHS[i]} ${n}`).join(", ")}">
            ${months.map((n, i) => `<div class="ymonth" title="${MONTHS[i]}: ${n} game${n === 1 ? "" : "s"}"><em>${n || ""}</em><i style="height:${n ? Math.max(6, n / mmax * 100) : 0}%"></i><span>${MONTHS[i][0]}</span></div>`).join("")}
          </div></section>` : ""}
        <section class="ysec"><h2>Most played <small>${mostThisYear ? `hours in ${y}${since ? `, counted since ${esc(since)}` : ""}` : `of your ${y} games, all-time hours`}</small></h2>
          <div class="ygrid small">${most.map((g) => card(g, esc(fmtHours(mostThisYear ? mins[g.id] / 60 : g.played_h)))).join("")}</div></section>
      </div>
      ${topGenres.length ? `<section class="ysec"><h2>Genres you played <small>games in each</small></h2>
        <div class="ygenres">${topGenres.map(([n, c]) => `<div class="ygenre" title="${esc(genreLabel(n))}: ${c} game${c === 1 ? "" : "s"}"><span>${esc(genreLabel(n))}</span><i style="width:${c / topGenres[0][1] * 100}%"></i><em>${c}</em></div>`).join("")}</div></section>` : ""}
    </div>
    ${paceHTML()}
    <section class="ysec"><h2>Everything you played in ${y} <small>most recent first</small></h2>
      <div class="ygrid">${played.map((g) => card(g, g.last_played && new Date(g.last_played * 1000).getFullYear() === y ? `Last played ${esc(day(g.last_played))}` : esc(fmtHours(g.played_h)))).join("")}</div></section>`}
  `);
}
$("#page-year").addEventListener("click", (e) => {
  const yb = e.target.closest("[data-year]");
  if (yb && !yb.disabled) { state.yearSel = Number(yb.dataset.year); renderYear(); scrollTo({ top: 0 }); return; }
  const card = e.target.closest("[data-id]");
  if (card) openGame(Number(card.dataset.id));
});
$("#page-year").addEventListener("contextmenu", (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  e.preventDefault();
  openMenu(Number(card.dataset.id), e.clientX, e.clientY);
});

/* ------------------------------------------------------------ add game / fix match */

function igdbReady() {
  const s = state.settings?.settings || {};
  return Boolean(s.igdb_client_id && s.igdb_client_secret?.set);
}

function searchBox(onPick, initial = "") {
  let timer, picked = null;
  const box = document.createElement("div");
  box.innerHTML = `
    <label class="field"><span>Search IGDB</span><input type="search" class="igdb-q" value="${esc(initial)}" placeholder="Type a game title"></label>
    <div class="results" role="listbox"></div>`;
  const input = $(".igdb-q", box), results = $(".results", box);
  async function run() {
    const q = input.value.trim();
    if (q.length < 2) { results.innerHTML = ""; return; }
    results.innerHTML = `<span class="source-note">Searching…</span>`;
    try {
      const data = await api(`/api/igdb/search?q=${encodeURIComponent(q)}`);
      results.innerHTML = data.results.length ? data.results.map((r) => `
        <button type="button" class="result" data-id="${r.id}" data-name="${esc(r.name)}">
          ${r.cover ? `<img src="${esc(r.cover)}" alt="" data-fallbacks="">` : `<span class="noimg"></span>`}
          <span><b>${esc(r.name)}</b><small>${r.year || "Year unknown"}${r.is_edition ? ", edition or DLC" : ""}</small></span>
        </button>`).join("") : `<span class="source-note">No matches. Try a shorter title.</span>`;
    } catch (e) { results.innerHTML = `<span class="test-result err">${esc(e.message)}</span>`; }
  }
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 300); });
  results.addEventListener("click", (e) => {
    const b = e.target.closest(".result");
    if (!b) return;
    $$(".result", results).forEach((x) => x.classList.toggle("on", x === b));
    picked = { id: Number(b.dataset.id), name: b.dataset.name };
    onPick(picked);
  });
  if (initial) setTimeout(run, 0);
  return box;
}

function openAdd() {
  const dlg = $("#add-dialog");
  $("#add-title").textContent = "Add a game";
  const body = $("#add-body");
  let picked = null;
  const options = Object.entries(state.platforms).filter(([k]) => k !== "steam" && k !== "playstation")
    .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  body.innerHTML = `
    <p class="lede">For Switch 2 and anywhere ${APP} can't import from yet. Art and details fill in automatically.</p>
    <button class="btn quiet small shots-link" data-open-shots>Import Nintendo Switch games from a screen recording instead</button>
    <div id="add-search"></div>
    <label class="field"><span>Title</span><input type="text" id="add-name" placeholder="Game title"></label>
    <div class="row">
      <label class="field"><span>Platform</span><select id="add-platform">${options}<option value="playstation">PlayStation (not played yet)</option><option value="steam">Steam</option></select></label>
      <label class="field"><span>Hours played</span><input type="number" id="add-hours" min="0" step="0.5" placeholder="0"></label>
    </div>
    <div class="row">
      <label class="field"><span>Last played</span><input type="date" id="add-last"></label>
      <label class="field"><span>Status</span><select id="add-status"><option value="">No status</option><option value="completed">Completed</option><option value="dnf">Will not finish</option></select></label>
    </div>
    <div class="dialog-actions"><button class="btn ghost" data-close-dialog>Cancel</button><button class="btn" id="add-save">Add to library</button></div>`;
  if (igdbReady()) {
    $("#add-search").append(searchBox((p) => { picked = p; $("#add-name").value = p.name; }));
  } else {
    $("#add-search").innerHTML = `<p class="source-note">Connect IGDB in Settings to search for games and fill in art automatically.</p>`;
  }
  $("#add-name").addEventListener("input", () => { picked = picked && $("#add-name").value === picked.name ? picked : null; });
  $("#add-save").onclick = async () => {
    const title = $("#add-name").value.trim();
    if (!title) { toast("Enter a title first", true); return; }
    const last = $("#add-last").value;
    try {
      const g = await api("/api/manual", {
        method: "POST",
        body: {
          title, igdb_id: picked?.id || null, platform: $("#add-platform").value,
          hours: Number($("#add-hours").value || 0),
          last_played: last ? Math.floor(new Date(last + "T12:00:00").getTime() / 1000) : null,
          status: $("#add-status").value,
        },
      });
      upsertLocal(g);
      dlg.close();
      toast(`Added ${g.title}. Fetching art and details…`);
      setTimeout(reloadLibrary, 6000);
      setTimeout(reloadLibrary, 15000);
    } catch (e) { toast(e.message, true); }
  };
  dlg.showModal();
  setTimeout(() => (igdbReady() ? $(".igdb-q", body) : $("#add-name")).focus(), 30);
}

function openFixMatch(g) {
  if (!igdbReady()) { toast("Connect IGDB in Settings first", true); return; }
  const dlg = $("#add-dialog");
  $("#add-title").textContent = "Fix match";
  const body = $("#add-body");
  body.innerHTML = `<p class="lede">Pick the right game for “${esc(g.title)}”. Your playtime and status stay as they are.</p><div id="fix-search"></div>
    <div class="dialog-actions"><button class="btn ghost" data-close-dialog>Cancel</button><button class="btn" id="fix-save" disabled>Use this game</button></div>`;
  let picked = null;
  $("#fix-search").append(searchBox((p) => { picked = p; $("#fix-save").disabled = false; }, g.title));
  $("#fix-save").onclick = async () => {
    if (!picked) return;
    $("#fix-save").disabled = true;
    $("#fix-save").textContent = "Updating…";
    try {
      const updated = await api(`/api/games/${g.id}/match`, { method: "POST", body: { igdb_id: picked.id } });
      dlg.close();
      await reloadLibrary();
      openGame(updated.id);
      toast("Match updated");
    } catch (e) { toast(e.message, true); $("#fix-save").disabled = false; $("#fix-save").textContent = "Use this game"; }
  };
  dlg.showModal();
}

/* ------------------------------------------------------------ Nintendo Switch from a screen recording or screenshots */

const shots = { files: [], items: null, problems: [], video: null, poll: null };

function openShots() {
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  clearInterval(shots.poll);
  shots.files = []; shots.items = null; shots.problems = []; shots.video = null;
  renderShots();
  $("#shots-dialog").showModal();
  // A recording may still be being read from before the window was closed.
  api("/api/switch/video").then((st) => {
    if (["starting", "setup", "scanning", "reading"].includes(st.state)) watchVideo();
    // Finished while the window was closed: pick up where it left off.
    else if (st.state === "done" && !st.applied && (st.items || []).length && Date.now() / 1000 - st.started < 3600) watchVideo(st);
  }).catch(() => {});
}

const PLAT_LABEL = { switch2: "Switch 2", switch: "Switch" };

function videoStageHTML() {
  const v = shots.video || {};
  if (v.uploading != null) {
    return `<p class="lede">Sending ${esc(v.name || "the recording")} to ${APP}…</p><div class="progress"><i style="width:${Math.round(v.uploading * 100)}%"></i></div>`;
  }
  const st = v.status || {};
  const label = {
    starting: "Getting started…",
    setup: "Getting the video reader ready. This happens once and takes a minute or two.",
    scanning: "Finding each game in the recording…",
    reading: st.total ? `Reading game cards: ${st.done} of ${st.total}` : "Reading game cards…",
  }[st.state] || "Working…";
  const pct = st.state === "reading" && st.total ? Math.round(100 * st.done / st.total) : null;
  return `<p class="lede">${esc(label)}</p><div class="progress ${pct == null ? "indeterminate" : ""}"><i style="width:${pct ?? 30}%"></i></div>
    <p class="source-note">You can keep this window open or come back to it; the recording keeps being read.</p>`;
}

function renderShots(stage = "pick") {
  const body = $("#shots-body");
  if (stage === "reading") {
    body.innerHTML = `<p class="lede">Reading ${shots.files.length} screenshot${shots.files.length > 1 ? "s" : ""}…</p><div class="progress indeterminate"><i></i></div>`;
    return;
  }
  if (shots.video && !shots.items) { body.innerHTML = videoStageHTML(); return; }
  if (shots.items) {
    const items = shots.items;
    const ticked = items.filter((x) => x.on).length;
    const rich = items.some((x) => x.first_played || x.last_played);
    const statusText = (x) => x.status === "update"
      ? (x.hours > x.current_hours + 0.05 ? `In your library: ${fmtHours(x.current_hours, { empty: "0 h" })} → ${fmtHours(x.hours)}` : "In your library, dates and history updated")
      : x.status === "join" ? `Joins ${esc(x.game_title)} from your other stores`
      : x.system ? "Nintendo app, not a game" : x.demo ? "Demo" : "New";
    body.innerHTML = `
      <p class="lede">Here's what ${APP} found. Untick anything that isn't right; you can fix titles, hours and the console too.${rich ? "" : ` Nintendo shows hours as "or more", so these are minimums.`}</p>
      ${shots.problems.map((p) => `<p class="test-result err">${esc(p)}</p>`).join("")}
      ${items.length ? `<div class="shots-list ${rich ? "rich" : ""}">
        ${items.map((x, i) => `<div class="shots-item ${x.on ? "" : "off"}" data-i="${i}">
          <input type="checkbox" ${x.on ? "checked" : ""} data-f="on" aria-label="Include">
          <span class="shots-title"><input type="text" value="${esc(x.title)}" data-f="title" aria-label="Title" spellcheck="false">
            ${rich ? `<small>${[x.last_played ? `Last played ${esc(fmtDate(x.last_played))}` : "", x.days ? `${x.days} day${x.days === 1 ? "" : "s"} played` : ""].filter(Boolean).join(" · ")}</small>` : ""}</span>
          <select data-f="platform" aria-label="Console">${["switch2", "switch"].map((p) => `<option value="${p}" ${x.platform === p ? "selected" : ""}>${PLAT_LABEL[p]}</option>`).join("")}</select>
          <span class="shots-hours"><input type="number" min="0" step="0.5" value="${Math.round(x.hours * 10) / 10}" data-f="hours" aria-label="Hours"> h${x.little ? `<small>a little while</small>` : ""}</span>
          <span class="shots-status ${x.status}">${statusText(x)}</span>
        </div>`).join("")}
      </div>` : `<p class="lede">No games found.</p>`}
      <div class="dialog-actions"><button class="btn ghost" data-shots-back>Start over</button>
        <button class="btn" data-shots-apply ${ticked ? "" : "disabled"}>Add ${ticked} game${ticked === 1 ? "" : "s"}</button></div>`;
    return;
  }
  body.innerHTML = `
    <p class="lede">Nintendo doesn't share your games online, but its <b>Nintendo Store</b> phone app shows them. Record your screen while you flip through them, and ${APP} reads every game's playtime, first and last played dates and day-by-day history from the video.</p>
    <ol class="shots-how">
      <li>On your phone, open the <b>Nintendo Store</b> app (free on the App Store and Google Play) and sign in with your Nintendo Account.</li>
      <li>Open your <b>profile</b>, go to <b>Play History</b> and tap the first game in the list to open its card.</li>
      <li>Start a screen recording. iPhone: open Control Center and tap the record button (⏺). Android: swipe down from the top twice and tap <b>Screen record</b>.</li>
      <li>Back in the app, swipe from card to card through all your games, pausing about a second on each one.</li>
      <li>Stop the recording and get the video onto this PC (email it to yourself, or use OneDrive, Google Drive or Google Photos), then drop it below.</li>
    </ol>
    <label class="dropzone" id="shots-drop" tabindex="0">
      <input type="file" id="shots-input" accept="video/*,image/*" multiple hidden>
      <b>Drop the screen recording here</b><span>or click to choose it. Screenshots of the Play History list work too, but give hours only (Ctrl+V to paste).</span>
    </label>
    ${shots.files.length ? `<div class="shots-thumbs">${shots.files.map((f, i) => `<figure><img src="${f.url}" alt=""><button class="btn icon ghost" data-shots-remove="${i}" aria-label="Remove">✕</button></figure>`).join("")}</div>` : ""}
    <div class="dialog-actions"><button class="btn ghost" data-close-dialog>Cancel</button>
      ${shots.files.length ? `<button class="btn" data-shots-read>Read ${shots.files.length} screenshot${shots.files.length === 1 ? "" : "s"}</button>` : ""}</div>`;
}

function addShotFiles(fileList) {
  const all = [...fileList];
  const video = all.find((f) => f.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv)$/i.test(f.name));
  if (video) { uploadVideo(video); return; }
  const files = all.filter((f) => f.type.startsWith("image/"));
  let pending = files.length;
  if (!pending) { toast("Choose a screen recording or screenshots", true); return; }
  for (const f of files) {
    const r = new FileReader();
    r.onload = () => { shots.files.push({ name: f.name, url: r.result }); if (--pending === 0) renderShots(); };
    r.readAsDataURL(f);
  }
}

function uploadVideo(file) {
  shots.video = { name: file.name, uploading: 0 };
  renderShots();
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/switch/video");
  xhr.setRequestHeader("X-GameShelf", "1");
  xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
  xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable && shots.video) { shots.video.uploading = e.loaded / e.total; renderShots(); }
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* empty */ }
    if (xhr.status !== 200) { shots.video = null; renderShots(); toast(data.error || "Couldn't send the video", true); return; }
    watchVideo(data);
  };
  xhr.onerror = () => { shots.video = null; renderShots(); toast(`Couldn't send the video to ${APP}`, true); };
  xhr.send(file);
}

function watchVideo(first) {
  shots.video = { ...(shots.video || {}), uploading: null, status: first || { state: "starting" } };
  renderShots();
  clearInterval(shots.poll);
  shots.poll = setInterval(async () => {
    let st;
    try { st = await api("/api/switch/video"); } catch { return; }
    if (!$("#shots-dialog").open) return;
    shots.video.status = st;
    if (st.state === "done") {
      clearInterval(shots.poll);
      shots.problems = st.problems || [];
      shots.items = (st.items || []).map((x) => ({ ...x, on: !x.system && !x.demo }));
      renderShots();
    } else if (st.state === "error") {
      clearInterval(shots.poll);
      shots.video = null;
      renderShots();
      shots.problems = [];
      toast(st.error || "Couldn't read the video", true);
    } else renderShots();
  }, 1000);
}

async function readShots() {
  renderShots("reading");
  try {
    const res = await api("/api/switch/preview", { method: "POST", body: { images: shots.files.map((f) => f.url) } });
    shots.problems = res.problems || [];
    shots.items = (res.items || []).map((x) => ({ ...x, on: !x.system && !x.demo && !(x.status === "update" && x.hours <= x.current_hours) }));
  } catch (e) {
    shots.problems = [e.message]; shots.items = [];
  }
  renderShots();
}

async function applyShots() {
  const items = shots.items.filter((x) => x.on).map((x) => ({
    title: x.title, hours: Number(x.hours) || 0, platform: x.platform,
    first_played: x.first_played || null, last_played: x.last_played || null,
    days: x.days || null, sessions: x.sessions || [],
  }));
  try {
    const res = await api("/api/switch/import", { method: "POST", body: { items, from_video: Boolean(shots.video) } });
    $("#shots-dialog").close();
    await reloadLibrary();
    const bits = [];
    if (res.added) bits.push(`added ${res.added} game${res.added > 1 ? "s" : ""}`);
    if (res.updated) bits.push(`updated ${res.updated}`);
    toast(bits.length ? `Nintendo Switch: ${bits.join(", ")}. Art and details are on their way.` : "Nothing new to add");
    setTimeout(reloadLibrary, 8000); setTimeout(reloadLibrary, 25000);
  } catch (e) { toast(e.message, true); }
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-shots]")) openShots();
  if (e.target.closest("[data-open-dups]")) { $("#sync-dialog").close(); openDups(); }
  if (e.target.closest("[data-open-unid]")) { $("#sync-dialog").close(); openUnid(); }
});
$("#shots-body").addEventListener("click", (e) => {
  if (e.target.closest("[data-shots-read]")) readShots();
  else if (e.target.closest("[data-shots-apply]")) applyShots();
  else if (e.target.closest("[data-shots-back]")) { shots.items = null; shots.problems = []; shots.video = null; shots.files = []; renderShots(); }
  else if (e.target.closest("[data-shots-remove]")) { shots.files.splice(Number(e.target.closest("[data-shots-remove]").dataset.shotsRemove), 1); renderShots(); }
});
$("#shots-body").addEventListener("change", (e) => {
  if (e.target.id === "shots-input") { addShotFiles(e.target.files); return; }
  const row = e.target.closest("[data-i]");
  if (!row || !shots.items) return;
  const x = shots.items[Number(row.dataset.i)], f = e.target.dataset.f;
  if (f === "on") { x.on = e.target.checked; renderShots(); }
  else if (f === "title") x.title = e.target.value;
  else if (f === "hours") x.hours = e.target.value;
  else if (f === "platform") x.platform = e.target.value;
});
$("#shots-body").addEventListener("dragover", (e) => { if (e.target.closest("#shots-drop")) { e.preventDefault(); e.target.closest("#shots-drop").classList.add("over"); } });
$("#shots-body").addEventListener("dragleave", (e) => { e.target.closest("#shots-drop")?.classList.remove("over"); });
$("#shots-body").addEventListener("drop", (e) => { if (e.target.closest("#shots-drop")) { e.preventDefault(); addShotFiles(e.dataTransfer.files); } });
document.addEventListener("paste", (e) => {
  if (!$("#shots-dialog").open || shots.items || shots.video) return;
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); addShotFiles(files); }
});

/* ------------------------------------------------------------ art picker */

const ART_SLOTS = [
  { id: "cover", label: "Cover" },
  { id: "hero", label: "Banner" },
  { id: "logo", label: "Logo" },
];

async function openArt(id, slot = "cover") {
  const dlg = $("#art-dialog");
  const g = byId(id);
  $("#art-title").textContent = `Change art${g ? ` for ${g.title}` : ""}`;
  const body = $("#art-body");
  body.innerHTML = `<p class="lede">Looking for artwork…</p><div class="progress indeterminate"><i></i></div>`;
  if (!dlg.open) dlg.showModal();
  let data;
  try { data = await api(`/api/games/${id}/art`); }
  catch (e) { body.innerHTML = `<p class="test-result err">${esc(e.message)}</p>`; return; }
  state.art = { id, data, slot, preview: null };
  renderArt();
}

function renderArt() {
  const { data, slot } = state.art;
  if (state.art.preview) {
    const p = state.art.preview;
    const isCurrent = p.url === data.current[slot];
    $("#art-body").innerHTML = `
      <div class="art-preview art-${slot}"><img src="${esc(p.url)}" alt="" data-fallbacks=""></div>
      <p class="source-note">${esc(p.source || "")}${p.size ? `, ${esc(p.size)}` : ""}</p>
      <div class="dialog-actions"><button class="btn ghost" data-art-back>Back to all ${slot === "hero" ? "banners" : slot + "s"}</button>
        <button class="btn" data-art-use ${isCurrent ? "disabled" : ""}>${isCurrent ? "In use" : `Use this ${slot === "hero" ? "banner" : slot}`}</button></div>`;
    return;
  }
  const opts = data.options[slot] || [];
  const current = data.current[slot];
  const locked = data.locks.includes(slot);
  $("#art-body").innerHTML = `
    <div class="segmented art-tabs" role="tablist">
      ${ART_SLOTS.map((t) => `<button data-slot="${t.id}" class="${t.id === slot ? "on" : ""}" role="tab">${t.label} <em>${(data.options[t.id] || []).length}</em></button>`).join("")}
    </div>
    <p class="source-note art-state">${locked ? "You picked this one. It stays even when you use Refresh assets." : "Chosen automatically."}
      ${locked ? `<button class="link" data-art-reset>Go back to automatic</button>` : ""}</p>
    ${opts.length ? `<div class="art-grid art-${slot}">
      ${opts.map((o) => `<button class="art-opt ${o.url === current ? "on" : ""}" data-url="${esc(o.url)}" data-source="${esc(o.source)}" data-size="${o.w ? `${o.w}×${o.h}` : ""}" title="Click for a closer look">
        <img src="${esc(o.thumb || o.url)}" alt="" loading="lazy" data-fallbacks=""><span>${esc(o.source)}</span></button>`).join("")}
    </div>` : `<p class="lede">No ${slot === "hero" ? "banners" : slot + "s"} found for this game.</p>`}
    ${data.notes.map((n) => `<p class="source-note">${esc(n)}</p>`).join("")}
    <div class="art-url">
      <label class="field"><span>Or paste an image link</span><input type="text" id="art-url" placeholder="https://…" spellcheck="false"></label>
      <button class="btn quiet small" data-art-paste>Use this image</button>
    </div>`;
}

async function chooseArt(url) {
  const { id, slot } = state.art;
  try {
    const g = await api(`/api/games/${id}/art`, { method: "POST", body: { slot, url } });
    upsertLocal(g);
    if (state.openId === id) renderDetail(derive(g));
    state.art.data.current = { cover: g.cover_url, hero: g.hero_url, logo: g.logo_url };
    state.art.data.locks = (g.art_locks || "").split(",").filter(Boolean);
    renderArt();
    toast(url ? "Art updated" : "Back to automatic art");
  } catch (e) { toast(e.message, true); }
}

$("#art-body").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-slot]");
  if (tab) { state.art.slot = tab.dataset.slot; state.art.preview = null; renderArt(); return; }
  const opt = e.target.closest(".art-opt");
  if (opt) { state.art.preview = { url: opt.dataset.url, source: opt.dataset.source, size: opt.dataset.size }; renderArt(); return; }
  if (e.target.closest("[data-art-use]")) { const u = state.art.preview.url; state.art.preview = null; chooseArt(u); return; }
  if (e.target.closest("[data-art-back]")) { state.art.preview = null; renderArt(); return; }
  if (e.target.closest("[data-art-reset]")) { chooseArt(null); return; }
  if (e.target.closest("[data-art-paste]")) {
    const url = $("#art-url").value.trim();
    if (!url) { toast("Paste an image link first", true); return; }
    chooseArt(url);
  }
});

/* ------------------------------------------------------------ settings */

const SERVICES = [
  {
    id: "igdb", name: "Game info: IGDB", blurb: "Descriptions, genres, covers, time to beat",
    ready: (s) => s.igdb_client_id && s.igdb_client_secret.set,
    steps: [
      `Sign in at <a href="https://dev.twitch.tv/console/apps" target="_blank" rel="noopener">dev.twitch.tv/console/apps</a> (a free Twitch account with two-factor sign-in turned on).`,
      `Choose <b>Register Your Application</b>. Name: anything. OAuth Redirect URL: <code>http://localhost</code>. Category: Application Integration. Client type: Confidential.`,
      `Open the app, copy the <b>Client ID</b>, then choose <b>New Secret</b> and copy the secret.`,
    ],
    fields: [["igdb_client_id", "Client ID", false], ["igdb_client_secret", "Client Secret", true]],
  },
  {
    id: "steam", name: "Steam", blurb: "Owned games, playtime, last played",
    ready: (s) => s.steam_api_key.set && s.steam_id,
    steps: [
      `Get a key at <a href="https://steamcommunity.com/dev/apikey" target="_blank" rel="noopener">steamcommunity.com/dev/apikey</a>. For the domain, type <code>localhost</code>.`,
      `Paste your profile URL (Steam › your name › Profile, then copy the address).`,
      `In Steam, set <b>Edit Profile › Privacy Settings › Game details</b> to Public.`,
    ],
    fields: [["steam_api_key", "Web API key", true], ["steam_id", "Profile URL or SteamID", false]],
  },
  {
    id: "playstation", name: "PlayStation", blurb: "PS4 and PS5 games you've played, playtime, last played",
    ready: (s) => s.psn_npsso.set,
    steps: [
      `Sign in at <a href="https://www.playstation.com" target="_blank" rel="noopener">playstation.com</a> in your browser.`,
      `In the same browser, open <a href="https://ca.account.sony.com/api/v1/ssocookie" target="_blank" rel="noopener">ca.account.sony.com/api/v1/ssocookie</a>. You'll see a short line of text.`,
      `Copy the long value after <code>"npsso":</code> (or the whole line) and paste it below. It lasts about two months.`,
    ],
    fields: [["psn_npsso", "NPSSO token", true]],
  },
  {
    id: "sgdb", name: "Artwork: SteamGridDB", blurb: "Optional. Better covers, banners and logos for console games",
    ready: (s) => s.sgdb_api_key.set,
    steps: [
      `Sign in at <a href="https://www.steamgriddb.com" target="_blank" rel="noopener">steamgriddb.com</a> (via Steam).`,
      `Open <a href="https://www.steamgriddb.com/profile/preferences/api" target="_blank" rel="noopener">Preferences › API</a> and generate a key.`,
    ],
    fields: [["sgdb_api_key", "API key", true]],
  },
];
SERVICES.push({
  id: "epic", name: "Epic Games Store", blurb: "Games you own (giveaways too) and playtime",
  ready: (s) => s.epic_refresh.set,
  steps: [
    `Sign in at <a href="https://www.epicgames.com" target="_blank" rel="noopener">epicgames.com</a> in your browser.`,
    `In the same browser, open <a href="https://www.epicgames.com/id/login?redirectUrl=https%3A%2F%2Fwww.epicgames.com%2Fid%2Fapi%2Fredirect%3FclientId%3D34a02cf8f4414e29b15921876da36f9a%26responseType%3Dcode" target="_blank" rel="noopener">this Epic sign-in link</a>. You'll see a short line of text.`,
    `Copy the value after <code>"authorizationCode":</code> (or the whole line), paste it below and choose Save and test straight away: the code only works once, for a few minutes. ${APP} stays signed in after that, as long as you sync every few weeks.`,
  ],
  fields: [["epic_code", "Sign-in code", true]],
  clears: ["epic_refresh", "epic_code"],  // signing out forgets the saved session
});
SERVICES.push({
  id: "gog", name: "GOG", blurb: "Games you own on GOG, with hours and last played from GOG Galaxy",
  ready: (s) => s.gog_refresh.set || !!state.settings?.gog_galaxy,
  steps: () => {
    const s = state.settings.settings, galaxy = !!state.settings.gog_galaxy;
    return [
      galaxy
        ? `<p class="ubi-status ok">GOG Galaxy is installed on this PC: ${APP} reads your GOG games, hours played and last played from it at every sync. Nothing to set up.${s.gog_refresh.set ? " You're also signed in to GOG, which adds any game Galaxy hasn't listed." : " Signing in below is optional: it adds games Galaxy hasn't listed yet."}</p>`
        : `<p class="ubi-status${s.gog_refresh.set ? " ok" : ""}">${s.gog_refresh.set ? `Signed in to GOG. ${APP} reads your games at every sync. Hours played and last played only come from GOG Galaxy, which isn't on this PC.` : `GOG Galaxy isn't installed on this PC. Sign in to GOG instead (hours played need Galaxy).`}</p>`,
      `Open <a href="https://auth.gog.com/auth?client_id=46899977096215655&redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient&response_type=code&layout=client2" target="_blank" rel="noopener">GOG's sign-in page</a> and sign in.`,
      `You land on a nearly blank page. Copy its whole address from the address bar (it ends with a code), paste it below and choose Save and test straight away: the code works once, for a few minutes. ${APP} stays signed in after that, as long as you sync every few weeks.`,
    ];
  },
  fields: [["gog_code", "Sign-in address or code", true]],
  clears: ["gog_refresh", "gog_code"],
});
SERVICES.push({
  id: "xbox", name: "Xbox and XGP", blurb: "Games you've played on Xbox or PC, playtime, last played",
  ready: (s) => s.xbl_key.set,
  steps: [
    `Go to <a href="https://xbl.io" target="_blank" rel="noopener">xbl.io</a> (OpenXBL, free) and choose <b>Login with Xbox Live</b>, using your Microsoft account.`,
    `On your <a href="https://xbl.io/profile" target="_blank" rel="noopener">OpenXBL profile</a>, create an API key under <b>API Keys</b> and copy it.`,
    `Paste it below. The XGP (Xbox PC Game Pass) and PS+ (PlayStation Plus Extra) lists (what's included, new and leaving soon) update on every sync and don't need anything set up.`,
  ],
  fields: [["xbl_key", "OpenXBL API key", true]],
});
// The "Send to <app>" bookmark for Ubisoft Connect. Ubisoft's sign-in only answers
// real browsers now, so this runs on connect.ubisoft.com in your own browser, where you're
// signed in: it reads your games and their stats cards there, then opens the app with them.
// Self-contained (it runs on Ubisoft's page, not here).
function ubiBookmarklet() {
  const GS = "__GS__", APPN = "__APP__", A = "b8fde481-327d-4031-85ce-7c10a202a700", U = "https://public-ubiservices.ubi.com";
  const say = (t) => { let b = document.getElementById("gs-note"); if (!b) { b = document.createElement("div"); b.id = "gs-note";
    b.style.cssText = "position:fixed;z-index:2147483647;left:50%;top:20px;transform:translateX(-50%);background:#1c1828;color:#fff;font:600 15px system-ui;padding:12px 18px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.5);border:1px solid #ff5c9e";
    document.body.appendChild(b); } b.textContent = t; };
  (async () => {
    const LOGIN = "https://connect.ubisoft.com/login?appId=" + A + "&lang=en-US";
    if (!/(^|\.)ubisoft\.com$/.test(location.hostname)) {
      if (confirm("This bookmark works on Ubisoft's website. Go there now? Once it has loaded (sign in if asked), click the bookmark again.")) location.assign(LOGIN);
      return;
    }
    const info = {};
    const F = ["ticket", "rememberMeTicket", "sessionId", "userId", "profileId", "nameOnPlatform", "username", "expiration"];
    // The sign-in lives in different places on Ubisoft's sites: PROD* keys on the login pages,
    // reduxPersist:AuthenticationStore (nested) on the Connect web app. Look through all of them.
    const dig = (v, d) => { if (!v || typeof v !== "object" || d > 5) return;
      for (const f of F) if (typeof v[f] === "string" && v[f] && !info[f]) info[f] = v[f];
      for (const k in v) dig(v[k], d + 1); };
    for (const st of [localStorage, sessionStorage]) for (let i = 0; i < st.length; i++) {
      const k = st.key(i); if (!/PROD|auth|login|session|user|remember|ticket/i.test(k)) continue;
      try { dig(JSON.parse(st.getItem(k)), 0); } catch (e) { /* not JSON */ }
    }
    if (!info.ticket && !info.rememberMeTicket) {
      if (confirm(APPN + " couldn't find your Ubisoft sign-in on this page. Open Ubisoft Connect's sign-in page? Sign in there, then click the bookmark again.")) location.assign(LOGIN);
      return;
    }
    say(APPN + ": reading your Ubisoft games…");
    // A sign-in pass belongs to the Ubisoft site that issued it, so try this site's app id first.
    const ids = [];
    const html = document.documentElement.outerHTML;
    for (const m of html.matchAll(/appId=([0-9a-f-]{36})/g)) ids.push(m[1]);
    ids.push("aa88e512-9395-457d-bcb9-8d59c23f88c9", A, "314d4fef-e568-454a-ae06-43e3bece12a6");
    const q = "query AllGames { viewer { id ...ownedGamesList } } fragment gameProps on Game { id spaceId name } fragment ownedGameProps on Game { ...gameProps viewer { meta { id ownedPlatformGroups { id name type } } } } fragment ownedGamesList on User { ownedGames: games(filterBy: {isOwned: true}) { totalCount nodes { ...ownedGameProps } } }";
    let s = null, AH = null, app = null, nodes = null;
    const tried = [];
    for (const id of [...new Set(ids)]) {
      const H = { "Ubi-AppId": id, "Content-Type": "application/json" };
      let ss = info;
      if (info.rememberMeTicket) {
        try {
          const r = await fetch(U + "/v3/profiles/sessions", { method: "POST", headers: { ...H, Authorization: "rm_v1 t=" + info.rememberMeTicket }, body: JSON.stringify({ rememberMe: true }) });
          if (r.ok) ss = { ...info, ...(await r.json()) };
        } catch (e) { /* keep the page's own pass */ }
      }
      if (!ss.ticket) continue;
      const h = { ...H, Authorization: "Ubi_v1 t=" + ss.ticket, "Ubi-SessionId": ss.sessionId };
      const gr = await fetch(U + "/v1/profiles/me/uplay/graphql", { method: "POST", headers: h, body: JSON.stringify({ operationName: "AllGames", variables: { owned: true }, query: q }) });
      tried.push(gr.status);
      if (!gr.ok) continue;
      const j = await gr.json();
      const n = j?.data?.viewer?.ownedGames?.nodes;
      if (!n) { tried.push("no list"); continue; }
      s = { ...ss, userId: ss.userId || j.data.viewer.id || info.profileId }; AH = h; app = id; nodes = n; break;
    }
    if (!nodes) { say(""); alert("Ubisoft didn't share your games (" + tried.join(", ") + "). Tell Claude these numbers."); return; }
    const games = nodes.map((n) => ({ id: n.id, spaceId: n.spaceId, name: n.name,
      types: (n.viewer?.meta?.ownedPlatformGroups || []).flat().map((p) => p && p.type).filter(Boolean), cards: [] }));
    let done = 0;
    for (let i = 0; i < games.length; i += 4) {
      await Promise.all(games.slice(i, i + 4).map(async (g) => {
        if (!g.spaceId) return;
        try {
          const r = await fetch(U + "/v1/profiles/" + s.userId + "/statscard?spaceId=" + g.spaceId, { headers: { ...AH, "Ubi-RequestedPlatformType": "uplay", "Ubi-LocaleCode": "en-US" } });
          if (r.ok) g.cards = ((await r.json()).Statscards || []).filter((c) => c.format === "LongTimespan" || c.lastModified)
            .map((c) => ({ statName: c.statName, displayName: c.displayName, format: c.format, unit: c.unit, value: c.value, lastModified: c.lastModified }));
        } catch (e) { /* no stats for this one */ }
        say(APPN + ": reading your Ubisoft games… " + (++done) + " of " + games.length);
      }));
    }
    const out = { v: 1, app, user: s.userId, name: s.nameOnPlatform || info.nameOnPlatform || info.username, ticket: s.ticket, session: s.sessionId,
      remember: s.rememberMeTicket || info.rememberMeTicket, expiration: s.expiration, games };
    say(APPN + ": opening " + APPN + "…");
    location.assign(GS + "/#ubisoft=" + encodeURIComponent(JSON.stringify(out)));
  })().catch((e) => { say(""); alert("Something went wrong: " + e.message); });
}
const ubiBookmarkHref = () => "javascript:" + encodeURIComponent("(" + ubiBookmarklet.toString().replace("__GS__", location.origin).replace("__APP__", APP.replace(/["\\]/g, "")) + ")()");

SERVICES.push({
  id: "ubisoft", name: "Ubisoft Connect", blurb: "PC games you own, playtime and last played",
  ready: (s) => !!s.ubi_user,
  // Signing in happens once, in your browser (Ubisoft only lets real browsers sign in, and it
  // won't send a sign-in back to a local app); after that every sync refreshes by itself.
  steps: () => {
    const s = state.settings.settings;
    const signedIn = !!s.ubi_user;
    const auto = !!s.ubi_remember?.set;
    const last = Number(s.ubi_refreshed_at || 0);
    const status = !signedIn ? ""
      : auto ? `<p class="ubi-status ok">Signed in${s.ubi_name ? ` as <b>${esc(s.ubi_name)}</b>` : ""}. ${APP} refreshes your Ubisoft games, hours and last played by itself at every sync${last ? ` (last refreshed ${esc(relDate(last).toLowerCase())})` : ""}.</p>`
      : `<p class="ubi-status warn">Signed in${s.ubi_name ? ` as <b>${esc(s.ubi_name)}</b>` : ""}, but without a "stay signed in" pass, so ${APP} can't refresh by itself${last ? ` (last refreshed ${esc(relDate(last).toLowerCase())})` : ""}. Sign in once more, with <b>Remember me</b> ticked.</p>`;
    const how = [
      `Drag this button to your browser's bookmarks bar (drag it, don't click it): <a class="btn small bookmarklet" href="__UBI_BOOKMARK__" onclick="event.preventDefault(); toast('Drag the button to your bookmarks bar instead of clicking it')">Send to ${APP}</a>`,
      `Open <a href="https://connect.ubisoft.com/login?appId=b8fde481-327d-4031-85ce-7c10a202a700&lang=en-US" target="_blank" rel="noopener">connect.ubisoft.com</a>, sign in with <b>Remember me</b> ticked (two-step codes work as usual), then click the bookmark on that page. It hands your games and the "stay signed in" pass to ${APP}, which refreshes by itself from then on.`,
    ];
    return signedIn && auto ? [status] : signedIn ? [status, ...how] : how;
  },
  fields: [],
  clears: ["ubi_remember", "ubi_ticket", "ubi_session", "ubi_user"],
});
SERVICES.push({
  id: "battlenet", name: "Battle.net", blurb: "Blizzard and Activision games you own, last played on this PC (no playtime: Blizzard doesn't share it)",
  ready: (s) => s.bnet_games.set,
  steps: [
    `Sign in at <a href="https://account.battle.net" target="_blank" rel="noopener">account.battle.net</a> in your browser.`,
    `In the same browser, open <a href="https://account.battle.net/api/games-and-subs" target="_blank" rel="noopener">account.battle.net/api/games-and-subs</a>. You'll see a block of text starting with <code>{</code>. Select all of it (Ctrl+A), copy it and paste it below.`,
    `Optional, for older games like Diablo II or Warcraft III: also open <a href="https://account.battle.net/api/classic-games" target="_blank" rel="noopener">account.battle.net/api/classic-games</a> and paste its text after the first one, in the same box.`,
    `Paste again whenever you get a new Blizzard game. Last-played dates come from the Battle.net app on this PC, by themselves.`,
  ],
  fields: [["bnet_games", "Games list (pasted)", true]],
});
SERVICES.push({
  id: "opencritic", name: "Critic scores: OpenCritic", blurb: "Optional. Top critic average, % recommended and tier",
  ready: (s) => s.opencritic_key.set,
  steps: [
    `Create a free account at <a href="https://rapidapi.com" target="_blank" rel="noopener">rapidapi.com</a>.`,
    `Open the <a href="https://rapidapi.com/opencritic-opencritic-default/api/opencritic-api/pricing" target="_blank" rel="noopener">OpenCritic API</a> and subscribe to the free <b>Basic</b> plan.`,
    `Copy your <b>X-RapidAPI-Key</b> from the API's page and paste it below. ${APP} stays well inside the free plan: at most 180 lookups a day, and it stops early if RapidAPI reports that only a tenth of your allowance is left. A big library fills in over a few days, by itself while ${APP} is open.`,
  ],
  fields: [["opencritic_key", "RapidAPI key", true]],
  extra: () => ocDaily(),
});

// OpenCritic: the daily allowance is read by itself while the app is open (Settings › OpenCritic).
function ocDaily() {
  const oc = state.settings.oc;
  if (!oc) return "";
  const at = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  let note;
  if (!oc.left) note = "Every game has been looked up. New games get their scores by themselves.";
  else {
    const left = `${oc.left.toLocaleString()} game${oc.left === 1 ? "" : "s"} still to look up`;
    const today = oc.used_today ? ` · ${oc.used_today} of ${oc.daily} lookups used today` : "";
    const next = !oc.auto ? ". They're looked up when you sync or use Refresh assets."
      : oc.paused || !oc.requests ? `. The next ${oc.daily} start by themselves after ${at(oc.next_day)}, while ${APP} is open.`
      : oc.cooling_until ? `. OpenCritic asked for a short break: carrying on by itself after ${at(oc.cooling_until)}.`
      : `. Carrying on by itself in the background.`;
    note = left + today + next;
  }
  return `<div class="oc-daily">
    <label class="switch"><input type="checkbox" id="oc-auto" ${oc.auto ? "checked" : ""}><span>Look up scores by itself each day while ${APP} is open</span></label>
    <p class="source-note">${note}</p></div>`;
}

const LATER = [
  ["Nintendo Switch and Switch 2", "Import from a screen recording (Sync › Nintendo Switch)"],
];

async function loadSettings() {
  state.settings = await api("/api/settings");
  return state.settings;
}

// One service's steps, fields and Save and test (Settings and the first-run setup share it).
function serviceBody(svc) {
  const s = state.settings.settings;
  return `<div class="body">
        ${(() => { const steps = typeof svc.steps === "function" ? svc.steps() : svc.steps;
          const status = steps.filter((x) => x.startsWith("<p class=\"ubi-status")), rest = steps.filter((x) => !status.includes(x));
          return status.join("") + (rest.length ? `<ol>${rest.map((x) => `<li>${x.replace("__UBI_BOOKMARK__", esc(ubiBookmarkHref()))}</li>`).join("")}</ol>` : ""); })()}
        ${svc.fields.map(([key, label, secret]) => `
          <label class="field"><span>${label}</span>
            <input type="text" class="${secret ? "secret" : ""}" name="${key}" autocomplete="off" spellcheck="false" data-1p-ignore data-lpignore="true" data-bwignore
              value="${secret ? "" : esc(s[key] || "")}" placeholder="${secret && s[key].set ? `Saved (${esc(s[key].hint)}). Paste a new one to replace it.` : ""}">
          </label>`).join("")}
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <button class="btn small" data-save="${svc.id}">${svc.fields.length ? "Save and test" : "Check"}</button>
          ${(svc.clears || []).some((k) => s[k]?.set) ? `<button class="link" data-clear="${svc.id}">Sign out</button>`
            : svc.fields.some(([k, , sec]) => sec && s[k].set) ? `<button class="link" data-clear="${svc.id}">${svc.id === "battlenet" ? "Remove saved list" : "Remove saved key"}</button>` : ""}
          <span class="test-result" data-result="${svc.id}"></span>
        </div>
        ${svc.extra && svc.ready(s) ? svc.extra() : ""}
      </div>`;
}

function renderSettings(openId) {
  const s = state.settings.settings;
  const body = $("#settings-body");
  body.innerHTML = updatesBlock() + displayBlock() + hiddenTypesSection(openId) + subsSection(openId) + shortcutsSection(openId) + SERVICES.map((svc) => {
    const ok = svc.ready(s);
    return `<details class="service" data-svc="${svc.id}" ${openId === svc.id || (!openId && !ok && svc.id !== "sgdb" && !body.dataset.seen) ? "open" : ""}>
      <summary><div><h3>${svc.name}</h3><p>${svc.blurb}</p></div><span class="state ${ok ? "ok" : "todo"}">${ok ? "Connected" : "Not set up"}</span></summary>
      ${serviceBody(svc)}
    </details>`;
  }).join("") + `
    <details class="service" ${openId === "hltb" ? "open" : ""}>
      <summary><div><h3>Time to beat: HowLongToBeat</h3><p>Used first when available, otherwise IGDB's numbers</p></div>
      <span class="state ${state.settings.hltb_installed ? "ok" : "later"}">${state.settings.hltb_installed ? "Available" : "Not installed"}</span></summary>
      <div class="body">
        ${state.settings.hltb_installed
          ? `<label class="switch"><input type="checkbox" id="hltb-toggle" ${s.hltb_enabled !== false ? "checked" : ""}><span>Use HowLongToBeat for time-to-beat numbers</span></label>`
          : `<p class="source-note">The launcher installs it automatically when it can. If it couldn't (for example without internet on first start), restart ${APP} to try again. Until then, IGDB's time-to-beat data is used.</p>`}
      </div>
    </details>
    ${LATER.map(([n, why]) => `<div class="service later"><summary style="cursor:default"><div><h3>${n}</h3></div><span class="state later">${why}</span></summary></div>`).join("")}`;
  body.dataset.seen = "1";
}

async function openSettings(openId) {
  await loadSettings();
  renderSettings(openId);
  $("#settings-dialog").showModal();
  refreshUpdates();  // a fresh look at GitHub every time Settings opens
}

// Check for a newer release and redraw just the Updates block (the rest of Settings keeps
// whatever you're typing or have open).
async function refreshUpdates() {
  const status = $("#pc-update"), btn = $('#settings-dialog [data-pc="check"]');
  if (status) { status.className = "test-result"; status.textContent = "Checking for updates…"; }
  if (btn) btn.disabled = true;
  await checkUpdate(true);
  const block = $("#settings-dialog .pc-block");
  if (block) block.outerHTML = updatesBlock();
}

// Save and test / Sign out, in Settings or in the first-run setup (rerender redraws that one).
async function onServiceClick(e, rerender) {
  const root = e.currentTarget;
  const save = e.target.closest("[data-save]");
  const clear = e.target.closest("[data-clear]");
  if (save) {
    const id = save.dataset.save;
    const svc = SERVICES.find((x) => x.id === id);
    const panel = save.closest(".service");
    const payload = {};
    for (const [key] of svc.fields) {
      const v = $(`[name=${key}]`, panel).value.trim();
      payload[key] = v;
    }
    const out = $(`[data-result=${id}]`, panel);
    out.className = "test-result";
    out.textContent = "Checking…";
    save.disabled = true;
    try {
      await api("/api/settings", { method: "POST", body: payload });
      const res = await api(`/api/test/${id}`, { method: "POST", body: {} });
      await loadSettings();
      rerender(id);
      const out2 = $(`[data-result=${id}]`, root);
      out2.className = "test-result " + (res.ok ? "ok" : "err");
      out2.textContent = res.message;
      if (!state.games.length) render();
    } catch (err) {
      out.className = "test-result err";
      out.textContent = err.message;
    } finally { save.disabled = false; }
  }
  if (clear) {
    const svc = SERVICES.find((x) => x.id === clear.dataset.clear);
    const keys = svc.clears || svc.fields.filter(([, , secret]) => secret).map(([key]) => key);
    for (const key of keys) await api(`/api/settings/clear/${key}`, { method: "POST", body: {} });
    await loadSettings();
    rerender(svc.id);
  }
}
$("#settings-body").addEventListener("click", (e) => {
  if (e.target.closest("[data-pc]")) { onThisPc(e.target.closest("[data-pc]")); return; }
  onServiceClick(e, renderSettings);
});
$("#settings-body").addEventListener("change", async (e) => {
  if (e.target.dataset.sub) { await saveSub(e.target); renderSettings("subs"); return; }
  const display = { "set-hide-vr": ["hide_vr", (t) => t.checked], "set-hide-ea": ["hide_early_access", (t) => t.checked],
    "set-start": ["start_page", (t) => t.value], "set-shelves": ["curator_shelves", (t) => t.value],
    "set-previews": ["curator_previews", (t) => t.checked] }[e.target.id];
  if (display) {
    await api("/api/settings", { method: "POST", body: { [display[0]]: display[1](e.target) } });
    await loadSettings();
    renderSettings(["set-start", "set-shelves", "set-previews"].includes(e.target.id) ? undefined : "hidden-types");
    renderAll();
    return;
  }
  if (e.target.id === "set-channel") {
    await api("/api/settings", { method: "POST", body: { update_channel: e.target.value } });
    await loadSettings();
    await refreshUpdates();
    return;
  }
  if (e.target.id === "oc-auto") {
    await api("/api/settings", { method: "POST", body: { oc_auto: e.target.checked } });
    await loadSettings();
    renderSettings("opencritic");
    return;
  }
  if (e.target.id === "hltb-toggle") {
    await api("/api/settings", { method: "POST", body: { hltb_enabled: e.target.checked } });
    await loadSettings();
  }
});

/* ------------------------------------------------------------ desktop app: window, updates, setup */

// state.desktop: {version, windows, packaged, app, shortcuts, setup_done, shortcuts_asked}
async function loadDesktop() {
  try { state.desktop = await api("/api/desktop"); } catch { state.desktop = {}; }
  return state.desktop;
}

// "Still here": desktop mode quits about a minute after the last one (the window was closed).
function startHeartbeat() {
  const beat = () => api("/api/ping").catch(() => {});
  beat();
  setInterval(beat, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) beat(); });
  if (state.desktop?.app) watchWindow();
}

// Remember the app window's size and place (and whether it's maximised) for next time.
function watchWindow() {
  let last = "", timer;
  const report = () => {
    const max = outerWidth >= screen.availWidth - 2 && outerHeight >= screen.availHeight - 2;
    const rect = { x: screenX, y: screenY, w: outerWidth, h: outerHeight, max };
    const key = JSON.stringify(rect);
    if (key === last || document.hidden) return;
    last = key;
    api("/api/window", { method: "POST", body: rect }).catch(() => {});
  };
  addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(report, 800); });
  setInterval(report, 4000);  // moving a window has no event: check now and then
  setTimeout(report, 1500);
}

// In the app window, links to other sites open in your normal browser (where you're signed in).
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || !state.desktop?.app || e.defaultPrevented) return;
  const href = a.href;
  if (!/^https?:/i.test(href) || new URL(href).host === location.host) return;
  e.preventDefault();
  api("/api/open", { method: "POST", body: { url: href } }).catch((err) => toast(err.message, true));
});

// Updates: a quiet bar under the header when a newer version is out.
async function checkUpdate(force = false) {
  let u;
  try { u = await api(`/api/update${force ? "?force=1" : ""}`); } catch { return null; }
  state.update = u;
  renderUpdateBar();
  return u;
}
function renderUpdateBar() {
  const u = state.update, bar = $("#update-bar");
  if (!u || !u.newer || state.updateDismissed === u.latest) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = u.busy
    ? `<span>Updating to ${APP} ${esc(u.latest)}: ${esc((u.stage || "working").toLowerCase())}…</span>`
    : `<span><b>${APP} ${esc(u.latest)}</b>${u.beta ? BETA_TAG : ""} is out (you have ${esc(u.current)}).</span>
       <button class="link" data-upd="notes">What's new</button>
       ${u.can_install ? `<button class="btn small" data-upd="install">Update now</button>` : `<a class="btn small" href="${esc(u.url)}" target="_blank" rel="noopener">Download</a>`}
       ${u.error ? `<span class="err">${esc(u.error)}</span>` : ""}
       <button class="link" data-upd="later">Later</button>`;
}
$("#update-bar").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-upd]");
  if (!b) return;
  if (b.dataset.upd === "later") { state.updateDismissed = state.update?.latest; renderUpdateBar(); return; }
  if (b.dataset.upd === "notes") { showReleaseNotes(); return; }
  startUpdate();
});

// Install the update: from the bar, What's new or Settings. Open windows close so the progress
// (in the bar at the top) is in view; GameShelf reloads itself once the new version is up.
async function startUpdate() {
  if (state.update?.busy) return;
  if (state.sync?.running && !confirm("A sync is running. Update anyway? It stops the sync; the next one picks up where it left off.")) return;
  try {
    state.update = await api("/api/update", { method: "POST", body: {} });
  } catch (err) { toast(err.message, true); return; }
  for (const d of $$("dialog[open]")) d.close();
  state.updateDismissed = null;
  renderUpdateBar();
  scrollTo({ top: 0 });
  // Wait for the app to come back as the new version, then reload.
  const from = state.update.current;
  const poll = setInterval(async () => {
    try {
      const st = await api("/api/update");
      state.update = { ...st, newer: true, busy: st.busy, latest: state.update.latest };
      if (st.error) { clearInterval(poll); state.update.busy = false; renderUpdateBar(); return; }
      renderUpdateBar();
    } catch { /* restarting */ }
    try {
      const p = await api("/api/ping");
      if (p.version && p.version !== from) { clearInterval(poll); location.reload(); }
    } catch { /* not back yet */ }
  }, 1500);
}

// Release notes (GitHub's Markdown, the simple parts): bullets, bold, code, paragraphs.
function notesHTML(md) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of String(md || "").split(/\r?\n/)) {
    const line = raw.trim();
    const item = line.match(/^[-*]\s+(.*)$/);
    if (item) { (list ||= []).push(`<li>${inline(item[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (/^#+\s/.test(line)) out.push(`<h4>${inline(line.replace(/^#+\s*/, ""))}</h4>`);
    else if (line) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("") || `<p class="source-note">No notes for this one.</p>`;
}
function showReleaseNotes() {
  const u = state.update || {};
  const changes = u.changes && u.changes.length ? u.changes : [{ version: u.latest, date: u.date, notes: u.notes }];
  $("#notes-body").innerHTML = `
    <p class="lede">You have ${esc(APP)} ${esc(u.current || "")}. Here's what's changed since:</p>
    ${changes.map((c) => `<section class="release">
      <h3>${esc(APP)} ${esc(c.version)}${c.beta ? BETA_TAG : ""}<span>${c.date ? esc(fmtDate(Date.parse(c.date) / 1000)) : ""}</span></h3>
      <div class="release-notes">${notesHTML(c.notes)}</div>
    </section>`).join("")}
    <div class="dialog-actions">
      <a class="link" href="${esc(u.url || "")}" target="_blank" rel="noopener">See it on GitHub</a>
      <span class="spacer"></span>
      <button class="btn ghost" data-close-dialog>Close</button>
      ${u.can_install ? `<button class="btn" id="notes-install">Update now</button>` : ""}
    </div>`;
  $("#notes-dialog").showModal();
}
$("#notes-body").addEventListener("click", (e) => {
  if (e.target.id === "notes-install") {
    startUpdate();
  }
});

// Settings, at the top: Updates (always visible) and, on Windows, Shortcuts.
const BETA_TAG = `<span class="beta-tag">Beta</span>`;
const isBetaVer = (v) => /-?(beta|b)\.?\d+$/i.test(v || "");
function updatesBlock() {
  const d = state.desktop || {}, u = state.update;
  const beta = (state.settings?.settings?.update_channel || "stable") === "beta";
  const status = !u ? "" : u.newer ? `${esc(APP)} ${esc(u.latest)}${u.beta ? BETA_TAG : ""} is available.` : u.error ? `Couldn't check (${esc(u.error)})` : "You're up to date.";
  const note = beta
    ? `Test builds of new features, before they reach everyone. They can have rough edges: your library is copied to <code>data/backups</code> before every new version.`
    : isBetaVer(d.version) ? `You're on a test build. You'll stay on it until the next stable version is out, then get that as usual.`
    : "";
  return `<div class="pc-block">
    <div class="pc-row">
      <div><h3>Updates</h3><p>You have ${esc(APP)} ${esc(d.version || "")}${isBetaVer(d.version) ? BETA_TAG : "."}</p></div>
      <span class="spacer"></span>
      ${u?.newer ? "" : `<span class="test-result" id="pc-update">${status}</span>`}
      <button class="btn small ghost" data-pc="check">Check for updates</button>
    </div>
    ${u?.newer ? `<div class="upd-new">
      <span class="test-result ok" id="pc-update">${status}</span>
      <span class="spacer"></span>
      <button class="btn small ghost" data-pc="notes">What's new</button>
      ${u.can_install ? `<button class="btn small" data-pc="install">Update now</button>` : ""}
    </div>` : ""}
    <div class="pc-row channel-row">
      <div><p><b>Get updates from</b></p></div>
      <select id="set-channel" aria-label="Get updates from">
        <option value="stable" ${beta ? "" : "selected"}>Stable</option>
        <option value="beta" ${beta ? "selected" : ""}>Beta: try new features early</option>
      </select>
      ${note ? `<p class="source-note channel-note">${note}</p>` : ""}
    </div>
    <p class="source-note">Your library, keys and settings live in the <code>data</code> folder next to the app. Updates never touch it.</p>
  </div>`;
}
// Settings › Display: the tab Sunbeam opens on (always visible, under Updates).
function displayBlock() {
  const s = state.settings.settings;
  return `<div class="pc-block">
    <div class="pc-row">
      <div><h3>Display</h3><p>The tab ${esc(APP)} opens on.</p></div>
      <span class="spacer"></span>
      <select id="set-start" aria-label="Open ${esc(APP)} on">${[["curator", CURATOR], ["library", "Library"], ["wishlist", "Wishlist"], ["year", "Stats"]].map(([v, l]) =>
        `<option value="${v}" ${(s.start_page || "curator") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
    </div>
    <div class="pc-row display-row">
      <div><p>Your shelves in ${esc(CURATOR)}</p></div>
      <span class="spacer"></span>
      <select id="set-shelves" aria-label="Your shelves in ${esc(CURATOR)}">${[["cards", "Cards"], ["rows", "Rows"]].map(([v, l]) =>
        `<option value="${v}" ${(s.curator_shelves || "cards") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
    </div>
    <div class="pc-row display-row">
      <label class="switch"><input type="checkbox" id="set-previews" ${previewsOn() ? "checked" : ""}><span>Previews in ${esc(CURATOR)}: point at a cover for its screenshots and summary</span></label>
    </div>
  </div>`;
}
// Settings › Hidden game types: kinds of games kept out of the library and Curator.
function hiddenTypesSection(openId) {
  const n = [hideVR(), hideEA()].filter(Boolean).length;
  return `<details class="service" ${openId === "hidden-types" ? "open" : ""}>
    <summary><div><h3>Hidden game types</h3><p>Kinds of games kept out of the library and ${CURATOR}</p></div>
    <span class="state ${n ? "ok" : "later"}">${[hideVR() ? "VR" : "", hideEA() ? "Early access" : ""].filter(Boolean).join(", ") || "None"}</span></summary>
    <div class="body display-set">
      <label class="switch"><input type="checkbox" id="set-hide-vr" ${hideVR() ? "checked" : ""}><span>Hide VR games</span></label>
      <label class="switch"><input type="checkbox" id="set-hide-ea" ${hideEA() ? "checked" : ""}><span>Hide early access games</span></label>
      <p class="source-note">Hidden ones can still be shown in the library from Filters › Type. Early access games stay on the Wishlist page in their own group, their full release still shows in ${CURATOR}, and they're never marked completed automatically.</p>
    </div>
  </details>`;
}
function subsSection(openId) {
  return `<details class="service" ${openId === "subs" ? "open" : ""}>
    <summary><div><h3>Subscriptions</h3><p>XGP and PS+ games you can play</p></div>
    <span class="state ${["gamepass", "psplus"].some(subscribed) ? "ok" : "later"}">${["gamepass", "psplus"].filter(subscribed).map((x) => SUB_SHORT[x]).join(" and ") || "None"}</span></summary>
    <div class="body display-set">${subSwitches()}
      <p class="source-note">Games from a service you don't have stay out of your library and ${CURATOR}, and wishlist games say "Not subscribed" for it.</p>
    </div>
  </details>`;
}
function shortcutsSection(openId) {
  const d = state.desktop || {};
  if (!d.windows) return "";
  const sc = d.shortcuts || {};
  const n = (sc.desktop ? 1 : 0) + (sc.startmenu ? 1 : 0);
  return `<details class="service" ${openId === "shortcuts" ? "open" : ""}>
    <summary><div><h3>Shortcuts</h3><p>Start ${esc(APP)} from the desktop or the Start menu</p></div>
      <span class="state ${n ? "ok" : "later"}">${n === 2 ? "Both added" : n ? "One added" : "None yet"}</span></summary>
    <div class="body">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn small ghost" data-pc="desktop" ${sc.desktop ? "disabled" : ""}>${sc.desktop ? "✓ Desktop shortcut" : "Add a desktop shortcut"}</button>
        <button class="btn small ghost" data-pc="startmenu" ${sc.startmenu ? "disabled" : ""}>${sc.startmenu ? "✓ In the Start menu" : "Add to the Start menu"}</button>
        <span class="test-result" id="pc-shortcut"></span>
      </div>
    </div>
  </details>`;
}
async function onThisPc(b) {
  if (b.dataset.pc === "check") { await refreshUpdates(); return; }
  if (b.dataset.pc === "notes") { showReleaseNotes(); return; }
  if (b.dataset.pc === "install") { startUpdate(); return; }
  try {
    await api("/api/desktop/shortcuts", { method: "POST", body: { which: [b.dataset.pc] } });
    await loadDesktop();
    renderSettings("shortcuts");
    toast(b.dataset.pc === "desktop" ? "Shortcut added to your desktop" : `${APP} is in the Start menu`);
  } catch (err) { $("#pc-shortcut").className = "test-result err"; $("#pc-shortcut").textContent = err.message; }
}

/* First-run setup: one thing at a time, everything skippable, the same steps as Settings. */
const SETUP_STORES = [
  ["steam", "Steam"], ["playstation", "PlayStation"], ["xbox", "Xbox and XGP"], ["epic", "Epic Games Store"], ["gog", "GOG"],
  ["ubisoft", "Ubisoft Connect"], ["battlenet", "Battle.net"], ["switch", "Nintendo Switch"],
];
const setup = { i: 0, stores: [], subs: {} };

function setupSteps() {
  const d = state.desktop || {};
  return ["welcome", "igdb", "stores", ...setup.stores.filter((x) => x !== "switch"), "subs", "extras",
    ...(d.windows && d.packaged ? ["shortcuts"] : []), "done"];
}

function renderSetup() {
  const steps = setupSteps();
  setup.i = Math.min(setup.i, steps.length - 1);
  const step = steps[setup.i];
  const s = state.settings.settings;
  const svc = SERVICES.find((x) => x.id === step);
  const ready = svc ? svc.ready(s) : true;
  let body = "";
  if (step === "welcome") {
    body = `<h2 class="setup-h">Welcome to ${APP}</h2>
      <p class="lede">All your games from every store in one place, with covers, details, playtime and how long each one takes to beat. Plus a few friendly suggestions for what to play next.</p>
      <p>Setup takes about five minutes: connect a free game-info service, then the stores you use. Skip anything you like; it's all in Settings later.</p>
      <p class="source-note">Everything stays on this PC, in the <code>data</code> folder next to ${APP}. Your keys are never sent anywhere except to the service they belong to.</p>`;
  } else if (step === "stores") {
    body = `<h2 class="setup-h">Which stores do you use?</h2>
      <p>Pick every one you'd like in ${APP}. You'll set them up one by one next.</p>
      <div class="setup-stores">${SETUP_STORES.map(([id, name]) => `<label class="chip ${setup.stores.includes(id) ? "on" : ""}">
        <input type="checkbox" value="${id}" ${setup.stores.includes(id) ? "checked" : ""}>${esc(name)}</label>`).join("")}</div>
      ${setup.stores.includes("switch") ? `<p class="source-note">Nintendo doesn't share libraries online: once setup is done, use <b>Sync › Nintendo Switch</b> to import a screen recording of your Play History from the Nintendo Store phone app (iPhone or Android).</p>` : ""}`;
  } else if (step === "subs") {
    body = `<h2 class="setup-h">Subscriptions</h2>
      <p>Do you have either of these? ${APP} then shows the games they include alongside your own, and in its suggestions.</p>
      <div class="setup-checks">${subSwitches((sv) => setup.subs[sv])}</div>
      <p class="source-note">You can change this any time in Settings › Subscriptions.</p>`;
  } else if (step === "extras") {
    const extra = (id) => { const x = SERVICES.find((v) => v.id === id); const ok = x.ready(s);
      return `<details class="service" data-svc="${id}"><summary><div><h3>${x.name}</h3><p>${x.blurb}</p></div><span class="state ${ok ? "ok" : "later"}">${ok ? "Connected" : "Optional"}</span></summary>${serviceBody(x)}</details>`; };
    body = `<h2 class="setup-h">Nice extras (optional)</h2>
      <p>Both are free. Skip them now and add them in Settings whenever you like.</p>
      ${extra("sgdb")}${extra("opencritic")}`;
  } else if (step === "shortcuts") {
    body = `<h2 class="setup-h">Shortcuts</h2>
      <p>Where would you like to find ${APP}?</p>
      <div class="setup-checks">
        <label class="switch"><input type="checkbox" id="sc-desktop" checked><span>A shortcut on the desktop</span></label>
        <label class="switch"><input type="checkbox" id="sc-startmenu" checked><span>In the Start menu (type “${APP}” in Start)</span></label>
      </div>
      <p class="source-note">${APP} opens in its own window. While it's running, its icon by the clock reopens it or quits; it also stops by itself a minute after you close the window.</p>
      <span class="test-result" id="sc-result"></span>`;
  } else if (step === "done") {
    const any = SERVICES.some((x) => !["igdb", "sgdb", "opencritic"].includes(x.id) && x.ready(s));
    body = `<h2 class="setup-h">${any ? "All set" : "Almost there"}</h2>
      <p>${any ? "Sync pulls in your games, playtime and wishlists, then fetches covers, details and time to beat. The first one can take a while for a big library (covers appear as it goes); after that it runs by itself once a day."
        : "No store is connected yet. Open Settings (the gear, top right) whenever you're ready."}</p>`;
  } else if (svc) {
    body = `<h2 class="setup-h">${esc(svc.name)}</h2><p>${esc(svc.blurb)}.</p>
      ${step === "igdb" ? `<p class="source-note">This one's worth it: it's where game details, covers and times come from. It needs a free Twitch developer app, which takes about three minutes.</p>` : ""}
      <div class="service open" data-svc="${svc.id}">${serviceBody(svc)}</div>`;
  }
  const last = setup.i === steps.length - 1;
  const nextLabel = step === "welcome" ? "Get started" : step === "shortcuts" ? "Next" : last ? (syncSources().some((x) => x.ok) ? "Sync now" : "Done")
    : svc && !ready ? "Skip for now" : "Next";
  $("#setup-body").innerHTML = `
    <div class="setup-progress" aria-hidden="true">${steps.map((_, i) => `<i class="${i < setup.i ? "done" : i === setup.i ? "on" : ""}"></i>`).join("")}</div>
    <div class="setup-step">${body}</div>
    <footer class="setup-foot">
      ${setup.i > 0 && !last ? `<button class="btn ghost" data-setup="back">Back</button>` : `<span></span>`}
      <button class="btn ${svc && !ready ? "ghost" : ""}" data-setup="next">${nextLabel}</button>
    </footer>`;
}

async function openSetup() {
  await loadSettings();
  // Unticked until you say you have it (an earlier answer is kept).
  setup.subs = { gamepass: state.settings.settings.sub_gamepass === true, psplus: state.settings.settings.sub_psplus === true };
  setup.i = 0;
  renderSetup();
  $("#setup-dialog").showModal();
}

async function finishSetup() {
  await api("/api/settings", { method: "POST", body: { setup_done: true } }).catch(() => {});
  if (state.desktop) state.desktop.setup_done = true;
}

$("#setup-body").addEventListener("click", async (e) => {
  const nav = e.target.closest("[data-setup]");
  if (!nav) { onServiceClick(e, () => renderSetup()); return; }
  const steps = setupSteps(), step = steps[setup.i];
  if (nav.dataset.setup === "back") { setup.i--; renderSetup(); return; }
  if (step === "subs") {
    await api("/api/settings", { method: "POST", body: { sub_gamepass: !!setup.subs.gamepass, sub_psplus: !!setup.subs.psplus } }).catch(() => {});
    await loadSettings();
  }
  if (step === "shortcuts") {
    const which = [["desktop", "#sc-desktop"], ["startmenu", "#sc-startmenu"]].filter(([, sel]) => $(sel).checked).map(([k]) => k);
    nav.disabled = true;
    try { await api("/api/desktop/shortcuts", { method: "POST", body: { which } }); await loadDesktop(); }
    catch (err) { $("#sc-result").className = "test-result err"; $("#sc-result").textContent = err.message; nav.disabled = false; return; }
  }
  if (setup.i === steps.length - 1) {
    $("#setup-dialog").close();  // (closing it marks setup as done)
    if (syncSources().some((x) => x.ok)) openJob("sync");
    return;
  }
  setup.i++;
  renderSetup();
});
$("#setup-body").addEventListener("change", (e) => {
  if (e.target.dataset.sub) { setup.subs[e.target.dataset.sub] = e.target.checked; return; }
  if (e.target.closest(".setup-stores")) {
    setup.stores = $$(".setup-stores input:checked").map((i) => i.value);
    renderSetup();
  }
});
$("#setup-dialog").addEventListener("close", finishSetup);

// The shortcut question on its own, for a library that was set up before (e.g. after moving
// to the Windows download).
function askShortcuts() {
  const d = $("#shortcut-dialog");
  d.showModal();
}
$("#shortcut-dialog").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-sc]");
  if (!b) return;
  const which = b.dataset.sc === "yes" ? [["desktop", "#sc2-desktop"], ["startmenu", "#sc2-startmenu"]].filter(([, sel]) => $(sel).checked).map(([k]) => k) : [];
  try { await api("/api/desktop/shortcuts", { method: "POST", body: { which } }); await loadDesktop(); }
  catch (err) { toast(err.message, true); }
  $("#shortcut-dialog").close();
  if (which.length) toast("Shortcuts added");
});

async function startDesktop() {
  const d = await loadDesktop();
  startHeartbeat();
  checkUpdate(true);  // a fresh look at GitHub every time the app starts
  setInterval(() => checkUpdate(), 6 * 3600 * 1000);  // and again now and then while it stays open
  if (!d.setup_done && !state.games.length) openSetup();
  else if (d.windows && d.packaged && !d.shortcuts_asked) askShortcuts();
}

/* ------------------------------------------------------------ sync */

let pollTimer = null, lastReload = 0;

function syncSources() {
  const s = state.settings?.settings || {};
  return [
    { id: "steam", name: "Steam", ok: s.steam_api_key?.set && s.steam_id },
    { id: "playstation", name: "PlayStation", ok: s.psn_npsso?.set },
    { id: "epic", name: "Epic Games Store", ok: s.epic_refresh?.set },
    { id: "gog", name: "GOG", ok: s.gog_refresh?.set || !!state.settings?.gog_galaxy },
    { id: "xbox", name: "Xbox", ok: s.xbl_key?.set },
    { id: "ubisoft", name: "Ubisoft Connect", ok: !!s.ubi_user },
    { id: "battlenet", name: "Battle.net", ok: s.bnet_games?.set },
  ];
}

const JOB_TITLES = { sync: "Sync your libraries", refresh: "Refresh assets" };
const REFRESH_STEPS = [
  { id: "info", label: "Game details", hint: "Identifies games on IGDB, then description, genres, developers", need: "igdb", unit: "missing" },
  { id: "art", label: "Artwork", hint: "Covers, banners, logos, and which screenshot comes first", unit: "missing" },
  { id: "reviews", label: "Steam user reviews", hint: "Every 14 days, plus Steam versions of console games", unit: "to check" },
  { id: "ttb", label: "Time to beat", hint: "Main, extras and 100% times", unit: "missing" },
  { id: "critics", label: "OpenCritic scores", hint: "Stays well inside the free plan (at most 180 lookups a day); the rest are looked up by themselves on later days", need: "opencritic", unit: "to look up" },
  { id: "completions", label: "Finished games", hint: "Marks games completed from your achievements and trophies. Statuses you set yourself stay put", unit: "to check" },
];

const STEP_ICON = { done: "✓", current: "", upcoming: "", skipped: "–", failed: "!" };

function timelineHTML(items) {
  if (!items || !items.length) return "";
  return `<ol class="timeline">${items.map((t) => {
    let right = "";
    if (t.state === "current" && t.note) right = t.note;
    else if (t.state === "current" && t.total) right = `${t.done.toLocaleString()} of ${t.total.toLocaleString()}`;
    else if (t.state === "current") right = "Working…";
    else if (t.state === "upcoming" && t.total) right = `${t.total.toLocaleString()} to do`;
    else if (t.state === "upcoming") right = "Waiting";
    else if (t.state === "done") right = t.note || (t.total ? `${t.total.toLocaleString()} done` : "Done");
    else right = t.note || "";
    if (t.state === "planned") right = t.note || "";
    return `<li class="${t.state === "planned" ? "upcoming" : t.state}"><i aria-hidden="true">${STEP_ICON[t.state] || ""}</i>
      <div><b>${esc(t.label)}</b>${t.hint ? `<small>${esc(t.hint)}</small>` : ""}</div>
      <span>${esc(right)}</span></li>`;
  }).join("")}</ol>`;
}

function syncPreview() {
  const picked = $$("input[name=src]:checked", $("#sync-body")).map((i) => i.value);
  const names = Object.fromEntries(syncSources().map((x) => [x.id, x.name]));  // one list, so a new store can't be missed
  const est = state.settings?.settings || {};
  const igdb = est.igdb_client_id && est.igdb_client_secret?.set;
  const items = [
    ...picked.map((p) => ({ label: `Import ${names[p]} library`, hint: "Games, playtime, last played", state: "upcoming" })),
    { label: "Update XGP and PS+ lists", hint: "What's included, recently added and leaving soon", state: "upcoming" },
    { label: "Identify new games", hint: igdb ? "Finds each game on IGDB" : "Skipped until IGDB is set up", state: igdb ? "upcoming" : "skipped" },
    { label: "Fetch details for new games", hint: "Description, genres, developers", state: igdb ? "upcoming" : "skipped" },
    { label: "Collect artwork for new games", hint: "Covers, banners, logos, and which screenshot comes first", state: "upcoming" },
    { label: "Read Steam reviews for new games", hint: "Steam's positive-review percentage", state: "upcoming" },
    { label: "Look up time to beat for new games", hint: "Main, extras and 100% times", state: "upcoming" },
    { label: "Read OpenCritic scores for new games", hint: "Top critic average and % recommended", state: est.opencritic_key?.set ? "upcoming" : "skipped", need: "OpenCritic" },
    { label: "Detect finished games", hint: "From achievements and trophies of games you've played since the last check", state: "upcoming" },
  ].map((t) => ({ ...t, state: t.state === "upcoming" ? "planned" : t.state, note: t.state === "skipped" ? `Needs ${t.need || "IGDB"}` : "" }));
  const box = $("#sync-preview");
  if (box) box.innerHTML = picked.length ? timelineHTML(items) : `<p class="source-note">Pick a library to sync.</p>`;
}

function logHTML(st, n) {
  return `<div class="log">${(st.log || []).slice(-n).reverse().map((l) => `<div class="${l.level}">${esc(l.msg)}</div>`).join("")}</div>`;
}

function renderJobDialog() {
  const st = state.sync || {};
  const view = st.running ? st.kind : state.jobView;
  $("#job-title").textContent = JOB_TITLES[view] || JOB_TITLES.sync;
  const body = $("#sync-body");

  if (st.running) {
    const pct = st.overall != null ? Math.floor(100 * st.overall) : null;
    const step = st.step_no && st.step_count > 1 ? `Step ${st.step_no} of ${st.step_count}: ` : "";
    const detail = st.total ? `${step}${esc(st.stage_label)}, ${st.done.toLocaleString()} of ${st.total.toLocaleString()}` : esc(st.stage_label || "Starting");
    const other = state.jobView && state.jobView !== st.kind
      ? `<p class="notice">${st.kind === "sync" ? "A sync is running. You can use Refresh assets once it finishes." : "A refresh is running. You can sync once it finishes."}</p>` : "";
    const full = st.kind === "refresh" && st.mode === "all";
    body.innerHTML = `
      <div class="job-cols">
        <div class="job-main">
          ${other}
          <p class="lede">You can close this and keep browsing. ${st.kind === "sync" ? "New games and art appear as they arrive." : "Updates appear as they arrive."}
            ${full ? `This full refresh is remembered: if ${esc(APP)} closes, it carries on where it stopped next time.` : ""}</p>
          <div class="sync-head"><b>${pct == null ? "Importing your libraries" : `${pct}% done`}</b><span class="source-note">${fmtEta(st.eta_s)}</span></div>
          <div class="progress ${pct == null ? "indeterminate" : ""}"><i style="width:${pct ?? 30}%"></i></div>
          <div class="log-wrap open"><div class="field-label">Activity</div>${logHTML(st, 10)}</div>
        </div>
        <div class="job-side">
          <div class="field-label">Steps</div>
          ${st.timeline && st.timeline.length ? timelineHTML(st.timeline) : `<div class="source-note">${detail}</div>`}
        </div>
      </div>
      <div class="dialog-actions">
        ${full ? `<button class="btn ghost" type="button" data-sync-cancel data-forget="1" title="Stop and forget this full refresh">Cancel refresh</button>
          <button class="btn ghost" type="button" data-sync-cancel title="Stop for now; continue it later from Refresh assets">Pause</button>`
          : `<button class="btn ghost" type="button" data-sync-cancel>Stop</button>`}
        <button class="btn" value="close">Keep browsing</button></div>`;
    return;
  }

  const finished = st.finished_at && st.kind === view
    ? (() => {
        const errs = (st.errors || []).length;
        const outcome = /Stopped early/.test(JSON.stringify(st.log || [])) ? "stopped early" : errs ? `${errs} problem${errs > 1 ? "s" : ""}` : "finished";
        const recent = Date.now() / 1000 - st.finished_at < 120;
        return `<details class="last-run" ${recent || errs ? "open" : ""}><summary>Last ${view === "refresh" ? "refresh" : "sync"}, ${esc(relDate(st.finished_at).toLowerCase())}: ${outcome}</summary>${timelineHTML(st.timeline)}${logHTML(st, 6)}</details>`;
      })() : "";

  if (view === "refresh") {
    const est = state.estimate || {};
    const mode = state.refreshMode || "missing";
    const all = mode === "all";
    const job = est.job;
    body.innerHTML = `
      <div class="job-cols">
      <div class="job-main">
      <p class="lede">Updates details, artwork, reviews and time to beat for games already in your library. Your playtime and the statuses you set aren't touched.</p>
      ${job ? `<div class="job-paused">
        <div><b>A full refresh is ${Math.round((job.progress || 0) * 100)}% done</b><small>Started ${esc(relDate(job.since).toLowerCase())}${job.paused ? ", paused" : ""}. It picks up where it stopped.</small></div>
        <button class="btn small" type="button" id="refresh-resume">Continue</button>
        <button class="link" type="button" id="refresh-forget">Cancel it</button>
      </div>` : ""}
      <div class="sync-sources">
        <label><input type="radio" name="rmode" value="missing" ${all ? "" : "checked"}><span>Fill in what's missing</span><small>Quick</small></label>
        <label><input type="radio" name="rmode" value="all" ${all ? "checked" : ""}><span>Re-fetch for every game</span><small>${(est.games || 0).toLocaleString()} games, slower; carries on across sessions</small></label>
      </div>
      ${libraryChecks()}
      ${!all && est.games && !REFRESH_STEPS.some((x) => est[x.id] && !((x.need === "igdb" && !est.igdb) || (x.need === "opencritic" && !est.opencritic))) ? `<p class="source-note">Everything is filled in. Choose “Re-fetch for every game” to pull the latest info anyway.</p>` : ""}
      ${finished}
      </div>
      <div class="job-side">
      <div class="field-label">Steps, in the order they run</div>
      <div class="toggles refresh-steps">
        ${REFRESH_STEPS.map((x) => {
          const off = (x.need === "igdb" && !est.igdb) || (x.need === "opencritic" && !est.opencritic);
          const n = est[x.id] || 0;
          const ocNote = x.id === "critics" && !off && est.oc_left
            ? (est.oc_left.paused || !est.oc_left.requests ? "Paused until tomorrow (free plan)"
              : est.oc_left.cooling_until ? "Short break (OpenCritic asked), back in a few minutes"
              : `${n.toLocaleString()} to look up, ${est.oc_left.requests} today`)
            : null;
          const note = off ? `<button type="button" class="link" data-setup="${x.need}">Set up ${x.need === "igdb" ? "IGDB" : "OpenCritic"}</button>`
            : ocNote ? ocNote
            : all ? "" : (n ? `${n.toLocaleString()} ${x.unit}` : "Up to date");
          return `<label class="${off ? "off" : ""}"><input type="checkbox" name="rstep" value="${x.id}" ${off ? "disabled" : "checked"}><span>${x.label}<small>${x.hint}</small></span><em>${note}</em></label>`;
        }).join("")}
      </div>
      </div>
      </div>
      <div class="dialog-actions"><button class="btn ghost" value="close">Close</button><button class="btn" type="button" id="refresh-start" ${est.games ? "" : "disabled"}>${all && job ? "Start over" : "Start refresh"}</button></div>`;
    return;
  }

  const sources = syncSources();
  const anyReady = sources.some((x) => x.ok);
  body.innerHTML = `
    <div class="job-cols">
    <div class="job-main">
    <p class="lede">Pulls your games, playtime and last played from your stores. Games that are new to your library also get their details, art and time to beat.</p>
    <div class="sync-sources">
      ${sources.map((x) => `<label class="${x.ok ? "" : "off"}"><input type="checkbox" name="src" value="${x.id}" ${x.ok ? "checked" : "disabled"}><span>${x.name}</span><small>${x.ok ? "Ready" : `<button type="button" class="link" data-setup="${x.id}">Set up</button>`}</small></label>`).join("")}
    </div>
    <div class="sync-sources"><label class="shots-row"><span>Nintendo Switch</span><small><button type="button" class="link" data-open-shots>Import from a screen recording</button></small></label></div>
    <p class="source-note">Games already in your library only get their playtime and last played updated. To update their details or art, use Refresh assets.</p>
    ${libraryChecks()}
    ${finished}
    </div>
    <div class="job-side">
    <div class="field-label">What happens</div>
    <div id="sync-preview"></div>
    </div>
    </div>
    <div class="dialog-actions"><button class="btn ghost" value="close">Close</button><button class="btn" type="button" id="sync-start" ${anyReady ? "" : "disabled"}>Start sync</button></div>`;
  syncPreview();
}

// Tidying tools, in the Sync and Refresh assets windows: duplicates and games IGDB couldn't identify.
function libraryChecks() {
  const n = unidentified().length;
  return `<div class="field-label checks-label">Library checks</div>
    <div class="sync-sources checks">
      <label class="shots-row"><span>Duplicate games<small>Editions and store versions listed more than once</small></span><small><button type="button" class="link" data-open-dups>Find duplicates</button></small></label>
      <label class="shots-row"><span>Unidentified games<small>${n ? `${n.toLocaleString()} game${n === 1 ? "" : "s"} without details, covers or time to beat` : "Every game is identified"}</small></span>
        <small>${n ? `<button type="button" class="link" data-open-unid>Review ${n === 1 ? "it" : "them"}</button>` : ""}</small></label>
    </div>`;
}

async function openJob(view) {
  state.jobView = view;
  await loadSettings();
  state.sync = await api("/api/sync");
  if (view === "refresh" && !state.sync.running) state.estimate = await api("/api/refresh/estimate");
  renderJobDialog();
  $("#sync-dialog").showModal();
}

$("#sync-body").addEventListener("change", (e) => {
  if (e.target.name === "src") syncPreview();
  if (e.target.name === "rmode") {
    // keep the step ticks the user chose
    const ticked = new Set($$("input[name=rstep]:checked").map((i) => i.value));
    state.refreshMode = e.target.value;
    renderJobDialog();
    $$("input[name=rstep]:not(:disabled)").forEach((i) => { i.checked = ticked.has(i.value); });
  }
});

$("#sync-body").addEventListener("click", async (e) => {
  if (e.target.id === "sync-start" || e.target.id === "refresh-start") {
    const isSync = e.target.id === "sync-start";
    const body = isSync
      ? { sources: $$("input[name=src]:checked", $("#sync-body")).map((i) => i.value) }
      : { mode: state.refreshMode || "missing", steps: $$("input[name=rstep]:checked").map((i) => i.value) };
    if (isSync && !body.sources.length) { toast("Pick at least one library", true); return; }
    if (!isSync && !body.steps.length) { toast("Pick at least one thing to update", true); return; }
    try {
      state.sync = await api(isSync ? "/api/sync" : "/api/refresh", { method: "POST", body });
      renderJobDialog();
      startPolling();
    } catch (err) { toast(err.message, true); }
  }
  if (e.target.closest("[data-sync-cancel]")) {
    const forget = e.target.closest("[data-sync-cancel]").dataset.forget === "1";
    if (forget && !confirm("Cancel the full refresh? What it already updated stays; the rest won't be re-fetched.")) return;
    await api("/api/sync/cancel", { method: "POST", body: { forget } });
    toast(forget ? "Cancelling the full refresh after the current game" : state.sync?.mode === "all" ? "Pausing after the current game. Continue it from Refresh assets." : "Stopping after the current game");
  }
  if (e.target.id === "refresh-resume") {
    try {
      state.sync = await api("/api/refresh", { method: "POST", body: { resume: true } });
      renderJobDialog();
      startPolling();
    } catch (err) { toast(err.message, true); }
  }
  if (e.target.id === "refresh-forget") {
    if (!confirm("Cancel the full refresh? What it already updated stays; the rest won't be re-fetched.")) return;
    await api("/api/refresh/forget", { method: "POST", body: {} });
    state.estimate = await api("/api/refresh/estimate");
    renderJobDialog();
    toast("Full refresh cancelled");
  }
  const setup = e.target.closest("[data-setup]");
  if (setup) { $("#sync-dialog").close(); openSettings(setup.dataset.setup); }
});

// A full refresh that was interrupted (the app closed or the PC shut down) carries on by itself.
// One you paused waits for you to continue it from Refresh assets.
async function resumeRefresh() {
  let job;
  try { job = (await api("/api/refresh/job")).job; } catch { return false; }
  if (!job || job.paused) return false;
  try {
    state.sync = await api("/api/refresh", { method: "POST", body: { resume: true } });
    startPolling();
    toast(`Continuing the full refresh (${Math.round((job.progress || 0) * 100)}% done)`);
    return true;
  } catch { return false; }
}

// Opening the app more than a day after the last sync starts one by itself: every
// library that's set up, the XGP and PS+ lists, and your wishlists (prices and sales too).
// After an update whose new features need a sync to fill in (SYNC_NEEDED in __init__.py): say so
// once, with Sync now / Later. An automatic sync that starts anyway takes care of it.
function showSyncNote() {
  const bar = $("#sync-note"), why = state.sync?.prompt || [];
  if (!why.length || state.sync?.running) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = `<span>${esc(APP)} ${esc(state.desktop?.version || "")} is installed. A sync fills in ${why.map(esc).join(", and ")}.</span>
    <button class="btn small" data-sn="sync">Sync now</button><button class="link" data-sn="later">Later</button>`;
}
$("#sync-note").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-sn]");
  if (!b) return;
  $("#sync-note").hidden = true;
  if (b.dataset.sn === "later") { api("/api/sync/prompt", { method: "POST", body: {} }).catch(() => {}); return; }
  openJob("sync");
});

async function maybeAutoSync() {
  const st = state.sync || {};
  const last = st.last_sync_at || 0;
  if (st.running || Date.now() / 1000 - last < 86400) return;
  const ready = syncSources().filter((x) => x.ok).map((x) => x.id);
  if (!ready.length) return;
  try {
    state.sync = await api("/api/sync", { method: "POST", body: { sources: ready } });
    startPolling();
    toast(last ? `Syncing your libraries: the last sync was ${relDate(last).toLowerCase()}` : "Syncing your libraries");
  } catch { /* another job is running; try again next time */ }
}

function fmtEta(sec) {
  if (sec == null) return "";
  if (sec < 60) return "Less than a minute left";
  const min = Math.round(sec / 60);
  if (min < 60) return `About ${min} min left`;
  const h = Math.floor(min / 60), m = min % 60;
  return `About ${h} h ${m ? m + " min " : ""}left`;
}

function updateSyncButton() {
  const st = state.sync || {};
  const pct = st.overall != null ? ` ${Math.floor(100 * st.overall)}%` : "…";
  const syncing = st.running && st.kind === "sync";
  const refreshing = st.running && st.kind === "refresh";
  $("#btn-sync").classList.toggle("syncing", Boolean(syncing));
  $("#btn-refresh").classList.toggle("syncing", Boolean(refreshing));
  $("#sync-label").textContent = syncing ? `Syncing${pct}` : "Sync";
  $("#refresh-label").textContent = refreshing ? `Refreshing${pct}` : "Refresh assets";
}

function startPolling() {
  clearInterval(pollTimer);
  updateSyncButton();
  pollTimer = setInterval(async () => {
    try { state.sync = await api("/api/sync"); } catch { return; }
    updateSyncButton();
    const dlg = $("#sync-dialog");
    if (dlg.open && (state.sync.running || state.jobView === state.sync.kind)) renderJobDialog();
    const now = Date.now();
    if (now - lastReload > 5000) { lastReload = now; reloadLibrary(); }
    if (!state.sync.running) {
      clearInterval(pollTimer);
      await reloadLibrary();
      if (state.sync.kind === "refresh") state.estimate = await api("/api/refresh/estimate").catch(() => state.estimate);
      if (dlg.open) renderJobDialog();
      const name = state.sync.kind === "refresh" ? "Refresh" : "Sync";
      const errs = (state.sync.errors || []).length;
      toast(errs ? `${name} finished with ${errs} problem${errs > 1 ? "s" : ""}. Open ${name === "Refresh" ? "Refresh assets" : "Sync"} for details.` : `${name} finished`, errs > 0);
      if (state.sync.kind === "sync") maybeReviewDups();
    }
  }, 1500);
}

/* ------------------------------------------------------------ data loading */

let libraryLoads = 0;
async function reloadLibrary() {
  const n = ++libraryLoads;
  const data = await api("/api/library");
  if (n !== libraryLoads) return;  // a newer reload already started: its answer wins
  state.platforms = data.platforms;
  state.wishlistAt = data.wishlist_at;
  state.shelves = data.shelves || [];
  state.wishOrder = data.wish_order || [];
  state.yearCache = {};  // Your year is worked out again from the new data
  const seen = new Map();
  for (const g of data.games) { const k = g.title.toLowerCase(); seen.set(k, (seen.get(k) || 0) + 1); }
  state.sameNames = new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
  const all = data.games.map(derive);
  state.games = all.filter((g) => !g.isWishOnly && !g.unsubOnly);
  state.wishGames = all.filter((g) => g.isWishOnly);
  renderAll();
}

/* ------------------------------------------------------------ events */

function bind() {
  const search = $("#search");
  let t;
  search.addEventListener("input", () => {
    // Searching means browsing the library (the Wishlist page searches the wishlist).
    if (state.page === "curator") showPage("library");
    clearTimeout(t); t = setTimeout(() => { state.q = search.value; render(); renderWishlist(); }, 120);
  });
  $$(".page-tabs button").forEach((b) => b.addEventListener("click", () => showPage(b.dataset.page)));
  $("#tab-curator").textContent = CURATOR;

  document.addEventListener("keydown", (e) => {
    if ($(".lightbox") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); lightboxStep(e.key === "ArrowLeft" ? -1 : 1); return; }
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); search.focus(); }
    if (e.key === "Escape") {
      if ($(".lightbox")) { $(".lightbox").remove(); return; }
      if (!$("#detail-wrap").hidden && !document.querySelector("dialog[open]")) closeGame();
    }
  });

  $("#library").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (th) {
      const key = th.dataset.sort;
      if (state.prefs.sort === key) state.prefs.dir = state.prefs.dir === "asc" ? "desc" : "asc";
      else { state.prefs.sort = key; state.prefs.dir = key === "title" ? "asc" : "desc"; }
      savePrefs(); render(); return;
    }
    const card = e.target.closest("[data-id]");
    if (card) openGame(Number(card.dataset.id));
  });

  $("#sort-key").addEventListener("change", (e) => {
    state.prefs.sort = e.target.value;
    state.prefs.dir = e.target.value === "title" ? "asc" : "desc";
    savePrefs(); render();
  });
  $("#sort-dir").addEventListener("click", () => { state.prefs.dir = state.prefs.dir === "asc" ? "desc" : "asc"; savePrefs(); render(); });
  $$(".view-toggle button").forEach((b) => b.addEventListener("click", () => { state.prefs.view = b.dataset.view; savePrefs(); render(); }));
  $("#size").addEventListener("input", (e) => { state.prefs.size = Number(e.target.value); savePrefs(); document.documentElement.style.setProperty("--cover", e.target.value + "px"); });

  bindFilters();
  const reset = () => clearFilter("all");


  $("#empty").addEventListener("click", (e) => {
    if (e.target.id === "empty-reset") reset();
    if (e.target.id === "empty-settings") openSettings();
    if (e.target.id === "empty-sync") openJob("sync");
    if (e.target.id === "empty-add") openAdd();
  });

  $("#btn-settings").addEventListener("click", () => openSettings());
  $("#btn-sync").addEventListener("click", () => openJob("sync"));
  $("#btn-refresh").addEventListener("click", () => openJob("refresh"));
  $("#btn-add").addEventListener("click", openAdd);

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-dialog]")) e.target.closest("dialog").close();
  });
  $$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  // detail panel
  document.addEventListener("click", (e) => { if (!e.target.closest(".more-menu")) toggleMoreMenu(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $(".more-pop:not([hidden])")) { e.stopImmediatePropagation(); toggleMoreMenu(false); }
  }, true);
  $("#detail-wrap").addEventListener("click", async (e) => {
    if (e.target.closest("[data-close]")) { closeGame(); return; }
    const id = state.openId;
    const g = byId(id);
    const st = e.target.closest(".status-picker button");
    if (st) { setStatus(id, st.dataset.s); return; }
    const act = e.target.closest("[data-act]")?.dataset.act;
    const actV = e.target.closest("[data-act]")?.dataset.v;
    if (act !== "play-menu" && !e.target.closest(".play-pop")) $$("#detail .play-pop").forEach((x) => { x.hidden = true; });
    if (act === "play") { $$("#detail .play-pop").forEach((x) => { x.hidden = true; }); playGame(id, actV); return; }
    if (act === "install") { $$("#detail .play-pop").forEach((x) => { x.hidden = true; }); installGame(id, actV); return; }
    if (act === "play-menu") { const pop = $("#detail .play-pop"); if (pop) pop.hidden = !pop.hidden; e.target.closest("[data-act]").setAttribute("aria-expanded", String(!pop?.hidden)); return; }
    if (act === "shelf-add") {
      const pop = e.target.closest(".shelf-add").querySelector(".shelf-add-pop");
      pop.hidden = !pop.hidden;
      e.target.closest("[data-act]").setAttribute("aria-expanded", String(!pop.hidden));
      if (!pop.hidden) $("input[type=text]", pop)?.focus();
      return;
    }
    const off = e.target.closest("[data-shelf-off]");
    if (off) { toggleShelf(id, Number(off.dataset.shelfOff), false); return; }
    const other = e.target.closest("[data-open]");
    if (other) { openGame(Number(other.dataset.open)); return; }
    if (act === "menu") { toggleMoreMenu(); return; }
    if (e.target.closest(".more-pop")) toggleMoreMenu(false);  // an action was picked
    if (act === "interest") { setInterest(id, e.target.closest("[data-act]").dataset.v); return; }
    if (act === "endless") {
      const v = e.target.closest("[data-act]").dataset.v;
      try {
        const g2 = await api(`/api/games/${id}`, { method: "PATCH", body: { endless: v === "auto" ? null : v === "1" } });
        renderDetail(upsertLocal(g2));
        toast(v === "auto" ? "Back to automatic" : v === "1" ? "Marked as open-ended" : "Progress bar is back");
      } catch (err) { toast(err.message, true); }
      return;
    }
    if (act === "art") { openArt(id); return; }
    if (act === "hide") { setHidden(id, e.target.closest("[data-act]").dataset.v === "1"); return; }
    if (act === "ttb-reset") { setTtb(id, null); return; }
    if (act === "fix") openFixMatch(g || { id, title: $("#detail-title").textContent });
    if (act === "refresh") {
      toast(`Refreshing ${g?.title || "this game"}…`);
      try {
        const fresh = await api(`/api/games/${id}/refresh`, { method: "POST", body: { prefer_sgdb: true } });
        renderDetail(upsertLocal(fresh)); toast("Updated");
      } catch (err) { toast(err.message, true); }
    }
    if (act === "remove-entry") {
      const row = e.target.closest("[data-entry]");
      if (!confirm("Remove this copy from your library?")) return;
      try {
        const res = await api(`/api/entries/${row.dataset.entry}`, { method: "DELETE" });
        if (res.game) { upsertLocal(res.game); renderDetail(derive(res.game)); }
        else { closeGame(); await reloadLibrary(); }
        toast("Removed");
      } catch (err) { toast(err.message, true); }
    }
    const shot = e.target.closest(".shots img");
    if (shot) {
      const g = byId(state.openId);
      openLightbox(g?.screenshots?.length ? g.screenshots : [...shot.parentElement.querySelectorAll("img")].map((x) => x.src),
        Number(shot.dataset.shot) || 0, g?.screenshots?.length ? g.id : null);
    }
  });
  $("#detail-wrap").addEventListener("input", (e) => {
    if (e.target.id === "ttb-range") $("#ttb-val").textContent = fmtHours(Number(e.target.value));
  });
  $("#detail-wrap").addEventListener("change", async (e) => {
    const id = state.openId;
    if (e.target.id === "ttb-range") { setTtb(id, Number(e.target.value)); return; }
    const row = e.target.closest("[data-entry]");
    if (row && e.target.dataset.field) {
      const f = e.target.dataset.field;
      let body;
      if (f === "counts") body = { excluded: !e.target.checked };
      else if (f === "hours" || f === "not_mine_hours") body = { [f]: Number(e.target.value || 0) };
      else body = { [f]: e.target.value ? Math.floor(new Date(e.target.value + "T12:00:00").getTime() / 1000) : null };
      try {
        const g = await api(`/api/entries/${row.dataset.entry}`, { method: "PATCH", body });
        upsertLocal(g); renderDetail(derive(g));
        toast(f === "counts" ? (body.excluded ? "This copy no longer counts" : "This copy counts again") : f === "not_mine_hours" ? "Saved: those hours won't count" : "Saved");
      } catch (err) { toast(err.message, true); }
    }
  });
}

/* ------------------------------------------------------------ right-click menu */

function closeMenu() { $("#ctx").hidden = true; state.menuId = null; }

function openMenu(id, x, y) {
  const g = byId(id);
  if (!g) return;
  state.menuId = id;
  state.menuY = scrollY;
  const st = g.status || "";
  const m = $("#ctx");
  const item = (m, icon, label, extra = "", on = false) =>
    `<button data-m="${m}" ${extra} class="${on ? "on" : ""}">${MENU_ICON[icon] || ""}<span>${label}</span>${on ? MENU_ICON.check.replace('class="ico"', 'class="ico now"') : ""}</button>`;
  m.innerHTML = `
    <div class="ctx-title">${esc(g.title)}</div>
    ${item("open", "open", "Open")}
    ${g.canPlay ? item("play", "play", "Play") : ""}
    <hr>
    ${g.isWishOnly ? "" : `${["completed", "dnf"].map((s) => item("status", s, statusLabel(s), `data-v="${s}"`, st === s)).join("")}
    <hr>
    ${Object.entries(INTEREST).map(([k, label]) => item("interest", k, label, `data-v="${k}"`, g.interest === k)).join("")}
    <hr>`}
    ${g.isWishOnly ? "" : item("shelves", "shelf", "Add to shelf…")}
    ${g.wish && state.page === "wishlist" && state.prefs.wishSort === "mine" ? `${item("wtop", "top", "Move to top")}${item("wbottom", "bottom", "Move to bottom")}<hr>` : ""}
    ${item("art", "art", "Change art…")}
    ${g.match_state === "none" ? item("fix", "identify", "Identify game…") : item("fix", "fix", "Fix match…")}
    ${g.isHidden ? item("hide", "unhide", "Unhide") : item("hide", "hide", "Hide")}`;
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
  m.querySelector("button").focus({ preventScroll: true });
}

$("#ctx").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-m]");
  if (!b) return;
  const id = state.menuId, g = byId(id);
  if (b.dataset.m === "shelves" && g) {
    // the menu turns into the shelf picker, in place
    $("#ctx").innerHTML = `<div class="ctx-title">Shelves for ${esc(g.title)}</div>${shelfPicker(g)}`;
    ($("#ctx input[type=text]") || $("#ctx input"))?.focus({ preventScroll: true });
    return;
  }
  closeMenu();
  if (!g) return;
  const m = b.dataset.m;
  if (m === "play") { playGame(id); return; }
  if (m === "wtop" || m === "wbottom") { moveInWishOrder(id, null, m === "wbottom"); return; }
  if (m === "open") openGame(id);
  else if (m === "status") setStatus(id, b.dataset.v);
  else if (m === "interest") setInterest(id, b.dataset.v);
  else if (m === "art") openArt(id);
  else if (m === "fix") openFixMatch(g);
  else if (m === "hide") setHidden(id, !g.isHidden);
});
// composedPath: a click that redrew the menu (Add to shelf…) has a detached target by now
document.addEventListener("click", (e) => { if (!e.composedPath().includes($("#ctx"))) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#ctx").hidden) { e.stopImmediatePropagation(); closeMenu(); } }, true);
window.addEventListener("scroll", () => { if (Math.abs(scrollY - (state.menuY || 0)) > 24) closeMenu(); }, { passive: true });
window.addEventListener("resize", closeMenu);
$("#page-curator").addEventListener("contextmenu", (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  e.preventDefault();
  openMenu(Number(card.dataset.id), e.clientX, e.clientY);
});
$("#page-curator").addEventListener("click", (e) => {
  const card = e.target.closest("[data-id]");
  if (card) openGame(Number(card.dataset.id));
});
$("#page-wishlist").addEventListener("contextmenu", (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  e.preventDefault();
  openMenu(Number(card.dataset.id), e.clientX, e.clientY);
});
$("#page-wishlist").addEventListener("click", (e) => {
  if (e.target.id === "wish-sync") { openJob("sync"); return; }
  const opt = e.target.closest("[data-wf]");
  if (opt) {
    const k = opt.dataset.wf, v = opt.dataset.v;
    if (k === "sale") state.prefs.wishSale = !state.prefs.wishSale;
    else state.prefs[k] = v;
    savePrefs(); renderWishlist(); return;
  }
  const card = e.target.closest("[data-id]");
  if (card) openGame(Number(card.dataset.id));
});
// Cover size: the same setting as the library's slider.
$("#page-wishlist").addEventListener("input", (e) => {
  if (!e.target.classList.contains("wish-size")) return;
  state.prefs.size = Number(e.target.value); savePrefs();
  document.documentElement.style.setProperty("--cover", e.target.value + "px");
  $("#size").value = e.target.value;
});
$("#page-wishlist").addEventListener("change", (e) => {
  if (e.target.id === "wish-sort") { state.prefs.wishSort = e.target.value; savePrefs(); renderWishlist(); }
});
$("#library").addEventListener("contextmenu", (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  e.preventDefault();
  openMenu(Number(card.dataset.id), e.clientX, e.clientY);
});

// The game page's ⋯ menu (Fix match, Hide, Change art, Refresh assets).
function toggleMoreMenu(open) {
  const pop = $("#detail .more-pop"), btn = $('#detail [data-act="menu"]');
  if (!pop) return;
  const show = open ?? pop.hidden;
  pop.hidden = !show;
  btn?.setAttribute("aria-expanded", String(show));
}

/* ------------------------------------------------------------ screenshot viewer */

// Full-size screenshots with previous / next (buttons, arrow keys or a swipe), wrapping around.
const lightbox = { list: [], i: 0, gameId: null };
// The full-size copy of a screenshot, whichever store it's from.
function bigShot(u) {
  return u.replace("t_screenshot_big", "t_1080p")
    .replace(/\.600x338\.jpg/, ".1920x1080.jpg")                          // Steam
    .replace(/_product_card_v2_mobile_slider_639\.jpg$/, ".jpg")            // GOG
    .replace(/\?w=640$/, "?w=1920");                                         // Microsoft Store
}
// The same screenshot whatever its size (meta/shots.py key()).
function shotKey(u) {
  return (u || "").replace(/[?#].*$/, "").replace(/\.(600x338|1920x1080|116x65)\.jpg$/, ".jpg")
    .replace(/_(product_card_v2_mobile_slider_639|ggvgl|ggvgm|ggvgt)(_2x)?\.jpg$/, ".jpg").replace(/\/t_[a-z0-9_]+\//, "/");
}

// gameId: the game's own screenshots (offers "Show this one first").
function openLightbox(list, i, gameId = null) {
  $(".lightbox")?.remove();
  Object.assign(lightbox, { list, i, gameId });
  const lb = document.createElement("div");
  lb.className = "lightbox";
  lb.innerHTML = `<img alt="">
    ${list.length > 1 ? `<button class="lb-nav prev" data-lb="-1" aria-label="Previous screenshot">‹</button>
      <button class="lb-nav next" data-lb="1" aria-label="Next screenshot">›</button>
      <span class="lb-count"></span>` : ""}
    <button class="lb-close" aria-label="Close">✕</button>
    ${gameId != null && list.length > 1 ? `<div class="lb-pick"></div>` : ""}`;
  lb.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-lb]");
    if (nav) { lightboxStep(Number(nav.dataset.lb)); return; }
    const pick = e.target.closest("[data-pick]");
    if (pick) { pickFirstShot(pick.dataset.pick === "auto" ? null : lightbox.list[lightbox.i]); return; }
    if (e.target.closest(".lb-pick")) return;
    if (e.target.tagName !== "IMG") lb.remove();  // outside the picture, or ✕
  });
  let x0 = null;
  lb.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  lb.addEventListener("touchend", (e) => {
    const dx = e.changedTouches[0].clientX - (x0 ?? 0);
    if (x0 != null && Math.abs(dx) > 40) lightboxStep(dx > 0 ? -1 : 1);
    x0 = null;
  });
  document.body.append(lb);
  lightboxShow();
}

function lightboxStep(d) {
  const n = lightbox.list.length;
  if (n < 2) return;
  lightbox.i = (lightbox.i + d + n) % n;
  lightboxShow();
}

function lightboxShow() {
  const lb = $(".lightbox"), { list, i } = lightbox;
  if (!lb) return;
  $("img", lb).src = bigShot(list[i]);
  const c = $(".lb-count", lb);
  if (c) c.textContent = `${i + 1} / ${list.length}`;
  const pick = $(".lb-pick", lb), g = lightbox.gameId != null ? byId(lightbox.gameId) : null;
  if (pick && g) {
    const mine = g.shot_first && shotKey(list[0]) === g.shot_first;
    pick.innerHTML = i === 0
      ? `<span class="lb-first">${ACTION_ICON.first}${mine ? "Your pick, shown first" : "Shown first"}</span>${mine ? `<button type="button" class="link" data-pick="auto">Back to automatic order</button>` : ""}`
      : `<button type="button" class="btn small" data-pick="this">${ACTION_ICON.first}Show this one first</button>`;
  }
  for (const j of [i + 1, i - 1]) { const u = list[(j + list.length) % list.length]; if (u) new Image().src = bigShot(u); }  // ready for the next flip
}

// "Show this one first": the preview and the game page lead with it from now on.
async function pickFirstShot(url) {
  const id = lightbox.gameId, cur = lightbox.list[lightbox.i];
  try {
    const res = await api(`/api/games/${id}/shots`, { method: "POST", body: { first: url } });
    const g = byId(id);
    if (g) {
      g.screenshots = res.screenshots;
      g.shot_first = url ? shotKey(url) : null;
    }
    lightbox.list = res.screenshots.length ? res.screenshots : lightbox.list;
    lightbox.i = url ? 0 : Math.max(0, lightbox.list.findIndex((u) => shotKey(u) === shotKey(cur)));
    lightboxShow();
    if (state.openId === id && g) renderDetail(g);
    toast(url ? "This screenshot now comes first" : "Back to the automatic order");
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------------------ unidentified games */

// Games IGDB couldn't match automatically (match_state "none"): yours first, then XGP / PS+, then wishlist.
// Hidden ones (betas, demos, tools, or hidden by you) are left out.
const unidentified = () => [...state.games, ...state.wishGames].filter((g) => g.match_state === "none" && !g.isHidden)
  .sort((a, b) => (a.isWishOnly - b.isWishOnly) || (a.isCatalog - b.isCatalog) || (b.playtime_min || 0) - (a.playtime_min || 0) || a.title.localeCompare(b.title));
const unid = { rows: [], picks: new Map(), done: new Map(), queue: null };
const normKey = (t) => (t || "").toLowerCase().replace(/[™®©:’'.,!?\-–—]/g, " ").replace(/\s+/g, " ").trim();

function openUnid() {
  if (!igdbReady()) { toast("Connect IGDB in Settings first", true); return; }
  unid.rows = unidentified().map((g) => ({ id: g.id, title: g.base_title || g.title, g, sugg: null, searching: false }));
  unid.picks = new Map(); unid.done = new Map();
  renderUnid();
  $("#unid-dialog").showModal();
  loadSuggestions();
}

// Best guesses, a few at a time (IGDB allows 4 requests a second).
async function loadSuggestions() {
  const todo = unid.rows.filter((r) => r.sugg === null);
  const worker = async () => {
    while (todo.length && $("#unid-dialog").open) {
      const r = todo.shift();
      try {
        const res = await api(`/api/igdb/search?clean=1&limit=4&q=${encodeURIComponent(r.title)}`);
        r.sugg = res.results.slice(0, 3);
        const exact = r.sugg.find((x) => normKey(x.name) === normKey(r.title.replace(/\s*\(.*?\)\s*$/, "")));
        if (exact && !unid.picks.has(r.id)) unid.picks.set(r.id, { id: exact.id, name: exact.name });
      } catch { r.sugg = []; }
      renderUnidRow(r);
      renderUnidFoot();
    }
  };
  await Promise.all([worker(), worker()]);
}

function unidRowHTML(r) {
  const g = r.g, done = unid.done.get(r.id), pick = unid.picks.get(r.id);
  const where = g.isWishOnly ? "Wishlist" : g.isCatalog ? (g.subs || []).map((x) => `On ${SUB_SHORT[x.s]}`).join(", ") || "Catalog"
    : g.platforms.map((p) => PLATFORM_SHORT[p] || p).join(", ");
  const info = `${esc(where)}${g.playtime_min ? ` · ${fmtHours(g.played_h)}` : ""}`;
  if (done) return `<div class="unid-row done" data-u="${r.id}"><span class="unid-cover"></span>
    <span class="unid-text"><b>${esc(r.title)}</b><small>${esc(done)}</small></span></div>`;
  const sugg = r.searching ? `<div class="unid-search"></div>`
    : r.sugg === null ? `<span class="source-note">Looking for matches…</span>`
    : `${r.sugg.map((x) => `<button type="button" class="unid-sugg ${pick?.id === x.id ? "on" : ""}" data-pick="${x.id}" data-name="${esc(x.name)}" title="${esc(x.name)}${x.year ? ` (${x.year})` : ""}">
        ${x.cover ? `<img src="${esc(x.cover)}" alt="" loading="lazy" data-fallbacks="">` : `<span class="noimg"></span>`}
        <span><b>${esc(x.name)}</b><small>${x.year || "Year unknown"}${x.is_edition ? " · edition/DLC" : ""}</small></span></button>`).join("")
      || `<span class="source-note">No guesses.</span>`}
      ${pick && !r.sugg.some((x) => x.id === pick.id) ? `<span class="unid-picked">Picked: <b>${esc(pick.name)}</b></span>` : ""}`;
  return `<div class="unid-row" data-u="${r.id}">
    <span class="unid-cover">${coverHTML(g)}</span>
    <span class="unid-text"><b>${esc(r.title)}</b><small>${info}</small></span>
    <div class="unid-suggs">${sugg}</div>
    <div class="unid-acts">
      <button class="btn small" data-use ${pick ? "" : "disabled"}>Use</button>
      <button class="link" data-search>${r.searching ? "Close search" : "Search…"}</button>
      <button class="link" data-hide title="Not a game (a tool, a beta, a demo…): hide it">Hide</button>
    </div>
  </div>`;
}
function renderUnidRow(r) {
  const el = $(`#unid-body [data-u="${r.id}"]`);
  if (!el) return;
  el.outerHTML = unidRowHTML(r);
  if (r.searching) {
    const box = searchBox((p) => { unid.picks.set(r.id, p); const b = $(`#unid-body [data-u="${r.id}"] [data-use]`); if (b) b.disabled = false; }, r.title);
    $(`#unid-body [data-u="${r.id}"] .unid-search`).append(box);
  }
}
function renderUnidFoot() {
  const n = unid.rows.filter((r) => !unid.done.has(r.id) && unid.picks.has(r.id)).length;
  const left = unid.rows.filter((r) => !unid.done.has(r.id)).length;
  $("#unid-foot").innerHTML = `<span class="source-note">${left ? `${left} left` : "All done"}</span><span class="spacer"></span>
    <button class="btn ghost" data-close-dialog>Close</button>
    <button class="btn" data-use-all ${n ? "" : "disabled"}>Use all picked${n ? ` (${n})` : ""}</button>`;
}
function renderUnid() {
  const hidden = [...state.games, ...state.wishGames].filter((g) => g.match_state === "none" && g.isHidden).length;
  $("#unid-body").innerHTML = unid.rows.length ? `
    <p class="lede">IGDB couldn't tell which game these are, so they have no details, covers or time to beat. Pick the right one from the guesses, search for it, or hide it if it isn't a game. Nothing changes until you press Use.</p>
    <div class="unid-list">${unid.rows.map(unidRowHTML).join("")}</div>
    ${hidden ? `<p class="source-note">${hidden} hidden game${hidden === 1 ? " isn't" : "s aren't"} listed (betas, demos, tools and games you hid).</p>` : ""}
    <div class="dialog-actions unid-foot" id="unid-foot"></div>`
    : `<p class="lede">Every game is identified.</p><div class="dialog-actions"><button class="btn" data-close-dialog>Done</button></div>`;
  if (unid.rows.length) renderUnidFoot();
}

async function useUnid(r) {
  const pick = unid.picks.get(r.id);
  if (!pick) return;
  const row = $(`#unid-body [data-u="${r.id}"]`);
  row?.classList.add("busy");
  try {
    await api(`/api/games/${r.id}/match`, { method: "POST", body: { igdb_id: pick.id } });
    unid.done.set(r.id, `Matched to ${pick.name}. Its details and art are on the way.`);
  } catch (e) { toast(`${r.title}: ${e.message}`, true); row?.classList.remove("busy"); return; }
  renderUnidRow(r);
}

$("#unid-body").addEventListener("click", async (e) => {
  const rowEl = e.target.closest("[data-u]");
  const r = rowEl && unid.rows.find((x) => x.id === Number(rowEl.dataset.u));
  if (e.target.closest("[data-use-all]")) {
    e.target.closest("[data-use-all]").disabled = true;
    for (const x of unid.rows) if (!unid.done.has(x.id) && unid.picks.has(x.id)) await useUnid(x);
    renderUnidFoot(); await reloadLibrary(); toast("Matches updated");
    return;
  }
  if (!r) return;
  const s = e.target.closest("[data-pick]");
  if (s) {
    const id = Number(s.dataset.pick);
    if (unid.picks.get(r.id)?.id === id) unid.picks.delete(r.id); else unid.picks.set(r.id, { id, name: s.dataset.name });
    renderUnidRow(r); renderUnidFoot(); return;
  }
  if (e.target.closest("[data-use]")) { await useUnid(r); renderUnidFoot(); reloadLibrary(); return; }
  if (e.target.closest("[data-search]")) { r.searching = !r.searching; renderUnidRow(r); return; }
  if (e.target.closest("[data-hide]")) {
    if (!(await setHidden(r.id, 1, { undo: false }))) return;
    unid.done.set(r.id, "Hidden. Pick Hidden in the Type filter to find it.");
    renderUnidRow(r); renderUnidFoot();
  }
});

/* ------------------------------------------------------------ duplicates */

const dups = { groups: null, auto: false };

const dupGroups = (res) => res.groups.map((gr) => ({ ...gr, picked: new Set(gr.games.map((x) => x.id)) }));

async function openDups() {
  const dlg = $("#dup-dialog");
  dups.auto = false;
  $("#dup-body").innerHTML = `<p class="lede">Looking for games listed more than once…</p><div class="progress indeterminate"><i></i></div>`;
  if (!dlg.open) dlg.showModal();
  try {
    dups.groups = dupGroups(await api("/api/duplicates"));
  } catch (e) { $("#dup-body").innerHTML = `<p class="test-result err">${esc(e.message)}</p>`; return; }
  renderDups();
}

// After a sync brings in games that look like ones you already have, the window opens by
// itself (new ones first). Nothing is combined until you say so, group by group.
async function maybeReviewDups() {
  try {
    if (!(await api("/api/duplicates/review")).pending || $("#dup-dialog").open) return;
    const groups = dupGroups(await api("/api/duplicates"));
    if (!groups.some((gr) => gr.fresh)) { api("/api/duplicates/review", { method: "POST", body: {} }).catch(() => {}); return; }
    dups.groups = groups;
    dups.auto = true;
    renderDups();
    $("#dup-dialog").showModal();
  } catch { /* it can wait for the next sync */ }
}

$("#dup-dialog").addEventListener("close", () => {
  // Seen: it won't open by itself again for these (Sync › Library checks › Find duplicates still lists any left).
  api("/api/duplicates/review", { method: "POST", body: {} }).catch(() => {});
});

function renderDups() {
  const body = $("#dup-body");
  const groups = dups.groups || [];
  if (!groups.length) {
    body.innerHTML = `<p class="lede">No duplicates found. Every game is listed once.</p>
      <div class="dialog-actions"><button class="btn" data-close-dialog>Done</button></div>`;
    return;
  }
  const fresh = groups.filter((gr) => gr.fresh).length;
  body.innerHTML = `
    ${dups.auto && fresh ? `<p class="dup-new-note"><b>New games just arrived${fresh === 1 ? " and one looks" : ` and ${fresh} look`} like ${fresh === 1 ? "a game" : "games"} you already have.</b> Check ${fresh === 1 ? "it" : "each one"} below: nothing is combined until you say so.</p>` : ""}
    <p class="lede">These look like the same game listed more than once: editions, store versions, tutorials or add-ons. Combining keeps one entry, adds up the playtime and keeps every copy. Untick anything that's really a different game.</p>
    <div class="dup-groups">
      ${groups.map((gr, gi) => `<div class="dup-group ${gr.fresh ? "fresh" : ""}" data-g="${gi}">
        ${gr.games.map((x) => `<div class="dup-row ${gr.picked.has(x.id) ? "" : "off"}">
          <input type="checkbox" id="dup-${gi}-${x.id}" data-pick="${x.id}" ${gr.picked.has(x.id) ? "checked" : ""} aria-label="Include ${esc(x.title)}">
          <label class="dup-cover" for="dup-${gi}-${x.id}">${x.cover ? `<img src="${esc(x.cover)}" alt="" loading="lazy" data-fallbacks="">` : ""}</label>
          <label class="dup-text" for="dup-${gi}-${x.id}"><b>${esc(x.title)}${x.fresh ? ` <span class="dup-fresh">New</span>` : ""}</b><small>${x.platforms.map((p) => esc(PLATFORM_SHORT[p] || p)).join(", ")}${x.hours ? `, ${fmtHours(x.hours)}` : ""}${x.reason ? ` · ${esc(x.reason)}` : ""}</small></label>
          <label class="dup-keep"><input type="radio" name="keep-${gi}" value="${x.id}" ${x.id === gr.keep ? "checked" : ""}> Keep this name</label>
        </div>`).join("")}
        <div class="dup-actions">
          <button class="link" data-dismiss="${gi}">Not duplicates</button>
          <button class="btn small" data-combine="${gi}" ${gr.picked.size < 2 ? "disabled" : ""}>Combine</button>
        </div>
      </div>`).join("")}
    </div>
    <div class="dialog-actions"><button class="btn ghost" data-close-dialog>${dups.auto ? "Decide later" : "Close"}</button>
      ${groups.length > 1 && !dups.auto ? `<button class="btn" data-combine-all>Combine all ${groups.length} groups</button>` : ""}</div>`;
}

async function combineGroup(gi) {
  const gr = dups.groups[gi];
  const ids = [...gr.picked];
  const keep = ids.includes(gr.keep) ? gr.keep : ids[0];
  await api("/api/duplicates/combine", { method: "POST", body: { keep, others: ids.filter((x) => x !== keep) } });
}

$("#dup-body").addEventListener("change", (e) => {
  const grEl = e.target.closest("[data-g]");
  if (!grEl) return;
  const gr = dups.groups[Number(grEl.dataset.g)];
  if (e.target.dataset.pick) {
    const id = Number(e.target.dataset.pick);
    e.target.checked ? gr.picked.add(id) : gr.picked.delete(id);
  } else if (e.target.type === "radio") {
    gr.keep = Number(e.target.value);
    gr.picked.add(gr.keep);
  }
  renderDups();
});

$("#dup-body").addEventListener("click", async (e) => {
  const one = e.target.closest("[data-combine]"), dis = e.target.closest("[data-dismiss]");
  try {
    if (one) {
      one.disabled = true;
      const gi = Number(one.dataset.combine);
      await combineGroup(gi);
      dups.groups.splice(gi, 1);
      renderDups(); await reloadLibrary();
      toast("Combined");
    } else if (dis) {
      const gi = Number(dis.dataset.dismiss);
      await api("/api/duplicates/dismiss", { method: "POST", body: { ids: dups.groups[gi].games.map((x) => x.id) } });
      dups.groups.splice(gi, 1);
      renderDups();
      toast("Got it, they'll stay separate");
    } else if (e.target.closest("[data-combine-all]")) {
      e.target.disabled = true;
      let n = 0;
      for (let gi = 0; gi < dups.groups.length; gi++) {
        if (dups.groups[gi].picked.size >= 2) { await combineGroup(gi); n++; }
      }
      dups.groups = dups.groups.filter((gr) => gr.picked.size < 2);
      renderDups(); await reloadLibrary();
      toast(`Combined ${n} game${n === 1 ? "" : "s"}`);
    }
  } catch (err) { toast(err.message, true); renderDups(); }
});

/* ------------------------------------------------------------ boot */

/* Keep password managers (1Password, LastPass, Bitwarden, Dashlane) from offering to
   save anything typed here: this is a local app, its keys live in the data folder. */
function ignoreForPasswordManagers(root = document) {
  root.querySelectorAll("input, textarea, form").forEach((el) => {
    el.setAttribute("data-1p-ignore", "");
    el.setAttribute("data-lpignore", "true");
    el.setAttribute("data-bwignore", "true");
    el.setAttribute("data-form-type", "other");
    if (el.tagName !== "FORM") el.setAttribute("autocomplete", "off");
  });
}
new MutationObserver(() => ignoreForPasswordManagers()).observe(document.body, { childList: true, subtree: true });

// Arriving from the Send to <app> bookmark on connect.ubisoft.com.
async function receiveUbisoft() {
  let payload = null;
  try { payload = JSON.parse(decodeURIComponent(location.hash.slice("#ubisoft=".length))); } catch { /* broken link */ }
  history.replaceState(null, "", location.pathname);
  if (!payload) { toast("That Ubisoft link didn't come through. Click the bookmark again.", true); return; }
  try {
    const res = await api("/api/ubisoft/link", { method: "POST", body: { payload } });
    await loadSettings();
    toast(res.remember ? `Ubisoft Connect linked${res.name ? ` as ${res.name}` : ""}: ${res.games} games. Adding them now…`
      : `Ubisoft Connect linked for today only: Ubisoft didn't hand over a "stay signed in" pass, so later syncs can't refresh by themselves. `
        + `Sign out on connect.ubisoft.com, sign in again with Remember me ticked, then click the bookmark again.`, !res.remember);
    if (!state.sync?.running) {
      state.sync = await api("/api/sync", { method: "POST", body: { sources: ["ubisoft"] } });
      startPolling();
    }
  } catch (e) { toast(e.message, true); }
}

(async function boot() {
  ignoreForPasswordManagers();
  bind();
  try {
    await loadSettings();
    await reloadLibrary();
    // The tab in the address (after a reload), else the one picked in Settings › Display.
    const start = ["curator", "library", "wishlist", "year"].includes(state.settings?.settings?.start_page) ? state.settings.settings.start_page : "curator";
    showPage(["#library", "#wishlist", "#curator", "#year"].includes(location.hash) ? location.hash.slice(1) : start, { push: false });
    await startDesktop();
    state.sync = await api("/api/sync");
    if (location.hash.startsWith("#ubisoft=")) await receiveUbisoft();
    if (state.sync.running) startPolling();
    else {
      maybeReviewDups();  // a sync that ran while the window was closed may have found some
      if (!(await resumeRefresh())) await maybeAutoSync();
      showSyncNote();
    }
    setInterval(() => { if (!state.sync?.running) api("/api/sync").then((st) => { state.sync = st; maybeAutoSync(); }).catch(() => {}); }, 60 * 60 * 1000);
  } catch (e) {
    toast(`Couldn't reach ${APP}. Start it again from its shortcut or the Start ${APP} file.`, true);
  }
})();

// Wishlist › My order: drag a cover onto another; a pink line shows which side it lands on.
(() => {
  const page = $("#page-wishlist");
  let dragId = null;
  const clear = () => $$("#page-wishlist .wcard").forEach((c) => c.classList.remove("ins-before", "ins-after", "dragging"));
  page.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".wcard[draggable]");
    if (!card) return;
    dragId = Number(card.dataset.id);
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragId));
  });
  page.addEventListener("dragover", (e) => {
    const card = e.target.closest(".wcard[draggable]");
    if (!card || dragId == null) return;
    e.preventDefault();
    const r = card.getBoundingClientRect(), after = e.clientX > r.left + r.width / 2;
    $$("#page-wishlist .wcard").forEach((c) => { if (c !== card) c.classList.remove("ins-before", "ins-after"); });
    if (Number(card.dataset.id) !== dragId) { card.classList.toggle("ins-after", after); card.classList.toggle("ins-before", !after); }
  });
  page.addEventListener("drop", (e) => {
    const card = e.target.closest(".wcard[draggable]");
    if (!card || dragId == null) return;
    e.preventDefault();
    const target = Number(card.dataset.id), after = card.classList.contains("ins-after"), id = dragId;
    dragId = null; clear();
    if (target !== id) moveInWishOrder(id, target, after);
  });
  page.addEventListener("dragend", () => { dragId = null; clear(); });
})();

// Wishlist section bar: jump to a section; the one you're looking at is lit as you scroll.
function wishJumpOffset() {
  return ($(".topbar")?.offsetHeight || 0) + ($("#wishlist .wish-jump")?.offsetHeight || 0) + 10;
}
function markWishJump() {
  const bar = $("#wishlist .wish-jump");
  if (!bar || state.page !== "wishlist") return;
  const off = wishJumpOffset() + 4;
  let cur = null;
  for (const s of $$("#wishlist [data-wsec]")) if (s.getBoundingClientRect().top <= off) cur = s.dataset.wsec;
  cur = cur || $("#wishlist [data-wsec]")?.dataset.wsec;
  for (const b of bar.querySelectorAll("[data-wjump]")) b.classList.toggle("on", b.dataset.wjump === cur);
}
$("#page-wishlist").addEventListener("click", (e) => {
  const b = e.target.closest("[data-wjump]");
  if (!b) return;
  const sec = $(`#wish-sec-${b.dataset.wjump}`);
  if (sec) scrollTo({ top: sec.getBoundingClientRect().top + scrollY - wishJumpOffset(), behavior: "smooth" });
}, true);
let wishJumpTick = false;
addEventListener("scroll", () => {
  if (wishJumpTick) return;
  wishJumpTick = true;
  requestAnimationFrame(() => { wishJumpTick = false; markWishJump(); });
}, { passive: true });
// Where the header ends (fractional on scaled displays: rounded down and overlapped by a pixel so no gap shows).
const setTopbarH = () => document.documentElement.style.setProperty("--topbar-h", Math.max(0, Math.floor($(".topbar")?.getBoundingClientRect().height || 68) - 1) + "px");
new MutationObserver(() => { markWishJump(); setTopbarH(); }).observe($("#wishlist"), { childList: true });
addEventListener("resize", setTopbarH);
setTopbarH();
