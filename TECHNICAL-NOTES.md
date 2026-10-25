# NoDebug transport: maintainer notes

Prepared on 24 October 2026. Base: the author's WITC.0.8.5.zip supplied by the player.
This English review edition is derived from the player's RU 0.8.5 NoDebug RU3 fork.
The adaptation was developed with OpenAI Codex assistance and tested in-game by the player.
Original authorship and the upstream MIT license are retained.

## What actually changed

We did not remove console execution from the bridge. The useful empirical finding was
that, in the tested EU5 release (1.3.11), GUI-triggered console execution, `run`, and
`console_history.txt` still worked while debug mode was off. The obstacle to dynamic
stories was reloading newly written localization, and the normal saves also needed
a binary reader. The fork replaces that text delivery step and reads binary saves.

This is an observed property of that game build, not a promise that the engine API
will remain available in every release.

## Original and replacement text paths

The upstream path wrote dynamic localization and requested a localization reload.
The NoDebug path preloads localization expressions when EU5 loads the mod. At runtime,
only the values of global variables change:

```text
AI response -> existing validation -> nodbg_text.encode()
    -> atomically replaced run/votc_nd85en_poll.txt
    -> GUI poll executes run
    -> global flag fragments and vnd_stamp are set
    -> GUI emits ACK into console_history.txt
    -> HistoryTail / protocol parser -> Mailbox.acknowledge()
    -> matching stamp releases the queued event and effects
```

The existing game-to-companion route is retained: GUI data expressions form records
such as `votc_say HEAD|...` and `votc_say ACK|...`. `votc_say` is deliberately not a
real console command. Its appearance in console history is the message transport.
Some bridge calls use `ExecuteConsoleCommandsForced`; the timed poll uses
`ExecuteConsoleCommand`. No player has to open or type into the console.

## Static text expressions and punctuation

`tools/gen_nodbg_text.py` generates the expressions and helper localizations from
`courtbrain/nodbg_manifest.json`. A field selects its fallback until its ready flag
is set. Its live expression joins groups of 32 `GetFlagName` expressions, for example:

```text
[GetGlobalVariable('vnd_628_0').GetFlagName]
```

The corresponding run script contains commands of the following shape:

```text
set_global_variable = { name = vnd_628_0 value = "flag:Example text" }
set_global_variable = { name = vnd_628_1 value = "flag:vnd_c044" }
set_global_variable = { name = vnd_628_ready value = yes }
set_global_variable = { name = vnd_stamp value = 123 }
```

`vnd_c044` is the static comma localization. Ordinary text fragments are limited
to 160 characters. ASCII punctuation and line breaks use preloaded character keys;
edge spaces do too. Pure ASCII word fragments are expanded to character keys to
avoid accidentally localizing a word such as `ENG` or `yes` as an existing key.
Tabs render as spaces. CRLF is normalized. AI-supplied brackets, hash and dollar
characters are converted to full-width equivalents, preserving readable prose
without interpreting it as localization expressions or formatting.

Every slot of a field is rewritten, with unused slots set to `vnd_empty`. That is
essential when a short answer replaces a long one, including after loading a save.
Unknown fields and capacity overflow are rejected before effects are queued; text
is not silently truncated. Names, location names, persistent measures and custom law
text use the same mechanism. Names are recorded as delivered only after a matching ACK.

The manifest has 691 fields: the original 635 field IDs are retained and 56 custom
law fields were appended for 0.8.5. Typical capacities are 128 fragments for short
fields, 2048 for bodies, 4096 for chronicle/biography bodies, 1024 for requested-effect
fields and 64 for names. Capacities are fragments, not character counts; consult the
manifest for the exact capacity of a particular field. Keep the manifest alongside
the generated localization. Regenerating IDs from a newly sorted key list would break
the correspondence and saved variables.

## Delivery ordering

Text and event/effects are separate mails. The game first writes `vnd_stamp`; the ACK
reports that value through `votc_loc_stamp`. Mail sequence IDs prevent repeated polls
from executing the same mail again. The event is released only after the expected text
stamp is acknowledged. Bounded retries/probes remain; after failure, the dependent
scene is dropped. The old bypass for unverified text delivery is disabled.

`Mailbox._write_loc()` explicitly raises an error in this edition. Localization reload
widgets are removed from the active bridge path, and the debug-only button requirement
is removed. Some inherited helper names, comments and unused reload configuration
remain in the source for a smaller diff; their presence does not imply that the live
NoDebug transport calls them. The installer also avoids the debug-tooltip patching path.

## Binary saves

Normal `save votc_nd85en_world` output is read through `binarysave.py`, backed by the
bundled Windows x64 `librakaly` 0.12.8 DLL (MIT). Metadata/world readers share cached
text conversions. The source save is never overwritten. Cache identity includes path,
size and modification time. A save changing during conversion is rejected for retry;
unknown tokens are rejected rather than accepting an incomplete world. Low-memory
and output-size guards remain. This is save reading, not save editing or changing an
achievement eligibility flag. See the bundled library license and source link in NOTICE.

## Achievements: evidence and limits

The player reported an actual Steam achievement unlock on 24 October 2026 with the RU
NoDebug mod enabled. The supplied Steam screenshot shows the Russian achievement
title “Господь дал, Господь и взял” and its condition: grant nobles at least eight
privileges, then revoke them. The image is included in the handoff evidence folder.

Earlier game checks showed debug off and achievements available; the player also
confirmed decree events, autonomous events and actual effects, including stability
changes and country modifiers. Those checks were performed across the RU NoDebug
development builds. The screenshot alone does not establish the exact executable
hash or launch arguments. The attribution to the active mod is the player's report.

The English EN1 edition has local tests and packaged-executable checks, but has NOT
yet been tested in a live campaign or used to unlock an achievement. This evidence
does not guarantee every achievement, every game version or every mod combination.
No Steam API unlocking, game executable patch, achievement-file edit or save rewrite
was added. The bridge reports `CanGetAchievements`; it does not set it.

The player observed fewer freezes and crashes than with the debug edition. Avoiding
localization reloads is a plausible explanation, but no controlled benchmark or crash
study was performed.

## Other changes included in this fork

The source archive is a complete fork, not a transport-only patch. It also contains:

- Russian parsing/localization support, retained behind the existing language choices;
  the EN1 companion UI and default AI language are English.
- Experimental ChatGPT account integration, independent of the NoDebug transport.
  It uses explicit browser sign-in and Windows-protected local storage; no credentials
  from the player or another app are included. The author can omit this feature when
  porting the transport.
- Asynchronous AI availability checks, so a failing provider cannot prevent the game
  mailbox and buttons from starting; startup errors are surfaced.
- Discovery of Steam libraries on other local drives.
- A packaging fix for the build runtime's `codex-rejected-cas.pem`. That runtime's SSL
  constructor expects the companion file; omitting it caused `FileNotFoundError`
  before any API request. The build includes it when needed and fails if the runtime
  requires it but it is absent. Certificate and hostname verification stay enabled.

EN1 uses a separate settings/account directory `WhispersInTheCourt-NoDebug085-EN`,
installed mod `WhispersInTheCourtNoDebug085EN`, memory `court_brain_nodebug085_en`,
transport tag `nd85en` and protocol version `8502`. It does not automatically copy
settings, prompts, accounts or memories from the RU edition or upstream installation.
The shared single-instance guard is deliberately retained: run one Court Brain and
enable one Whispers in the Court edition at a time. Other in-save variable and event
names remain shared, so start with a disposable campaign when comparing editions.

## Suggested review order

1. `courtbrain/nodbg_text.py`, `nodbg_manifest.json`, `tools/gen_nodbg_text.py` and
   generated `votc_nd_text_l_*.yml`: static text representation and bounds.
2. `courtbrain/mailbox.py`: two-stage text/event delivery and ACK handling.
3. `mod/.../gui/votc_bridge.gui`, `common/scripted_guis/votc_bridge_guis.txt`,
   `votc_bridge_l_*.yml` and runtime script values: debug gates, polling, ACKs and IDs.
4. `courtbrain/binarysave.py`, `worldsave.py`, `savesindex.py`: read-only save conversion.
5. `app.py` and `bundle.py`: active NoDebug startup, save names, installation behavior.
6. `tests` in `tools/test_*.py` and `courtbrain/nodbg_check.py`: regression tests and
   a live no-AI diagnostic that can be repeated without inference costs.

The handoff includes a unified text diff against the supplied 0.8.5 source and a
file-change inventory. Binary additions are carried in the full source archive.
The diff is not a claim that every change is required for NoDebug; use the review
order above to separate the core transport from UI, account and packaging changes.
