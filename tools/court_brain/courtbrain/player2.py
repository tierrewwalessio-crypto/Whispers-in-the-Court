"""Client for the Player2 desktop app's local API.

The app serves an OpenAI-shaped API on 127.0.0.1. Two details of the real
API drive the design here:

* The port is discovered from %APPDATA%/game.player2.client/api.port and only
  falls back to 4315. The file disappears when the app exits cleanly, so its
  absence doubles as "Player2 is not running".
* localhost resolves to ::1 first on Windows and the app binds v4 only, so
  the host is always written as 127.0.0.1.

Only the standard library is used, so the middleware installs with nothing
but a Python interpreter.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Any


class Player2Error(RuntimeError):
    """Any failure to get an answer out of the Player2 app."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Player2Client:
    NAME = "Player2"
    # Waits before the next try when the server is overloaded (HTTP 5xx), in seconds.
    SERVER_WAITS = (4, 10, 25)

    def __init__(
        self,
        base_url: str,
        game_key: str,
        *,
        timeout: float = 120.0,
        temperature: float = 0.85,
        max_tokens: int = 900,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.game_key = game_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.last_usage = Usage()
        self.last_model = ""
        # The room each kind of request turned out to need (reasoning included). An
        # answer that runs out of room comes back EMPTY and the whole question must be
        # sent again - paid twice - so a kind that once needed more starts with it.
        self.room: dict[str, int] = {}
        self.on_room: Callable[[dict[str, int]], None] | None = None
        # Every answer's cost: (kind, prompt tokens, completion tokens, attempt).
        self.on_usage: Callable[[str, int, int, int], None] | None = None
        # What each request cost in joules, read from the player's balance (see meter()).
        self.on_meter: Callable[[list[dict[str, Any]]], None] | None = None
        self._meter_lock = threading.Lock()
        self._meter_last: int | None = None
        self._meter_pending: list[dict[str, Any]] = []
        self._meter_voice = False
        self._meter_fails = 0
        self.up: bool | None = None          # did the last /health answer? (None: not asked yet)
        self._health_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    def _request(self, path: str, payload: dict[str, Any] | None = None, method: str = "POST") -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        for name, value in self._headers().items():
            req.add_header(name, value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            # 402 is "out of joules" and 429 is a rate limit; both are worth
            # saying out loud rather than retrying into the void.
            hint = self._hints().get(exc.code, "")
            msg = f"{self.NAME} returned HTTP {exc.code}"
            if hint:
                msg += f" ({hint})"
            if detail:
                msg += f": {detail}"
            raise Player2Error(msg, status=exc.code, retryable=exc.code in (429, 500, 502, 503, 504)) from exc
        except urllib.error.URLError as exc:
            raise Player2Error(f"{self._unreachable()} ({exc.reason})", retryable=True) from exc
        except TimeoutError as exc:
            raise Player2Error(f"{self.NAME} took too long to answer", retryable=True) from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise Player2Error(f"{self.NAME} sent something that is not JSON: {body[:200]}") from exc

    # What another provider changes (see CloudClient): how to sign a request, what an
    # error means for the player, what a request carries besides the messages.
    def _headers(self) -> dict[str, str]:
        return {"player2-game-key": self.game_key}

    def _hints(self) -> dict[int, str]:
        return {401: "sign in to the Player2 app", 402: "the Player2 account is out of credit",
                429: "Player2 is rate limiting; slow down"}

    def _unreachable(self) -> str:
        return f"cannot reach the Player2 app at {self.base_url} - is it running?"

    def _extra(self) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any] | None:
        try:
            answer = self._request("/health", method="GET")
        except Player2Error:
            self.up = False
            return None
        self.up = True
        return answer

    def is_up(self) -> bool:
        return self.health() is not None

    def start_health_pings(self, interval: float = 60.0) -> None:
        """Player2 asks integrations to ping /health every 60s.

        It is how the app measures time spent in a game, which feeds its
        reward programme. Doing it is part of using the API politely.
        """
        if self._health_thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(interval):
                self.health()

        self._health_thread = threading.Thread(target=loop, name="player2-health", daemon=True)
        self._health_thread.start()

    # ------------------------------------------------------------------ what it costs
    def balance(self) -> int | None:
        """The player's joules, as Player2 counts them (None if it cannot say)."""
        try:
            return int(self._request("/joules", None, method="GET").get("joules"))
        except (Player2Error, TypeError, ValueError, AttributeError):
            return None

    def meter(self) -> None:
        """Read the balance and charge what it fell by to the requests answered since the
        last reading, in proportion to their tokens: each kind of request, with each model,
        shows what it really costs. Asked before a request only when something is waiting
        to be charged (a local call of a few milliseconds)."""
        with self._meter_lock:
            if not self._meter_pending and self._meter_last is not None:
                return
        balance = self.balance() if self._meter_fails < 3 else None
        self._meter_fails = 0 if balance is not None else self._meter_fails + 1
        with self._meter_lock:
            pending, self._meter_pending = self._meter_pending, []
            last = self._meter_last
            if balance is not None:
                self._meter_last = balance
            voice, self._meter_voice = self._meter_voice, False
        if not pending:
            return
        spent = last - balance if last is not None and balance is not None and last >= balance else None
        total = sum(p["in"] + p["out"] for p in pending) or 1
        for p in pending:
            p["joules"] = round(spent * (p["in"] + p["out"]) / total, 2) if spent is not None else None
            if voice:
                p["voice"] = True
        if self.on_meter is not None:
            try:
                self.on_meter(pending)
            except Exception:  # noqa: BLE001 - accounting must never cost an answer
                pass

    def close(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def complete_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        schema_name: str = "scene",
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 2,
        json_object: bool = False,
    ) -> dict[str, Any]:
        """Ask for an answer that must match `schema`.

        The local API supports response_format json_schema, which is what
        keeps a creative model inside the mod's action vocabulary: it cannot
        invent an effect name if the enum does not contain it. Strict mode is
        requested, and the result is parsed and re-validated here anyway,
        because "the server promised" is not a reason to trust input.
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max(self.max_tokens if max_tokens is None else max_tokens,
                              self.room.get(schema_name, 0)),
            "stream": False,
            "response_format": ({"type": "json_object"} if json_object else {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }),
            **self._extra(),
        }
        last: Exception | None = None
        self.meter()
        attempt = -1
        while attempt < retries:
            attempt += 1
            try:
                data = self._request("/chat/completions", payload)
            except Player2Error as exc:
                last = exc
                # An overloaded server (5xx) usually recovers within a minute: wait longer,
                # and try a little more often, before a scene loses what it decided.
                busy = bool(exc.status and exc.status >= 500)
                if busy:
                    retries = max(retries, len(self.SERVER_WAITS))
                if not exc.retryable or attempt >= retries:
                    raise
                time.sleep(self.SERVER_WAITS[min(attempt, len(self.SERVER_WAITS) - 1)] if busy
                           else 1.5 * (attempt + 1))
                continue
            try:
                first = data["choices"][0]
                choice = first.get("message", {}).get("content")
                finish = first.get("finish_reason") or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise Player2Error(f"unexpected completion shape: {str(data)[:200]}") from exc
            usage = data.get("usage") or {}
            self.last_usage = Usage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            )
            self.last_model = str(data.get("model") or "")
            if self.on_usage is not None:
                try:
                    self.on_usage(schema_name, self.last_usage.prompt_tokens,
                                  self.last_usage.completion_tokens, attempt)
                except Exception:  # noqa: BLE001 - accounting must never cost an answer
                    pass
            with self._meter_lock:
                self._meter_pending.append({"t": time.time(), "kind": schema_name, "model": self.last_model,
                                            "in": self.last_usage.prompt_tokens,
                                            "out": self.last_usage.completion_tokens, "try": attempt + 1})
            if not choice:
                # Running into the token ceiling mid-JSON comes back as a
                # message with no content at all. Give it more room rather
                # than losing the scene; a schema with staged outcomes is
                # simply longer than one without.
                if attempt < retries:
                    # Player2's current model reasons before it answers, and
                    # the reasoning is billed against max_tokens. When it
                    # runs out it returns an EMPTY message with finish_reason
                    # "stop", not "length" (measured: 900 tokens used, empty;
                    # the same request with 3000 succeeds at ~1700). So an
                    # empty answer always means: give it more room.
                    payload["max_tokens"] = min(int(payload["max_tokens"] * 2), 8000)
                    self.room[schema_name] = max(self.room.get(schema_name, 0), payload["max_tokens"])
                    if self.on_room is not None:
                        try:
                            self.on_room(dict(self.room))
                        except Exception:  # noqa: BLE001
                            pass
                    time.sleep(0.8)
                    continue
                raise Player2Error(
                    "the model returned an empty message"
                    + (f" (stopped because: {finish})" if finish else "")
                )
            try:
                return json.loads(choice)
            except json.JSONDecodeError as exc:
                last = exc
                if attempt == retries:
                    raise Player2Error(
                        f"the model did not return valid JSON: {str(choice)[:300]}"
                    ) from exc
                # Nudge it once with its own bad output rather than silently
                # discarding the turn.
                payload["messages"] = messages + [
                    {"role": "assistant", "content": str(choice)[:2000]},
                    {
                        "role": "user",
                        "content": "That was not valid JSON. Reply again with the JSON object only.",
                    },
                ]
        raise Player2Error(f"gave up after {retries + 1} attempts: {last}")

    # ------------------------------------------------------------------
    def voices(self) -> list[dict[str, Any]]:
        try:
            data = self._request("/tts/voices", method="GET") or {}
        except Player2Error:
            return []
        return list(data.get("voices") or [])

    def speak(self, text: str, voice_id: str = "") -> None:
        """Say a line out loud through the Player2 app.

        Fire and forget: a failed TTS call must never cost the player the
        scene they were reading.
        """
        if not text.strip():
            return
        self._meter_voice = True           # a voice line costs joules too: marked in the meter
        # text, play_in_app and speed are all required by SingleTextToSpeechRequest.
        payload: dict[str, Any] = {"text": text[:1800], "play_in_app": True, "speed": 1.0}
        if voice_id:
            payload["voice_ids"] = [voice_id]
        try:
            self._request("/tts/speak", payload)
        except Player2Error:
            pass

    # ------------------------------------------------------------------
    def listen_start(self, timeout_s: float = 30.0) -> bool:
        """Start dictation in the Player2 app.

        The app owns the microphone; Court Brain never touches it. That is
        deliberate - the player already granted Player2 that permission and
        can see in its own window when it is listening.
        """
        seconds = max(3.0, min(60.0, float(timeout_s)))
        try:
            self._request("/stt/start", {"timeout": seconds})
            return True
        except Player2Error:
            return False

    def listen_stop(self) -> str:
        try:
            data = self._request("/stt/stop", {}) or {}
        except Player2Error:
            return ""
        return str(data.get("text") or "").strip()


# ======================================================================
# Other providers: the player's own key for a cloud model
# ======================================================================
# Player2 stays the default. A player may instead connect Court Brain to Google Gemini or
# Mistral with a key of their own (both free within limits): same requests, same answers,
# only who writes changes. Voices and dictation still go through the Player2 app.
PROVIDERS: dict[str, dict[str, str]] = {
    "player2": {"name": "Player2", "base": "", "model": "", "key_url": "https://player2.game", "prefer": ""},
    "gemini": {"name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-3.1-flash-lite", "key_url": "https://aistudio.google.com/apikey", "prefer": "flash"},
    "mistral": {"name": "Mistral", "base": "https://api.mistral.ai/v1", "model": "mistral-large-latest",
                "key_url": "https://console.mistral.ai/api-keys", "prefer": "large"},
    # One key for hundreds of models ("maker/model", e.g. google/gemini-3.1-flash-lite,
    # deepseek/deepseek-v4-flash, anthropic/claude-sonnet-5.5; ":free" ones cost nothing).
    "openrouter": {"name": "OpenRouter", "base": "https://openrouter.ai/api/v1",
                   "model": "google/gemini-3.1-flash-lite", "key_url": "https://openrouter.ai/keys",
                   "prefer": "gemini"},
}

# A 400 about the answer's format: the model's API does not take this JSON schema.
_SCHEMA_REFUSED = re.compile(r"schema|response_format|additionalProperties|unknown name|json", re.I)


class CloudClient(Player2Client):
    """A cloud model through its OpenAI-compatible API, with the player's own key (Google
    Gemini, Mistral). Everything else - voices, dictation, the app's health pings - still goes
    to the Player2 app when it runs; nothing of the mod changes but who writes."""

    def __init__(self, provider: str, api_key: str, model: str, voice: Player2Client, **kw: Any) -> None:
        spec = PROVIDERS[provider]
        super().__init__(spec["base"], "", **kw)
        self.provider = provider
        self.NAME = spec["name"]
        self.api_key = api_key.strip()
        self.model = model.strip() or spec["model"]
        self.voice = voice
        # Some APIs refuse a strict JSON schema that is too large or too deep (Gemini answers a
        # bare "invalid argument"): then, for that kind of request, the schema is given in the
        # request itself and a plain JSON object is asked for - the answer is the same.
        self.loose: set[str] = set()

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "openrouter":
            # how OpenRouter names the app in the player's own activity page
            headers["X-Title"] = "Whispers in the Court"
        return headers

    def _key_ok(self) -> None:
        """OpenRouter lists its models to anyone: the key itself is checked on its own
        (raises Player2Error when it is not valid)."""
        if self.provider != "openrouter":
            return
        try:
            self._request("/key", None, method="GET")
        except Player2Error as exc:
            if exc.status != 404:
                raise
            self._request("/auth/key", None, method="GET")

    def _hints(self) -> dict[int, str]:
        return {400: "the request was refused", 401: "the API key is not valid - check it in Settings",
                403: "this key may not use this model", 404: "unknown model - check its name in Settings",
                429: "the limit of this key is reached for now - wait a little, or switch to Player2 in Settings"}

    def _unreachable(self) -> str:
        return f"cannot reach {self.NAME} - is the computer online?"

    def _extra(self) -> dict[str, Any]:
        return {"model": self.model}

    @staticmethod
    def _with_schema(messages: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
        note = ("Answer with ONE JSON object and nothing else. It must follow this JSON schema exactly "
                "(every required field, no other field):\n" + json.dumps(schema, ensure_ascii=False))
        return list(messages) + [{"role": "user", "content": note}]

    def complete_json(self, messages: list[dict[str, Any]], schema: dict[str, Any], *,
                      schema_name: str = "scene", **kw: Any) -> dict[str, Any]:
        if schema_name in self.loose:
            return super().complete_json(self._with_schema(messages, schema), schema, schema_name=schema_name,
                                         json_object=True, **kw)
        try:
            return super().complete_json(messages, schema, schema_name=schema_name, **kw)
        except Player2Error as exc:
            if exc.status != 400:
                raise
            self.loose.add(schema_name)
            return super().complete_json(self._with_schema(messages, schema), schema, schema_name=schema_name,
                                         json_object=True, **kw)

    def models(self) -> list[str]:
        data = self._request("/models", None, method="GET") or {}
        return [str(m.get("id") or "").removeprefix("models/") for m in data.get("data") or [] if isinstance(m, dict)]

    def check(self) -> tuple[bool, str]:
        """Is the key good, and does the model exist? If the model's name is no longer offered,
        the closest one the key can use is taken (names change often)."""
        try:
            self._key_ok()
            names = self.models()
        except Player2Error as exc:
            self.up = False
            return False, str(exc)
        self.up = True
        if names and self.model not in names and "/" in (names[0] or ""):
            # OpenRouter's names are "maker/model": the model alone ("gemini-3.1-flash-lite") is found by its end
            same = [n for n in names if n.split("/", 1)[-1] == self.model]
            if same:
                old, self.model = self.model, same[0]
                return True, f"model {self.model}"
            default = PROVIDERS[self.provider]["model"]
            if not difflib.get_close_matches(self.model, names, n=1, cutoff=0.75) and default in names:
                old, self.model = self.model, default
                return True, f"model {old} is not offered: using {self.model}"
        if names and self.model not in names:
            usable = [n for n in names if not any(
                x in n for x in ("embed", "tts", "image", "audio", "live", "robotics", "computer"))]
            # what the player typed, completed ("gemini-3.1-flash" -> "gemini-3.1-flash-lite"), else the nearest
            starts = sorted((n for n in usable if n.startswith(self.model) and "preview" not in n), key=len)
            close = starts[:1] or difflib.get_close_matches(self.model, usable, n=1, cutoff=0.75)
            if close:
                old, self.model = self.model, close[0]
                return True, f"model {old} is not offered to this key: using the closest, {self.model}"
            prefer = PROVIDERS[self.provider]["prefer"]
            fits = [n for n in names if prefer in n and "embed" not in n and "lite" not in n and "tts" not in n
                    and "image" not in n and "audio" not in n]
            if fits:
                old, self.model = self.model, sorted(fits, key=lambda n: ("latest" not in n, n), reverse=False)[0] \
                    if any("latest" in n for n in fits) else sorted(fits)[-1]
                return True, f"model {old} is not offered to this key: using {self.model}"
        return True, f"model {self.model}"

    def health(self) -> dict[str, Any] | None:
        ok, _ = self.check()
        return {"provider": self.provider} if ok else None

    def balance(self) -> int | None:
        return None                     # no joules: the provider bills the player's own key

    def start_health_pings(self, interval: float = 60.0) -> None:
        self.voice.start_health_pings(interval)

    def voices(self) -> list[dict[str, Any]]:
        return self.voice.voices()

    def speak(self, text: str, voice_id: str = "") -> None:
        self.voice.speak(text, voice_id)

    def listen_start(self, timeout_s: float = 30.0) -> bool:
        return self.voice.listen_start(timeout_s)

    def listen_stop(self) -> str:
        return self.voice.listen_stop()

    def close(self) -> None:
        self.voice.close()
        super().close()


def make_client(cfg: Any, log: Callable[[str], None] | None = None) -> Player2Client:
    """The client for the provider chosen in Settings; Player2 when none (or no key) is set."""
    kw = {"timeout": cfg.request_timeout_s, "temperature": cfg.model_temperature, "max_tokens": cfg.max_tokens}
    voice = Player2Client(cfg.player2_base, cfg.player2_game_key, **kw)
    provider = str(getattr(cfg, "ai_provider", "") or "player2").lower()
    if provider in PROVIDERS and provider != "player2":
        key = str(getattr(cfg, f"{provider}_api_key", "") or "").strip()
        if key:
            return CloudClient(provider, key, str(getattr(cfg, f"{provider}_model", "") or ""), voice, **kw)
        if log is not None:
            log(f"WARNING: no API key for {PROVIDERS[provider]['name']}: Court Brain uses Player2. "
                "Add the key in Settings.")
    return voice
