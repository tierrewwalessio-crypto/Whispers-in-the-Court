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

- **Python** (PSF License) and its standard library. Court Brain needs no other
  packages to run.
- **PyInstaller** (GPL 2.0 with the bootloader exception) packs the release exe.
  It is used only to build and is not part of the source.
- The AI services Court Brain can talk to (Player2, Google Gemini, Mistral,
  OpenRouter) have their own terms. Each player accepts those terms with their
  own account.
