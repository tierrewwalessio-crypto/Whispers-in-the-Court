# Architecture (version 2)

Everything described below was **tested in the player's game** (EU5 1.3.11,
release build, `-debug_mode`) using a small probe mod before building on it.

## Why version 1 did not work

Version 1 relied on `debug_log` / `error_log` to let the script communicate. The
documentation for those effects is inside `eu5.exe`, but they are compiled out
of the release build: the game reports them as *Unknown effect*. There is no
script effect that writes to a file.

## The channel that works

```
click in the game
  └─ the mod's effect sets var:votc_sig_<tipo> on the player's country
       └─ gui/votc_bridge.gui (invisible widget, created by scripted_widgets)
            state trigger_when → ExecuteConsoleCommandsForced(Localize('votc_emit_<tipo>'))
              └─ a list of "votc_say ..." commands with the game data already resolved
                   └─ the console records them in console_history.txt  ──►  Court Brain
```

- `votc_say` is not a command: it fails harmlessly, but the console still writes
  it to the history. *Tested:* `votc_say ciao prova` reached disk when clicked.
- Data travels inside localisation strings, which the game resolves at the
  moment of the click. *Tested:* `votc_say E tag=URB nome=Urbino stab=29`.
- The last command in the list (`effect remove_variable = votc_sig_<tipo>`)
  clears the signal. *Tested:* a `run` from the interface set a variable, and
  the watcher wrote `votc_say D variabile_vista` automatically.

## The return path to the game (without simulated keystrokes)

Version 3. Each message is written **inside** `run/votc_poll.txt`, which the
bridge executes every 3 seconds with a single console command:

    if = { limit = { votc_mail_is_new = { seq = N } } votc_mail_open = { seq = N } ... votc_in_ack = yes }
    else = { votc_bridge_watchdog = yes }

The previous version launched a second command (`run votc_in.txt`) at the same
time as the check: the console rejected it ("still running commands"), leaving
the mailbox stuck, with consequences, heartbeats and the memory head never
delivered. Only two separate commands remain, both delayed (text reload after
0.8 seconds, save after 1.6 seconds), and each bridge signal is dispatched
0.6 seconds after it is set. The watchdog retriggers a signal that has remained
set for two consecutive checks. The message containing the event text is
acknowledged only after the reload (`votc_loc_done`), and the message that
displays the event is sent after that acknowledgement.

## The side panel

`tools/court_brain/courtbrain/drawer.py`: a borderless, always-on-top Tk window
anchored to the right edge of EU5's client area. It uses the game's panel
colours and fonts from the game folder (Cormorant Garamond and Noto Serif,
loaded only for this process). It slides in when a signal arrives and hides
when the player switches away from the game.

It is styled like an EU5 window: a header on a gradient `Canvas`, plaque-style
buttons (`Plate`), section bars like those in the outliner (`Bar`), recessed
panels with gold borders, and a thin scrollbar (`ThinScroll`). The ornaments
(fleur-de-lis corners, a frieze, and a diamond divider) come from `gfx.py`,
which decodes three DXT5 masks from the game files in pure Python, colours
them gold, and stores them as PNGs in Court Brain's folder (`gfx_v3/`). If
they are missing, the panel is drawn without them.

`ledger.py` is the **Court Brain** window that replaces the console: status
(game, Player2, campaign, date, read every second from `CourtBrain.status()`
without network calls) and a colour-coded log, also written to
`court_brain.log`. `run_court_brain.bat` starts it with `pyw` (no console);
startup errors are written to `court_brain_crash.log` and shown in a message.

## Diplomacy

Three country interactions (`votc_send_envoy`, `votc_request_meeting`,
`votc_state_visit`) call `votc_signal_envoy` with `mode` 1/2/3, which appears
in the snapshot as `agenda`. The menu category comes from the
`dip_<azione>_CATEGORY` key, which must be one of the engine's `CATEGORY_*`
values: we use `CATEGORY_FRIENDLY_ACTIONS`.

## Consequences

**Scripting rule:** EU5 does not let an effect read a variable it has written
during the same execution. For this reason, no action reads what it has just
written: the budget check is the `votc_can_spend` trigger (which reads the
budget as it stood at the start of the message), followed by the `votc_spend`
effect. Court Brain enforces the per-scene action limit before writing the
message.

**Consequence queue** (`tools/gen_queue.py` → `votc_queue_effects.txt`): the
message applies nothing; it writes `votc_queue = { slot id }` (up to 6 slots),
and the event's "So be it" option executes `votc_apply_queue`, so the game's
tooltip shows the actual consequences. IDs come from `actions.variants()`:
the file must be regenerated after every catalogue change. The message
containing the event text is sent separately, before the script (localisation
must finish reloading before the event is created, or the fallback text will
appear).

**Ruler's orders** (`votc_order_effects.txt`, `votc_order_*`): when the player
makes the decision (in dialogue or with a button), no influence is spent, but
each successful order incurs a fixed cost (stability, prestige, government
power). If a condition is not met, the order is not carried out, no cost is
paid, and event `votc.910` explains the reason one day later (so it can read
the variables), using a code translated in `votc_orders_l_english.yml`.

- Minor consequences go through `votc_act_*` (`votc_action_effects.txt`) and
  are sent when the conversation closes, together with outcome event `votc.100`.
- Major consequences go through `votc_do_*` (`votc_diplomacy_effects.txt`) and
  are sent only when the confirmation button in the side panel is pressed.
  They are filtered twice: in Python (`actions.validate_offer`) and in the
  game script at execution time.
- The amounts use the base game's constants
  (`main_menu/common/script_values/default_values.txt`). The budget
  regenerates according to the number of in-game months that have elapsed.

## Context

- **Snapshot**: HEAD/STATE/MIGHT/REALM/COURT/ESTATES/RULER/HEIR/PERSON records,
  plus the target of the click (TARGETCHAR, TARGETCOUNTRY, LOCATION), sent with
  every signal.
- **Codex**: laws, privileges, reforms and countries read from the installed
  files.
- **World** (`worldsave.py`, `lore.py`): every 20 minutes, the
  `votc_mail_save` message triggers `save votc_world`. The text save is read
  by section (countries, government and laws, characters, diplomacy, wars,
  events that have fired) and summarised for the player's realm and the realm
  they are speaking with.
- **Live snapshot** (`tools/gen_live.py` → `votc_live_effects.txt`, `live.py`):
  on every heartbeat and when the Court menu opens, `votc_prepare_live` fills
  30 country variables on the player's country (war, allies, rivals, subjects,
  overlord, great powers, neighbours, each with its main enemy), and the
  bridge sends them as `LIVE` records. `live.news()` compares two snapshots
  and produces news (wars, peace, new rulers, alliances), which is added to
  memory. `votc_live_effects.txt` is generated: edit `tools/gen_live.py`.
- **Memory** (`memory.py`): a tree-structured journal for each campaign
  (`court_brain/campaigns/<id>/journal.jsonl`). Each entry (fact, person,
  story thread, chronicle page) has an ID, a date and the ID of the preceding
  entry. What the court remembers is the chain from the root to a particular
  entry, the *head*. Character personas and the realm profile live in
  `meta.json`, outside the journal.
- **Campaigns and saves**: two global variables, `votc_campaign` and
  `votc_memhead`, live in the game state and therefore in every save. The
  bridge sends them with each snapshot (the `ID` record). Each message
  updates the head using `votc_set_memhead = { campaign from head }`. In the
  game script, this changes the head only if the current campaign and head
  match those the message was written for (so an in-flight message cannot
  stamp a newly loaded save). Court Brain detects a load when the game
  reports a different head from the one it was given, or a date earlier than
  the most recent memory, and switches to that head. A game without a number
  receives `votc_set_campaign`. The bridge widget requests a snapshot as soon
  as it appears (`_show`), that is, after every load. `savesindex.py` reads
  the beginning of text saves (date, playthrough, campaign, head) for
  `--saves` and to reread the correct save after a load. Saves from an
  abandoned future are not used as the world snapshot.
- **Prompts** (`prompts.py`): CRAFT (distinct voices, no stock phrases),
  IDENTITY (each realm has its own identity), DECREE_TASK (government type ×
  strength of the Crown).
- **Player2**: the model reasons before answering, and that reasoning uses
  `max_tokens`. If the limit is exhausted, the response is empty. The default
  is 3000; each empty response doubles the limit (up to 8000).

## Reference files

`docs/` contains lists extracted from the game (effects, triggers, modifiers,
casus belli, script vocabulary) used by `tools/validate_mod.py`.
