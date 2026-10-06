# Developing Whispers in the Court

Everything needed to run, change and rebuild the mod is in this repository.
The release exe is only a convenience: it is built from these files.

## What is where

```
mod/WhispersInTheCourt/        the EU5 mod (script, interface, localisation)
tools/court_brain/             Court Brain, the Python middleware
  courtbrain/                    the package (python -m courtbrain)
    app.py                         scenes, director, referee, consequences
    prompts.py                     every text the AI receives
    actions.py                     the catalogue of consequences (queue slots)
    works.py, trade.py, ...        decrees' works, trade deals, ...
    player2.py                     the AI providers (Player2, Gemini, Mistral, OpenRouter)
    memory.py                      the campaign's memory
    worldsave.py, world.py         reading the save and the live game
    bundle.py                      installing the mod, starting EU5
  WhispersInTheCourt.py          entry point of the exe
  config.example.json            template of config.json
tools/gen_*.py                 generators of mod files (see below)
tools/validate_mod.py          static checks of the mod's script
tools/build_exe.py             builds dist/WhispersInTheCourt.exe
tools/make_cover.py            draws workshop/cover.png
docs/ARCHITETTURA.md           how the game and Court Brain talk
docs/eu5_*.txt                 the game's script vocabulary, for the checks
extras/                        optional pieces and the old v1 bridge
workshop/                      Steam Workshop cover and screenshots
```

## Running from the sources

You need Python 3.10 or newer (the releases use 3.14). No packages are needed:
Court Brain uses only the standard library, and `tkinter` comes with the
python.org installer.

```bat
cd tools\court_brain
py -3 -m courtbrain --install     & rem copy the mod into EU5's mod folder
py -3 -m courtbrain               & rem open the Court Brain window
```

`run_court_brain.bat` opens the window without a console.
`run_court_brain_console.bat` keeps the console open.
`check.bat` (or `--check`) checks the installation.
`--saves` shows the memory attached to each save.

From the sources, `config.json` and `instructions.json` live in
`tools/court_brain/`. Both are ignored by git because they may hold your API
keys. Copy `config.example.json` to start, or let Court Brain create it.

Don't run `--install` while EU5 is open. Replacing the mod under a running game
mixes old and new scripts and crashes it. Court Brain refuses to do it.

## Generated files: never edit them by hand

Some mod files are written by the generators. Change the generator, or the
Court Brain catalogue it reads, and run it again from the repository root:

| Generator | Writes | Run it after changing |
|---|---|---|
| `gen_queue.py` | `votc_queue_effects.txt` (the consequence queue) | `courtbrain/actions.py` |
| `gen_policies.py` | `votc_policies.txt` + loc | policy levers, strengths, income bands |
| `gen_standing.py` | standing measures | `gen_standing.py` |
| `gen_live.py` | the live picture (heartbeat) | `gen_live.py` |
| `gen_battle.py` | "Command the battle" | `courtbrain/battle.py` |
| `gen_simulated.py` | `votc_simulated.txt` + `courtbrain/simulated.py` | a game update (needs the game installed) |

The queue's numbering must never shift: a slot id may already be parked in a
player's save. New variants are always appended at the end of
`actions.variants()`.

## Checking the mod

```bat
py -3 tools\validate_mod.py
```

It checks braces, undefined `votc_*` effects, missing parameters, missing
localisation keys, missing UTF-8 BOMs and tokens that the game's own script never
uses, all without starting EU5. After a game update, refresh its vocabulary:
`--game "<EU5 install folder>"`.

EU5 reads the mod's files as written: UTF-8 **with BOM** and LF line endings.
`.gitattributes` keeps git from converting them.

## Building the exe

```bat
py -3 tools\build_exe.py
```

It runs `validate_mod.py`, installs PyInstaller if missing (only needed to
build), draws the icon and writes `dist\WhispersInTheCourt.exe` (about 12 MB),
with Python, Court Brain and a copy of the mod inside. At start the exe installs
or updates that mod copy in EU5's mod folder.

Releases are built by GitHub Actions (`.github/workflows/build.yml`). Push a
tag such as `v0.7.0` to build the exe from that tag and attach it to a GitHub
release, with its SHA-256. The **Actions** tab can also build it by hand, without
a release.

When the mod changes in a way Court Brain must know about, raise `MOD_VERSION`
in `courtbrain/app.py` and `votc_v_modver` in
`mod/.../script_values/votc_runtime_values.txt` together. Court Brain warns
when the game runs another version of the mod.

## Testing in the game

The mod needs EU5 started with `-debug_mode`. Use a save of your own made for
testing, never a campaign you care about: consequences are real and the
campaign memory is kept per save.
