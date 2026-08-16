/* Agentricity v1 — minimal vanilla JS. No dependencies, no tracking. */
(function () {
  "use strict";

  /* ------------------------------------------------------------------
     SIGNUP ENDPOINT — deploy-time stub.
     TODO(kevin): set this to the early-access ingest URL when the
     backend is wired (it is deployed separately; its credential/URL
     stays out of the repo). Leave "" until then: with no endpoint the
     form falls back to a plain mailto: signup, which genuinely works
     and promises nothing the site can't deliver.
     ------------------------------------------------------------------ */
  var SIGNUP_ENDPOINT = "";
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
