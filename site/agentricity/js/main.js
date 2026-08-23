/* Agentricity v1 — minimal vanilla JS. No dependencies, no tracking. */
(function () {
  "use strict";

  /* ------------------------------------------------------------------
     SIGNUP ENDPOINT — same-origin capture served by the site's own
     container (site/agentricity/server/app.py -> POST /api/signup).
     The DB credential lives ONLY in that server's env, never here: the
     page just POSTs the email same-origin. If the POST fails for any
     reason the form falls back to a plain mailto:, so it is never a
     dead end and never claims a signup it didn't record.
     ------------------------------------------------------------------ */
  var SIGNUP_ENDPOINT = "/api/signup";
  var CONTACT_EMAIL = "support@talkingback.ai";

  /* ------------------------------------------------------------------
     REFERRAL ATTRIBUTION — so we can honestly measure where a signup
     came from (the launch: HN, X, direct, ...). First-touch: computed
     once on the session's first landing and kept in sessionStorage, so
     a visitor who arrives from HN and then clicks around still counts
     as HN. It is BEST-EFFORT and truthful: an explicit ?utm_source wins;
     otherwise we map the referrer's host to a short label; when we can't
     tell (no utm, no/again-stripped referrer), the source is "direct" —
     we never invent a channel we didn't observe. The value rides along
     as the signup's `source` (server stores it as-is, capped at 120).
     No cookies, no third-party tracking, no PII — just the channel.
     ------------------------------------------------------------------ */
  var ATTR_KEY = "aq_src";

  /* Map a referrer hostname to a short, stable channel label. Unknown
     hosts pass through as their bare host (www. stripped) — honest and
     still useful — rather than being forced into a bucket. */
  function hostToChannel(host) {
    host = (host || "").toLowerCase().replace(/^www\./, "");
    if (!host) return "";
    if (host === "news.ycombinator.com") return "hn";
    if (host === "x.com" || host === "twitter.com" ||
        host === "mobile.twitter.com" || host === "t.co") return "x";
    if (host === "linkedin.com" || host === "lnkd.in") return "linkedin";
    if (host === "reddit.com" || host === "old.reddit.com") return "reddit";
    if (host.indexOf("google.") === 0 || host === "google.com") return "google";
    return host;
  }

  /* Compute this landing's channel from utm_source (preferred) or the
     referrer, else "direct". Kept short (<=120, the server's cap). */
  function computeSource() {
    try {
      var params = new URLSearchParams(window.location.search || "");
      var utm = (params.get("utm_source") || "").trim().toLowerCase();
      if (utm) {
        var campaign = (params.get("utm_campaign") || "").trim().toLowerCase();
        var s = campaign ? utm + "/" + campaign : utm;
        return s.slice(0, 120);
      }
      var channel = hostToChannel(
        (function () { try { return new URL(document.referrer).hostname; }
                       catch (e) { return ""; } })()
      );
      return (channel || "direct").slice(0, 120);
    } catch (e) {
      return "direct";
    }
  }

  /* First-touch: reuse the session's original source if we already have
     one, else compute and remember it. Falls back gracefully if
     sessionStorage is unavailable (private mode, etc.). */
  function attributionSource() {
    try {
      var saved = window.sessionStorage.getItem(ATTR_KEY);
      if (saved) return saved;
      var computed = computeSource();
      try { window.sessionStorage.setItem(ATTR_KEY, computed); } catch (e) {}
      return computed;
    } catch (e) {
      return computeSource();
    }
  }

  /* Stamp the first-touch source as soon as the page loads (before any
     click-through can overwrite the referrer on a later navigation). */
  attributionSource();

  /* Sticky header: hairline once the page scrolls. */
  var header = document.querySelector(".site-header");
  var onScroll = function () {
    header.classList.toggle("scrolled", window.scrollY > 8);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* Email capture (hero + final band). */
  document.querySelectorAll("form[data-capture]").forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var input = form.querySelector('input[type="email"]');
      var email = (input.value || "").trim();
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        input.focus();
        input.setAttribute("aria-invalid", "true");
        return;
      }
      input.removeAttribute("aria-invalid");

      if (SIGNUP_ENDPOINT) {
        var btn = form.querySelector('button[type="submit"]');
        btn.disabled = true;
        fetch(SIGNUP_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: email, source: attributionSource() })
        })
          .then(function (res) {
            if (!res.ok) throw new Error("bad status " + res.status);
            return res.json().catch(function () { return null; });
          })
          .then(function (data) {
            /* Show the server's HONEST result: it distinguishes a newly
               recorded signup from one already on the list. Fall back to
               the static confirm copy if the body wasn't JSON. */
            if (data && data.message) {
              var confirm = form.querySelector(".confirm");
              if (confirm) confirm.textContent = data.message;
            }
            form.classList.add("done");
          })
          .catch(function () {
            /* Honest failure path: never claim success we didn't record. */
            btn.disabled = false;
            fallbackMailto(form, email);
          });
      } else {
        /* No backend wired yet: a mailto signup is the honest v0 path. */
        fallbackMailto(form, email);
      }
    });
  });

  function fallbackMailto(form, email) {
    /* Truthful confirmation for this path: the user is on the list only
       once they actually send the email we open for them. */
    var confirm = form.querySelector(".confirm");
    if (confirm) {
      confirm.textContent =
        "Almost there — we just opened an email to " + CONTACT_EMAIL +
        ". Hit send and you're on the early-access list. (Nothing opened? " +
        "Just email us at " + CONTACT_EMAIL + ".)";
    }
    form.classList.add("done");
    var subject = encodeURIComponent("Agentricity early access");
    var body = encodeURIComponent(
      "Please add me to the early-access list.\n\nEmail: " + email + "\n"
    );
    window.location.href =
      "mailto:" + CONTACT_EMAIL + "?subject=" + subject + "&body=" + body;
  }
})();
