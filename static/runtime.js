/* klevs runtime shim, loaded first on every page.

   Web (Cloudflare Pages or the FastAPI app): does nothing. The API is same-origin and pages
   link to /app and /admin.

   Native shells (Capacitor, Android and iOS): the app is bundled inside the shell, so /api calls
   go to the Worker origin and page links go to the bundled files. scripts/build-mobile.mjs writes
   the Worker origin into the placeholder below. */
(function () {
  "use strict";

  var BUILT_API_ORIGIN = "__KLEVS_API_ORIGIN__";
  var cap = window.Capacitor;
  var native = !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  var apiBase = native ? BUILT_API_ORIGIN.replace(/\/+$/, "") : "";
  var routes = native
    ? { home: "index.html", app: "index.html", admin: "admin.html" }
    : { home: "/", app: "/app", admin: "/admin" };

  /* Requests from phones on weak networks. Every call has a time limit, so a stalled connection
     cannot leave a button disabled for ever. A failed read is retried once; a failed write is not
     retried, because the server may already have acted on it. A connection failure is reported
     as an Error with `network = true` and a message people can act on. */
  var CONNECTION_MESSAGE = "We couldn't reach the server. Check your internet connection and try again.";
  var DEFAULT_TIMEOUT_MS = 20000;
  var RETRY_STATUSES = [502, 503, 504];

  function fetchWithTimeout(input, init, timeoutMs) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    var options = controller ? Object.assign({}, init || {}, { signal: controller.signal }) : init;
    return window.fetch(input, options).then(
      function (res) { if (timer) clearTimeout(timer); return res; },
      function (err) { if (timer) clearTimeout(timer); throw err; }
    );
  }

  function pause(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function request(input, init, opts) {
    var options = opts || {};
    var method = String((init && init.method) || "GET").toUpperCase();
    var retries = options.retries != null ? options.retries : (method === "GET" ? 1 : 0);
    var timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
    for (var attempt = 0; ; attempt += 1) {
      try {
        var res = await fetchWithTimeout(input, init, timeoutMs);
        if (attempt < retries && RETRY_STATUSES.indexOf(res.status) !== -1) {
          await pause(700 * (attempt + 1));
          continue;
        }
        return res;
      } catch (err) {
        if (attempt < retries) {
          await pause(700 * (attempt + 1));
          continue;
        }
        var failure = new Error(CONNECTION_MESSAGE);
        failure.network = true;
        throw failure;
      }
    }
  }

  window.KLEVS = {
    native: native,
    apiBase: apiBase,
    route: function (name) {
      return routes[name] || routes.home;
    },
    request: request,
    CONNECTION_MESSAGE: CONNECTION_MESSAGE,
  };

  // The Worker must be reached over HTTPS from the shells. Anything else stays unrouted.
  if (native && /^https:\/\/[^/]+$/.test(apiBase) && typeof window.fetch === "function") {
    var nativeFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      if (typeof input === "string" && input.indexOf("/api/") === 0) {
        input = apiBase + input;
      }
      return nativeFetch(input, init);
    };
  }
})();
