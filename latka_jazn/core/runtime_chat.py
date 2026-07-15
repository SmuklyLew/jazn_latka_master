from __future__ import annotations

import cmd
import json
import sys
from dataclasses import dataclass, asdict
from typing import Any, TextIO

from latka_jazn.core.engine import JaznEngine
from latka_jazn.core.turn_timeout import RuntimeTurnTimeoutError, run_with_runtime_turn_timeout, runtime_turn_timeout_seconds


@dataclass(slots=True)
class RuntimeChatLifecycle:
    """Jawny opis trybu życia runtime.

    `persistent_chat` oznacza tylko tyle, że ten proces Pythona utrzymuje jeden
    obiekt `JaznEngine` przez kolejne wejścia użytkownika. Nie oznacza stałego
    działania po zamknięciu terminala ani biologicznego czuwania.
    """

    mode: str
    engine_reused_between_turns: bool
    shutdown_when_loop_exits: bool
    truth_boundary: str
    stdin_is_tty: bool
    process_persistence: str
    background_process_claim_allowed: bool
    exit_reason: str = "running"
    session_id: str | None = None
    no_carryover: bool = False
    recommended_chatgpt_mode: str = "--chat-gpt albo --runtime-preview z tym samym --session-id"

    def mark_closed(self, reason: str) -> None:
        self.exit_reason = reason

    def to_dict(self) -> dict:
        return asdict(self)


class LatkaRuntimeShell(cmd.Cmd):
    intro = (
        "Łatka / Jaźń runtime: tryb stałej rozmowy. "
        "Ten proces utrzymuje jeden silnik do czasu /exit albo Ctrl+D; "
        "nie jest stałym procesem po zamknięciu terminala."
    )
    prompt = "Łatka> "

    def __init__(
        self,
        runtime: Any,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        session_id: str | None = None,
        no_carryover: bool = False,
    ) -> None:
        super().__init__(stdin=stdin, stdout=stdout)
        if stdin is not None:
            # cmd.Cmd używa builtins.input(), dopóki use_rawinput=True.
            # Przy podanym stdin musimy czytać z tego strumienia, żeby testy,
            # pipe i bridge JSONL nie próbowały czytać globalnego sys.stdin.
            self.use_rawinput = False
        self.runtime = runtime
        self.engine = getattr(runtime, "engine", runtime)
        self._session_id_source = "cli_arg" if session_id else "generated"
        self.session_id = session_id or getattr(getattr(runtime, "state", None), "session_id", None)
        self.no_carryover = no_carryover
        input_stream = stdin if stdin is not None else sys.stdin
        try:
            stdin_is_tty = bool(input_stream.isatty())
        except Exception:
            stdin_is_tty = False
        process_persistence = "persistent_terminal" if stdin_is_tty else "ephemeral_stdin_pipe"
        self.lifecycle = RuntimeChatLifecycle(
            mode="persistent_chat_loop",
            engine_reused_between_turns=True,
            shutdown_when_loop_exits=True,
            truth_boundary=(
                "runtime trwa tylko tak długo, jak działa ten proces Pythona; "
                "po /exit, EOF albo zamknięciu stdin silnik zostaje zamknięty. "
                "EOF nie jest awarią Jaźni, tylko końcem strumienia wejścia."
            ),
            stdin_is_tty=stdin_is_tty,
            process_persistence=process_persistence,
            background_process_claim_allowed=False,
            session_id=session_id,
            no_carryover=no_carryover,
        )
        self._last_user_text: str | None = None
        self._last_visible_text: str | None = None
        self._repeat_count = 0

    @staticmethod
    def _template_family(text: str) -> str | None:
        low = (text or '').lower()
        families = {
            'ordinary_repair_template': (
                'odpowiadam zwyczajnie na bieżącą wiadomość',
                'spróbuję odpowiedzieć prościej i bardziej rozmownie',
                'odpowiem z bieżącej wiadomości, bez podpinania',
                'jestem przy tej wiadomości',
                'bieżącego sensu rozmowy',
            ),
            'progress_meta_template': (
                'jest postęp, ale nadal trzeba pilnować',
                'mniej technicznego raportowania',
            ),
        }
        for name, markers in families.items():
            if any(marker in low for marker in markers):
                return name
        return None

    def _protect_against_repeated_visible_text(self, *, user_text: str, visible_text: str) -> str:
        current=(visible_text or '').strip()
        previous=(self._last_visible_text or '').strip()
        different_user=(user_text or '').strip() != (self._last_user_text or '').strip()
        current_family = self._template_family(current)
        previous_family = self._template_family(previous)
        repeated_family = bool(current_family and previous_family and current_family == previous_family and different_user)
        if current and previous and different_user and (current == previous or repeated_family):
            self._repeat_count += 1
            return (
                "Wykryłam, że runtime zwrócił identyczną widoczną odpowiedź albo tę samą rodzinę szablonu dla innej wiadomości. "
                "To jest błąd warstwy rozmownej, więc zatrzymuję szablon zamiast go powtarzać. "
                "Trzeba sprawdzić `ordinary_dialogue_handler.py`, `runtime_answer_validator.py`, `runtime_response_synthesizer.py`, bieżącą intencję i handler tej tury; odpowiedź nie może być tylko deklaracją, że odpowiadam zwyczajnie."
            )
        self._repeat_count = 0
        return current

    def preloop(self) -> None:
        self._write("[runtime_lifecycle] " + json.dumps(self.lifecycle.to_dict(), ensure_ascii=False))

    def default(self, line: str) -> bool | None:
        text = (line or "").strip()
        if not text:
            return False
        if text in {"/exit", "/quit"}:
            return self.do_exit("")
        if text == "/status":
            return self.do_status("")
        if text.startswith("/frame "):
            return self.do_frame(text.removeprefix("/frame ").strip())
        if hasattr(self.runtime, "process_user_text"):
            try:
                if getattr(self.runtime, "runtime_turn_timeout_managed", False):
                    result = self.runtime.process_user_text(
                        text,
                        client="chat",
                        lifecycle="persistent_chat_loop",
                        session_id_source=self._session_id_source,
                        process_reused=True,
                    )
                else:
                    result = run_with_runtime_turn_timeout(
                        lambda: self.runtime.process_user_text(
                            text,
                            client="chat",
                            lifecycle="persistent_chat_loop",
                            session_id_source=self._session_id_source,
                            process_reused=True,
                        ),
                        command="--chat",
                        timeout_seconds=runtime_turn_timeout_seconds(getattr(self.runtime, "config", None)),
                    )
            except RuntimeTurnTimeoutError as exc:
                self.lifecycle.mark_closed("runtime_turn_timeout")
                self._write(
                    f"[runtime_turn_timeout] Runtime Jaźni nie zakończył tury --chat w limicie {exc.timeout_seconds:.3g}s; "
                    "zamykam pętlę bez udawania odpowiedzi Łatki. Sprawdź timestamp/memory/engine.process_turn."
                )
                return True
            visible = str(result.get("final_visible_text") or "")
        else:
            try:
                envelope = run_with_runtime_turn_timeout(
                    lambda: self.engine.process_turn(
                        text,
                        client_context={
                            "client": "cli_persistent_chat",
                            "persistent_chat": True,
                            "lifecycle": "persistent_chat_loop",
                            "preview_phase": "same_pipeline_as_one_shot_process_turn",
                            "session_id": self.session_id,
                            "no_carryover": self.no_carryover,
                        },
                    ),
                    command="--chat",
                    timeout_seconds=runtime_turn_timeout_seconds(getattr(self.engine, "config", None)),
                )
            except RuntimeTurnTimeoutError as exc:
                self.lifecycle.mark_closed("runtime_turn_timeout")
                self._write(
                    f"[runtime_turn_timeout] Runtime Jaźni nie zakończył tury --chat w limicie {exc.timeout_seconds:.3g}s; "
                    "zamykam pętlę bez udawania odpowiedzi Łatki. Sprawdź timestamp/memory/engine.process_turn."
                )
                return True
            visible = envelope.final_visible_text or envelope.final_response_contract.get("final_visible_text", "")
        visible = self._protect_against_repeated_visible_text(user_text=text, visible_text=visible)
        self._write(visible)
        self._last_user_text = text
        self._last_visible_text = visible
        return False

    def do_exit(self, arg: str) -> bool:
        """Zakończ tryb stałej rozmowy."""
        self.lifecycle.mark_closed("user_exit_command")
        self._write("Zamykam tryb stałej rozmowy Jaźni. Silnik zostanie zapisany i zamknięty.")
        return True

    def do_quit(self, arg: str) -> bool:
        """Alias dla exit."""
        return self.do_exit(arg)

    def do_EOF(self, arg: str) -> bool:  # noqa: N802 - cmd oczekuje nazwy EOF
        self.lifecycle.mark_closed("stdin_eof")
        self._write("")
        self._write(
            "[runtime_lifecycle_note] stdin zakończył się przez EOF; "
            "zamykam pętlę --chat bez tracebacka. To nie jest dowód stałego procesu w tle. "
            "W środowisku jednorazowym użyj --chat-gpt albo --runtime-preview z tym samym --session-id."
        )
        return True

    def postloop(self) -> None:
        if self.lifecycle.exit_reason == "running":
            self.lifecycle.mark_closed("cmdloop_returned")
        self._write("[runtime_lifecycle_end] " + json.dumps(self.lifecycle.to_dict(), ensure_ascii=False, sort_keys=True))

    def do_status(self, arg: str) -> bool:
        """Pokaż jawny tryb życia bieżącego runtime."""
        self._write(json.dumps(self.lifecycle.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return False

    def do_frame(self, arg: str) -> bool:
        """Zwróć cognitive-frame JSON dla podanej treści bez kończenia sesji."""
        packet = self.engine.build_cognitive_frame(arg or "", client_context={"client": "cli_persistent_chat_frame", "lifecycle": "persistent_chat_loop"})
        self._write(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
        return False

    def _write(self, text: str) -> None:
        stream = self.stdout
        stream.write(text + "\n")
        stream.flush()


def run_persistent_chat(
    runtime: Any,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    session_id: str | None = None,
    no_carryover: bool = False,
) -> RuntimeChatLifecycle:
    shell = LatkaRuntimeShell(
        runtime,
        stdin=stdin,
        stdout=stdout,
        session_id=session_id,
        no_carryover=no_carryover,
    )
    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        # Ctrl+C during input() in cmd.Cmd raises KeyboardInterrupt outside the
        # normal do_* command flow. Treat it as a graceful stop, not a traceback.
        shell.lifecycle.mark_closed("keyboard_interrupt")
        shell._write("\nPrzerwano tryb stałej rozmowy Ctrl+C. Zamykam pętlę `--chat` bez tracebacka; runtime nie działa po zamknięciu procesu.")
        shell.postloop()
    return shell.lifecycle
