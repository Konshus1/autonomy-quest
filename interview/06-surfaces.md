# Interview 6 — Surfaces

**Default: the local web UI, plus one push channel for when it needs you.**

Two different questions, and people answer the second one badly if you don't separate them.

## Where do you watch it? (pull)

| Surface | |
|---|---|
| **Local web UI** *(default)* | `http://localhost:8080` — the loop, what it did, what it learned, what it's about to do, what it's spent. Ships in the container. Take this. |
| CLI only | `./aq status`. Fine for someone who lives in a terminal and hates browser tabs. |

## How does it reach you when it needs a decision? (push)

This one matters more than it looks. `act-reversible` means the system **will** hit things it must
ask about — and if it asks somewhere you don't look, the loop stalls silently and you find out days
later that it's been parked waiting on you.

| Channel | |
|---|---|
| **Email** *(default)* | Reliable, works when you're away from the box, and you already check it. |
| Chat (Discord / Slack) | Good if that's genuinely where you live. |
| UI only | It waits in the UI until you next open it. **Only choose this if you'll actually open it daily.** Be blunt with them: this is how instances quietly go dormant. |

Ask directly: *"When it needs a decision from you and you're not at your desk — where does it reach
you?"* Take their real answer, not their aspirational one.

## Record

```yaml
surfaces:
  watch:
    web_ui: true
    port: 8080
  notify:
    channel: email          # email | discord | slack | none
    address: "..."          # they type it; never guess it
```

## After this file

The interview is done. `instance.yaml` should now have: mission, template, datastore, models,
budget, surfaces.

**Read the whole thing back to them before you install anything.** They are about to let this thing
act on their behalf, continuously, with their money. Sixty seconds of reading it back is the last
cheap moment to catch a wrong answer. If any field is empty — especially `mission` — go back and
fill it. Do not install an unaimed instance.
