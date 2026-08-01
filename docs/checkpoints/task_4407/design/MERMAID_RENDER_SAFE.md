# Render-safe mermaid for BB notes (how to avoid diagram render errors)

BB renders fenced ```mermaid blocks. A **single bad token** makes the whole diagram fail with a
parse error (blank box / "syntax error"), so diagrams must stay inside a safe subset. This is the
checklist that would have caught the #2158 sequence-diagram error.

## The one rule that explains most breaks

- **Flowchart node labels CAN be quoted** — `A["text with (parens), : colons, / slashes"]` — so
  special characters survive.
- **sequenceDiagram message / note / loop / participant-alias text CANNOT be quoted.** Whatever you
  type after `:` (or after `Note over X:`, or after `loop`) is parsed raw. Special characters there
  break the parse. **This is where #2158 failed.**

## sequenceDiagram — the safe subset (what actually breaks)

In message text, `Note` text, `loop`/`alt`/`opt` labels, and `participant ... as` aliases, DO NOT use:

| Banned in seq text | Why | Use instead |
|---|---|---|
| `;` | statement separator — ends the line early | `,` or " and " |
| `>` `<` | collide with arrow tokens (`->>`, `-->>`) | words: "UI then API then unit" |
| `→ ← ⇒` (unicode arrows) | not recognized in text | "to" / "into" |
| `( )` | fine in some renderers, breaks others | commas / drop them |
| `{ }` `[ ]` `\|` `+` | tokenizer hazards | words / commas |

- **participant aliases:** `participant P as Planner` — spaces are OK; special chars are not.
  (`participant P as Planner (causal+formal)` was a breaker: parens + `+`.)
- **One statement per line.** No trailing `;`.

## flowchart — safe subset

- **Quote every label that has special chars:** `n["Formal planning: PROVE plan satisfies principles"]`.
- Even inside quotes, prefer words over `|`, `>`, and unicode arrows — some renderers still choke.
  Edge labels use `A -->|label| B`; never put a literal `|` inside node text.
- Subgraph titles: quote them too — `subgraph s["Work to eval to rework"]`.

## Belt-and-suspenders (do this every time)

1. **Also commit an SVG** of the diagram and cite it as the **authoritative visual**. An SVG never
   has a "render error"; the mermaid is a convenience mirror. (Every design diagram here ships as
   both.)
2. **Pre-post scan** (agents can't render in-loop): grep the mermaid for the banned tokens above in
   non-quotable positions; if any appear, rewrite before posting.

## Quick fix recipe for a broken sequence diagram

Replace, in all message/note/loop/alias text: `;` → `,` · `>`/`<` → words · `→` → "to" ·
strip `( ) { } | +` · make aliases plain (`as Planner`, `as Principles and Learnings`).
Then it renders.
