# Notice

**Whispers in the Court** is a fan-made mod. It is not affiliated with, endorsed
by or sponsored by Paradox Interactive. *Europa Universalis*, *Europa Universalis V*
and Paradox Interactive are trademarks of Paradox Interactive AB. The mod needs
a legitimate copy of the game to run.

## What the MIT license covers

The [MIT license](LICENSE) covers what was written for this project:

- the mod's script, interface and localisation files (`mod/`, `extras/`);
- Court Brain, the Python middleware (`tools/court_brain/`);
- the generators and build tools (`tools/*.py`);
- the documentation (`README.md`, `docs/ARCHITETTURA.md`, these files);
- the icon and cover art, drawn by code (`tools/build_exe.py`, `tools/make_cover.py`).

## What it does not cover

These belong to Paradox Interactive and are used under its terms for mods.
They are not offered under the MIT license:

- `mod/WhispersInTheCourt/main_menu/gfx/interface/icons/unit_ability/votc_take_command.dds`
  is an unchanged copy of the game's own `march_to_sound_of_guns.dds` icon. It is
  shipped under this name because the game takes a unit ability's icon from the
  ability's name.
- The numbers inside `mod/.../static_modifiers/votc_simulated.txt` are copied from
  the game's own reforms, laws and privileges by `tools/gen_simulated.py`.
- `docs/eu5_*.txt` list the names of the game's effects, triggers, modifiers and
  casus belli. They are used to check the mod's script without starting the game.
- The screenshots in `workshop/screenshots/` show the game.

The game files that Court Brain copies and patches on the player's machine
(`courtbrain/hidedebug.py` and `courtbrain/battleview.py`) are never part of
this repository. They are copied from the player's own installed game at every
`--install`.

## Third-party software

- **Python** (PSF License) and its standard library. The optional ChatGPT integration additionally uses PyJWT, cryptography and jsonschema
  with their dependencies; license texts are supplied in the handoff package.
- **PyInstaller** (GPL 2.0 with the bootloader exception) packs the release exe.
  It is used only to build and is not part of the source.
- The AI services Court Brain can talk to (Player2, Google Gemini, Mistral,
  OpenRouter) have their own terms. Each player accepts those terms with their
  own account.


NoDebug experimental changes (2026-10-17): variable-based text delivery, read-only binary save conversion, isolated installation, offline diagnostic events. Includes librakaly 0.12.8 (MIT), https://github.com/rakaly/librakaly/releases/tag/v0.12.8; license in tools/court_brain/courtbrain/vendor/LICENSE-librakaly.txt.

0.8.5 RU NoDebug adaptation (2026-10-22): preserves the earlier RU/ChatGPT/NoDebug changes, adds variable-based text for custom laws, Russian law labels and an isolated migration from 0.8.1.2. Upstream game mechanics are retained; historical validation is documented in README-RU.md.


English NoDebug EN1 (2026-10-24): derived from RU3, with an English companion UI,
English default output, isolated configuration/memory/mod paths, and no automatic
import of personal settings or credentials. Developed with OpenAI Codex assistance;
original authorship and the MIT license are retained. See docs/NODEBUG-TECHNICAL-NOTES.md.
The achievement screenshot in the handoff package was supplied by the player and
shows Steam/game interface content; it is evidence, not source code covered by MIT.
The NoDebug path does not install hidedebug.py's optional debug-tooltip patches.
