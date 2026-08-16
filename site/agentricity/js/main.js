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
  var CONTACT_EMAIL = "hello@agentricity.com";

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
          body: JSON.stringify({ email: email, source: "agentricity.com" })
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
