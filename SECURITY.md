# Security and privacy

Court Brain (`WhispersInTheCourt.exe`) is a program you run on your own PC.
Here is exactly what it does, so you can check it against the source.

## What it connects to

Only the AI service you chose in its window, and nothing else. There is no
telemetry, no update check and no analytics.

| Service | Address | When |
|---|---|---|
| Player2 (default) | `http://127.0.0.1:<port>/v1`, the Player2 app on your own PC | always, unless you choose another provider |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | only with your own Gemini key |
| Mistral | `https://api.mistral.ai/v1` | only with your own Mistral key |
| OpenRouter | `https://openrouter.ai/api/v1` | only with your own OpenRouter key |

What is sent: the text of the scene (your words, a summary of your realm and of
the characters taken from your save, and the campaign's memory). Only the
provider you chose receives it. All requests are in `courtbrain/player2.py`.

## What it reads and writes

- **Settings:** `%LOCALAPPDATA%\WhispersInTheCourt\config.json` for the exe, or
  `tools/court_brain/config.json` when run from the sources. API keys are stored
  only here, in plain text, on your PC. They are never written anywhere else and
  never shown in the log.
- **The mod:** `Documents\Paradox Interactive\Europa Universalis V\mod\WhispersInTheCourt`.
  It is installed or updated there, but never while EU5 is running. At install it
  copies a few interface files from your own copy of the game into the mod, to
  hide the debug boxes and add the battle button.
- **The game's user folder:** it reads your saves and the game's logs. It writes
  the mod's texts (the localisation that carries the scenes into the game) and
  the campaign memory in `...\Europa Universalis V\court_brain\`.
- **Processes:** it checks with `tasklist` whether `eu5.exe` is running. When you
  press **Start EU5**, it starts the game with `-debug_mode`.

## Verifying a release

Each release lists the SHA-256 of `WhispersInTheCourt.exe`, and the
`WhispersInTheCourt.exe.sha256` file next to it holds the same value. The exe
is built from the tagged source by the GitHub Actions workflow in
`.github/workflows/build.yml`. You can check your download in PowerShell:

```powershell
Get-FileHash .\WhispersInTheCourt.exe -Algorithm SHA256
```

Or skip the exe completely and run Court Brain from the sources (see
[docs/DEVELOPING.md](docs/DEVELOPING.md)).

The exe is not code-signed, so Windows SmartScreen may warn about it.

## Reporting a problem

If you find a security problem (for example, a way for a save file or an AI
answer to make Court Brain write outside its folders), please report it
privately through GitHub's **Report a vulnerability** button on the Security
tab rather than in a public issue.
