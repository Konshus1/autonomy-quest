# Interview 3 — Datastore

**Default: Postgres + Apache AGE. Take the default unless they have a strong reason.**

The system needs to store two different shapes of thing:

- **Records** — tasks, runs, outcomes, costs, measurements. Rows. Postgres is obviously right.
- **Relationships** — *this failure resembles that one; this decision caused that outcome; these two
  pieces of work are the same shape.* That's a graph. It's how the system learns across its own
  history instead of just accumulating it.

## Options

| Option | Verdict |
|---|---|
| **Postgres + Apache AGE** *(default)* | **Take this.** AGE is a Postgres extension giving you openCypher graph queries *inside* Postgres. Rows and relationships in **one database, one container, one backup, one connection string.** This is what makes the single-container goal work. |
| Postgres alone | Fine if they're certain they'll never want the relationship layer. The learning loop degrades to "history it can query" rather than "history it can reason across." Reversible — AGE can be added later. |
| Postgres + separate graph DB (Neo4j etc.) | Only if they *already run one* and want to use it. Otherwise it's a second service, a second backup, a second failure mode, and it breaks the one-container story for a capability AGE already gives you. |
| SQLite | **Not supported.** No first-class graph extension, so the relationship layer has nowhere to live. Don't offer it. |

## How to run it

Don't make them adjudicate this. Say what you're doing and why, in one breath:

> "I'm putting everything in one Postgres with the AGE extension — that gives you both the records
> and the relationship graph in a single database, so the whole system stays in one container.
> Unless you already run a graph database you want me to use?"

Then move on. Reopen it only if they already have infrastructure they want to reuse.

## Record

```yaml
datastore:
  engine: postgres
  graph: age          # age | none | external
  version: "16"
```
