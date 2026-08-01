# Launch `ccf-4407-ralph-to-aq` (Claude Code living manager)

## How managers with a “goal prompt” are spun up here

| Engine | Goal mechanism | Launch |
|---|---|---|
| **Claude Code** (`ccf-*`) | Living-manager **goal prompt file** (no Codex `/goal` slash command). Prompt is passed at spawn via `dispatch_worker` → `_invoke_claude … "$(cat promptfile)"`. Persist with `bb_session_board.persistent=true` + `keep_open_living_manager`. | `dispatch_worker claude <window> <prompt-file> <cwd>` |
| **Codex** (`cxf-*`) | Native `/goal` / “Pursuing goal” | `dispatch_worker codex …` or `cxspawn` |
| Manual Claude | Inside tmux: `ccw <window-name>` then paste prompt (less reliable than dispatch_worker) | `ccw ccf-4407-ralph-to-aq` |

Canonical living-manager docs:

- `/Users/kevincthomas/src/talkingback-fastapi/docs/agent_comms/living_manager_session_launch.md`
- BB Decision #351 (launch phrase), #353 (verify prompt submitted)

## Why cwd is the AQ worktree

`dispatch_worker` **refuses** task-numbered workers (`ccf-4407-…`) whose cwd is the shared TalkingBack checkout or any descendant (includes `.worktrees/...`).  
AQ path `/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port` is outside that tree → allowed.

## One-shot launch (preferred)

From any zsh that has sourced `~/.zshrc` (or the dispatch helpers):

```zsh
source ~/.zshrc
DISPATCH_TMUX_SESSION="cc agents" \
dispatch_worker claude \
  ccf-4407-ralph-to-aq \
  /Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port/docs/checkpoints/task_4407/launch/ccf-4407-ralph-to-aq.GOAL.md \
  /Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port
```

Or run the wrapper:

```zsh
/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port/docs/checkpoints/task_4407/launch/launch_ccf_4407.sh
```

## After spawn — verify submission (BB #353)

```sh
sleep 5
tmux capture-pane -p -t "cc agents:ccf-4407-ralph-to-aq" | tail -60
```

Confirm the agent is **working** (not sitting with the full prompt still staged at `❯`). If staged, send one Enter and re-check.

## Register living-manager board row

```sh
# Prefer the helper in launch_ccf_4407.sh --register
# Or SQL via Postgres MCP / psql updating bb_session_board:
# session_name=ccf-4407-ralph-to-aq, task_id=4407, persistent=true,
# metadata={session_role:manager, keep_open_living_manager:true, goal_prompt:...}
```

## Goal prompt path (canonical)

`/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port/docs/checkpoints/task_4407/launch/ccf-4407-ralph-to-aq.GOAL.md`

Mirror (docs only):  
`/Users/kevincthomas/src/talkingback-fastapi/artifacts/task_4407/worker_prompts/ccf-4407-ralph-to-aq.GOAL.md`
