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

  window.KLEVS = {
    native: native,
    apiBase: apiBase,
    route: function (name) {
      return routes[name] || routes.home;
    },
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
