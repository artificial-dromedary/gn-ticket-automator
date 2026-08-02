/**
 * The PWA. One HTML file, inline CSS and JS, no build step, no framework, no
 * external requests. Served from `GET /`, added to the iOS home screen.
 */

export const MANIFEST_JSON = JSON.stringify({
  name: "Kit Control",
  short_name: "Kit",
  start_url: "/",
  display: "standalone",
  background_color: "#111418",
  theme_color: "#111418",
  icons: [
    {
      src:
        "data:image/svg+xml," +
        encodeURIComponent(
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">' +
            '<rect width="192" height="192" rx="42" fill="#111418"/>' +
            '<path d="M96 40a30 30 0 0 0-30 30v18h-6a10 10 0 0 0-10 10v46a10 10 0 0 0 10 10h72a10 10 0 0 0 10-10v-46a10 10 0 0 0-10-10h-6V70a30 30 0 0 0-30-30zm0 16a14 14 0 0 1 14 14v18H82V70a14 14 0 0 1 14-14z" fill="#f2f5f7"/>' +
            "</svg>",
        ),
      sizes: "192x192",
      type: "image/svg+xml",
    },
  ],
});

const APPLE_ICON =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">' +
      '<rect width="180" height="180" fill="#111418"/>' +
      '<path d="M90 38a28 28 0 0 0-28 28v17h-6a9 9 0 0 0-9 9v43a9 9 0 0 0 9 9h68a9 9 0 0 0 9-9V92a9 9 0 0 0-9-9h-6V66a28 28 0 0 0-28-28zm0 15a13 13 0 0 1 13 13v17H77V66a13 13 0 0 1 13-13z" fill="#f2f5f7"/>' +
      "</svg>",
  );

export const PWA_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Kit">
<meta name="theme-color" content="#111418">
<meta name="robots" content="noindex, nofollow">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="${APPLE_ICON}">
<title>Kit Control</title>
<style>
  :root {
    --bg: #111418;
    --card: #1a1f26;
    --card-locked: #3a1416;
    --edge: #2a313a;
    --edge-locked: #7c2a2f;
    --text: #f2f5f7;
    --muted: #98a4b3;
    --accent: #3f7fd6;
    --locked: #e0464f;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body {
    margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font: 17px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  body {
    padding: env(safe-area-inset-top) env(safe-area-inset-right)
             env(safe-area-inset-bottom) env(safe-area-inset-left);
  }
  main { max-width: 34rem; margin: 0 auto; padding: 1rem 1rem 2.5rem; }

  header { display: flex; align-items: baseline; gap: .6rem; margin: .5rem 0 1.25rem; }
  header h1 { font-size: 1.5rem; margin: 0; letter-spacing: -.02em; white-space: nowrap; }
  .stamp {
    color: var(--muted); font-size: .8rem; flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  header button { flex: none; padding: .55rem .8rem; font-size: .9rem; }

  button {
    font: inherit; color: inherit; border-radius: .75rem; border: 1px solid var(--edge);
    background: #232a33; padding: .6rem 1rem; cursor: pointer;
  }
  button:disabled { opacity: .55; cursor: default; }

  .card {
    background: var(--card); border: 1px solid var(--edge); border-left: 4px solid var(--edge);
    border-radius: 1rem; padding: 1rem; margin-bottom: .85rem;
    display: flex; align-items: center; gap: 1rem;
  }
  .card.locked {
    background: var(--card-locked); border-color: var(--edge-locked);
    border-left-color: var(--locked);
  }
  .card .who { flex: 1; min-width: 0; }
  .card .name { font-size: 1.25rem; font-weight: 640; letter-spacing: -.01em; }
  .card .state { color: var(--muted); font-size: .9rem; margin-top: .2rem; }
  .card.locked .state { color: #ffb8bc; }
  .card .state .tag { color: var(--muted); }
  .card .blocked { color: #ffcf8f; font-size: .82rem; margin-top: .35rem; }

  .action {
    min-width: 6.2rem; min-height: 3.1rem; font-weight: 640; font-size: 1.05rem;
    border: none; background: var(--accent); color: #fff;
  }
  .action.unlock { background: var(--locked); }
  .action.spin { color: transparent; position: relative; }
  .action.spin::after {
    content: ""; position: absolute; inset: 0; margin: auto; width: 1.15rem; height: 1.15rem;
    border: 2px solid rgba(255,255,255,.45); border-top-color: #fff; border-radius: 50%;
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  footer { margin-top: 1.5rem; }
  .all {
    width: 100%; min-height: 3.4rem; font-weight: 640; font-size: 1.05rem;
    background: #232a33; border-color: var(--edge);
  }
  .all.armed { background: var(--locked); border-color: var(--locked); color: #fff; }

  .msg {
    margin: 0 0 1rem; padding: .7rem .85rem; border-radius: .7rem; font-size: .9rem;
    background: #3a1416; border: 1px solid var(--edge-locked); color: #ffb8bc;
  }
  .msg.info { background: #16202c; border-color: #2c4360; color: #b9d2ee; }
  .muted-row { margin-top: 1.5rem; text-align: center; }
  .link {
    background: none; border: none; color: var(--muted); font-size: .82rem;
    text-decoration: underline; padding: .5rem;
  }
</style>
</head>
<body>
<main>
  <header>
    <h1>Kit Control</h1>
    <span class="stamp" id="stamp"></span>
    <button id="refresh" type="button">Refresh</button>
  </header>

  <div id="messages"></div>
  <div id="cards"></div>

  <footer>
    <button class="all" id="lockAll" type="button">Lock everyone</button>
  </footer>

  <div class="muted-row">
    <button class="link" id="forget" type="button">Clear saved token</button>
  </div>
</main>

<script>
(function () {
  "use strict";

  var TOKEN_KEY = "kit-control-token";
  var cardsEl = document.getElementById("cards");
  var stampEl = document.getElementById("stamp");
  var msgEl = document.getElementById("messages");
  var refreshEl = document.getElementById("refresh");
  var lockAllEl = document.getElementById("lockAll");
  var forgetEl = document.getElementById("forget");

  var state = { children: [], fetchedAt: null };
  var busy = {};
  var armed = false;
  var armedTimer = null;

  function token() {
    var t = localStorage.getItem(TOKEN_KEY);
    if (!t) {
      t = window.prompt("Kit Control access token");
      if (t) { t = t.trim(); localStorage.setItem(TOKEN_KEY, t); }
    }
    return t || "";
  }

  function message(text, kind) {
    msgEl.innerHTML = "";
    if (!text) return;
    var div = document.createElement("div");
    div.className = "msg" + (kind === "info" ? " info" : "");
    div.textContent = text;
    msgEl.appendChild(div);
  }

  function api(path, options) {
    options = options || {};
    return fetch(path, {
      method: options.method || "GET",
      headers: { "Authorization": "Bearer " + token() },
      cache: "no-store"
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "api_changed", detail: "Kit Control returned a non-JSON body" };
      }).then(function (body) {
        if (!response.ok && response.status !== 409 && response.status !== 207) {
          var err = new Error(body.detail || body.error || ("HTTP " + response.status));
          err.code = body.error;
          err.status = response.status;
          throw err;
        }
        return body;
      });
    });
  }

  function clockOf(iso) {
    if (!iso) return "";
    // "2026-08-02T06:32:00-06:00" — read the local wall clock straight off the
    // string. Parsing to a Date would re-render it in the phone's zone.
    var m = /T(\\d{2}):(\\d{2})/.exec(iso);
    if (!m) return "";
    var hour = parseInt(m[1], 10);
    var suffix = hour < 12 ? "AM" : "PM";
    var h12 = hour % 12 === 0 ? 12 : hour % 12;
    return h12 + ":" + m[2] + " " + suffix;
  }

  function stateLine(child) {
    if (child.locked) {
      var line = "Locked until " + clockOf(child.lockedUntil);
      if (child.source === "qustodio") line += " (set in Qustodio)";
      return line;
    }
    if (child.activeRoutineIsBedtime) return "Bedtime scheduled";
    if (child.activeRoutine) return child.activeRoutine;
    return "Unlocked";
  }

  function render() {
    cardsEl.innerHTML = "";

    state.children.forEach(function (child) {
      var card = document.createElement("div");
      card.className = "card" + (child.locked ? " locked" : "");

      var who = document.createElement("div");
      who.className = "who";

      var name = document.createElement("div");
      name.className = "name";
      name.textContent = child.name;
      who.appendChild(name);

      var line = document.createElement("div");
      line.className = "state";
      line.textContent = stateLine(child);
      who.appendChild(line);

      if (!child.lockAvailable && !child.locked) {
        var blocked = document.createElement("div");
        blocked.className = "blocked";
        // First sentence on the card; the whole reason on long press / hover,
        // so one child's setup note cannot swamp the other two.
        var reason = child.lockBlockedReason || "";
        var firstStop = reason.indexOf(". ");
        blocked.textContent =
          "Lock unavailable \\u2014 " + (firstStop > 0 ? reason.slice(0, firstStop + 1) : reason);
        blocked.title = reason;
        who.appendChild(blocked);
      }

      card.appendChild(who);

      var button = document.createElement("button");
      var unlocking = child.locked;
      button.type = "button";
      button.className = "action" + (unlocking ? " unlock" : "") + (busy[child.slug] ? " spin" : "");
      button.textContent = unlocking ? "Unlock" : "Lock";
      button.disabled = !!busy[child.slug] || (!unlocking && !child.lockAvailable);
      button.addEventListener("click", function () {
        act(child.slug, unlocking ? "unlock" : "lock");
      });
      card.appendChild(button);

      cardsEl.appendChild(card);
    });

    stampEl.textContent = state.fetchedAt ? clockOf(state.fetchedAt) : "";
    stampEl.title = state.fetchedAt ? "Last refreshed " + clockOf(state.fetchedAt) : "";
    var anyBusy = Object.keys(busy).some(function (k) { return busy[k]; });
    refreshEl.disabled = anyBusy;
    lockAllEl.disabled = anyBusy;
  }

  function applyChild(updated) {
    if (!updated) return;
    state.children = state.children.map(function (c) {
      return c.slug === updated.slug ? updated : c;
    });
  }

  function load(fresh) {
    return api("/api/status" + (fresh ? "?fresh=1" : "")).then(function (payload) {
      if (payload && payload.children) {
        state = payload;
        render();
      }
      return payload;
    });
  }

  function act(slug, verb) {
    busy[slug] = true;
    render();
    message("");

    api("/api/" + verb + "/" + slug, { method: "POST" }).then(function (result) {
      // Render from the verified response, never from an optimistic guess.
      applyChild(result.child);
      if (result.status === "already_locked") {
        message(result.spoken, "info");
      } else if (result.status === "already_unlocked") {
        message(result.spoken, "info");
      }
    }).catch(function (err) {
      // Leave the previous state visible; only the message changes.
      message(err.message || "Something failed. Check the Qustodio app.");
    }).then(function () {
      busy[slug] = false;
      render();
    });
  }

  function disarm() {
    armed = false;
    if (armedTimer) { clearTimeout(armedTimer); armedTimer = null; }
    lockAllEl.className = "all";
    lockAllEl.textContent = "Lock everyone";
  }

  lockAllEl.addEventListener("click", function () {
    if (!armed) {
      armed = true;
      lockAllEl.className = "all armed";
      lockAllEl.textContent = "Tap again to lock everyone";
      armedTimer = setTimeout(disarm, 5000);
      return;
    }
    disarm();

    state.children.forEach(function (c) { busy[c.slug] = true; });
    render();
    message("");

    api("/api/lock-all", { method: "POST" }).then(function (result) {
      (result.results || []).forEach(function (r) { applyChild(r.child); });
      var failed = (result.results || []).filter(function (r) { return r.status === "error"; });
      // Per-child outcome, always. A blanket "done" would hide a failure.
      if (failed.length) {
        message(failed.map(function (r) {
          return r.name + ": " + (r.detail || "failed");
        }).join("\\n"));
      } else {
        message(result.spoken, "info");
      }
    }).catch(function (err) {
      message(err.message || "Lock everyone failed. Check the Qustodio app.");
    }).then(function () {
      state.children.forEach(function (c) { busy[c.slug] = false; });
      render();
      load(true).catch(function () {});
    });
  });

  refreshEl.addEventListener("click", function () {
    refreshEl.disabled = true;
    message("");
    load(true).catch(function (err) {
      message(err.message || "Couldn't refresh.");
    }).then(function () { render(); });
  });

  forgetEl.addEventListener("click", function () {
    localStorage.removeItem(TOKEN_KEY);
    message("Token cleared. Reload to enter a new one.", "info");
  });

  // On open: cached status first so something renders immediately, then a
  // fresh read in the background.
  load(false).catch(function (err) {
    message(err.message || "Couldn't reach Kit Control.");
  }).then(function () {
    return load(true).catch(function () {});
  });
})();
</script>
</body>
</html>
`;
