# Agentricity legal pages — product-accurate DRAFTS (privacy / terms / refund)

**Status:** drafts for Kevin's review, to replace the carried-over TalkingBack pages (which describe
voice, hosted credit packs, Life Engine, managed containers — none of which is the Agentricity product).
These describe the *actual* product. **Not legal advice; before these replace the live pages backing real
$299/$99 charges, they want a real legal pass on the license grant, liability, and refund terms.**
`H-loop / #6102`. Entity: Intelligent Agentricity Inc., 1365 Watercrest Cir., Lawrenceville, GA 30043.

## The product these describe (so the docs stay accurate)
- **Free:** the public open-source repo (`github.com/Konshus1/autonomy-quest`, MIT). Self-hosted.
- **Own it — $299 one-time:** a license + access to the private repo (`autonomy-quest-private`). Self-hosted.
- **Stay current — $99/mo:** ongoing access to updates of the private repo. Cancel anytime.
- **We do NOT host the customer's agent or data.** They run the software on *their* infrastructure with
  *their* LLM keys. So we hold no conversation/usage data from the software — only what the website
  collects (signup email, purchase record) + payment via Stripe.

## DECISIONS Kevin must make (each drives specific wording below)
1. **Private-repo license grant.** The public repo is MIT (permissive). What does the *paid private*
   repo license actually permit/forbid? Options: (a) also MIT (then the paid value is vetting + updates +
   replication, not code secrecy — matches the "assurance not secrecy" moat); (b) a restricted commercial
   license (internal use only, no redistribution/resale). **Draft assumes (b) a non-exclusive,
   non-transferable, internal-use license, no redistribution/resale** — change if you want (a).
2. **One-time refund window.** Digital license, access granted on purchase. Draft: **14-day refund if you
   haven't been granted/accepted the private-repo invite** (i.e., before delivery); after access is
   granted, no refund (standard for delivered digital goods). Adjust the window/terms.
3. **Subscription refund/cancel.** Draft: **cancel anytime; access continues through the paid period; no
   partial-month refunds.** Adjust (prorate? refund last charge on request?).
4. **Governing law:** draft assumes **Georgia, USA**. Confirm.
5. **Minimum age:** draft assumes **18+** (paid developer tool). Confirm.
6. **Cancellation mechanism:** Stripe Customer Billing Portal (enable it) vs. email support@ to cancel.
   Draft says email + portal; enabling the Portal is a small Stripe task.

---

## DRAFT — Privacy Policy

**Effective / Last updated:** [date on ship]

**1. Who we are.** Intelligent Agentricity Inc. ("Agentricity", "we") operates the website
agentricity.com and sells access to self-hosted automation software. We do not host your software, your
agent, or the data it processes — you run it on your own infrastructure.

**2. What we collect.**
- **Early-access signups:** if you submit your email through a form on agentricity.com, we store that
  email to contact you about access and updates. We record limited technical metadata with the
  submission (IP address, browser user-agent) solely to prevent form abuse. We don't sell it.
- **Purchases:** when you buy, our payment processor (Stripe) handles the card details — **we never
  receive or store your full card data.** We store the purchase record (email, the GitHub username you
  provide for repo access, product, amount, and Stripe identifiers) to fulfill and support the purchase.
- **Website analytics:** basic, privacy-respecting usage data (page views, referrer/UTM) to understand
  what's working. [Decision: name the analytics approach once chosen; keep first-party.]
- **What we do NOT collect:** because the software is self-hosted, we do not receive the conversations,
  goals, data, or outputs your Agentricity instance processes. That stays on your infrastructure.

**3. How we use it.** To provide access and support, contact you about your purchase and product updates,
prevent fraud/abuse, and comply with law.

**4. Sharing.** With service providers who make the service work — **Stripe** (payments), **GitHub**
(granting repo access to the username you provide), and our email provider — each under their own privacy
terms. For legal compliance when required. **We do not sell your personal information.**

**5. Retention & your rights.** We keep signup and purchase records as long as needed to provide the
service and meet legal/tax obligations. You may request access to or deletion of your personal
information by emailing support@talkingback.ai [Decision: use a support@agentricity.com address instead?].

**6. Cookies.** We use only what's needed for the site to function and for basic analytics; control
cookies via your browser.

**7. Security.** We use reasonable safeguards. The purchase ledger is write-only from the public site and
read-blind by design. No method is 100% secure.

**8. Children.** The service is not directed to anyone under 18; we don't knowingly collect their data.

**9. International.** Data is processed in the United States; by using the service you consent to that
transfer.

**10. Changes & contact.** We'll post changes here with an updated date. Questions: [support address],
Intelligent Agentricity Inc., 1365 Watercrest Cir., Lawrenceville, GA 30043, USA.

---

## DRAFT — Terms of Service

**Effective / Last updated:** [date on ship]

**1. What we offer.** agentricity.com sells (a) **Own it** — a one-time license with access to the
private Agentricity software repository, and (b) **Stay current** — a monthly subscription for ongoing
access to updates. A free, open-source version is separately available under its own (MIT) license in the
public repository. The software is **self-hosted**: you run it on your own infrastructure and supply your
own third-party model/API keys and pay those providers directly.

**2. License [Decision 1].** Subject to these terms and payment, we grant you a **non-exclusive,
non-transferable license to use the Agentricity private software for your own internal purposes.** You may
not redistribute, resell, sublicense, or publish the private code. We retain all intellectual-property
rights. The free public version remains governed by its MIT license.

**3. Subscriptions & billing.** "Stay current" is billed **$99/month via Stripe and auto-renews** until
canceled. "Own it" is a **$299 one-time** charge. You can **cancel a subscription at any time** [via the
billing portal / by emailing support]; access continues through the end of the paid period. Prices may
change with notice for future terms.

**4. Fulfillment.** After purchase you provide a GitHub username; we grant access to the private
repository to that account, typically within a day (onboarding is async/batched — we'll tell you plainly,
not pretend it's instant).

**5. Your responsibilities — autonomous software.** Agentricity runs **autonomously within the limits you
configure.** You are responsible for configuring its authority/permission gates, for the infrastructure
and credentials you run it on, for the costs it incurs with your model/API providers, and for the actions
it takes in your environment. **You must not use it for unlawful, harmful, or abusive purposes.**

**6. Disclaimers & limitation of liability.** The software is provided **"as is," without warranties** of
any kind. Because you self-host and operate an autonomous system, **we are not liable for the actions,
outputs, costs, or outcomes of your instance**, or for indirect or consequential damages. Our total
liability is limited to the amount you paid us in the prior 12 months. [Real legal review recommended.]

**7. Termination.** You may stop using the software anytime. We may suspend access for breach of these
terms (e.g., redistribution, unlawful use). License terms surviving termination survive.

**8. Governing law [Decision 4].** These terms are governed by the laws of the State of Georgia, USA.

**9. Changes & contact.** We may update these terms with notice via the site. Contact: [support address],
Intelligent Agentricity Inc., 1365 Watercrest Cir., Lawrenceville, GA 30043, USA.

---

## DRAFT — Refund Policy

**Effective / Last updated:** [date on ship]

**Own it ($299, one-time) [Decision 2].** Because this delivers immediate access to software, refunds are
available **within 14 days of purchase *if* you have not yet been granted or accepted access to the
private repository.** Once access has been granted, the sale is final (the digital good is delivered). If
something went wrong with delivery, email us and we'll make it right.

**Stay current ($99/month, subscription) [Decision 3].** You can **cancel anytime**; your access continues
through the end of the current paid month, and **we do not charge you again after cancellation.** We
generally **do not offer partial-month refunds**, but if you were charged in error or never received
access, email us.

**How to request.** Email [support address] with your purchase email. Approved refunds are issued to the
original payment method via Stripe.

**Contact.** [support address] — Intelligent Agentricity Inc., 1365 Watercrest Cir., Lawrenceville, GA
30043, USA.
