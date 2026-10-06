# Contributing

Thanks for wanting to help. Bug reports, translations, balance ideas and code are
all welcome.

## Reporting a bug

Open an issue with:

- what you did and what happened (a screenshot helps);
- the versions of EU5 and of Whispers in the Court;
- the Court Brain record (the text in its window) around the problem;
- for a crash: the newest folder in `Documents\Paradox Interactive\Europa Universalis V\crashes`,
  and `logs\error.log`.

Don't paste your `config.json` without removing your API keys first.

## Changing the code

See [docs/DEVELOPING.md](docs/DEVELOPING.md) for the layout, running from
the sources, the generators and the build.

Before a pull request:

- `py -3 tools\validate_mod.py` reports nothing;
- if you changed the catalogue or a generator's input, run the generator again
  and commit its output. Never renumber the consequence queue: new variants go
  at the end;
- test in the game with a save made for testing.

What the AI is told lives in `courtbrain/prompts.py`. Keep the characters'
speech human: no game terms, numbers or menus in what they say.

By contributing you agree that your work is published under the
[MIT license](LICENSE).
