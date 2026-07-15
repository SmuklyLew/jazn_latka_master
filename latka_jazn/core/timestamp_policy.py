from __future__ import annotations

# P0: jeden punkt prawdy dla widocznego czasu Jaźni.
# Timestamp jest częścią ciągłości i kontraktu prawdy, nie ozdobą UI.

TIMESTAMP_TIMEZONE = "Europe/Warsaw"
TIMESTAMP_NETWORK_FIRST_DEFAULT = True
TIMESTAMP_NETWORK_IN_NORMAL_TURN_DEFAULT = True
TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT = True
TIMESTAMP_NETWORK_TIMEOUT_SECONDS = 1.5
TIMESTAMP_MAX_AGE_SECONDS = 120
TIMESTAMP_REQUIRE_TRUSTED_IN_FINAL_VISIBLE = True
TIMESTAMP_ALLOW_DEGRADED_LOCAL_VISIBLE = True
from latka_jazn.version import schema_version

TIMESTAMP_POLICY_SCHEMA = schema_version("timestamp_runtime_policy")


def timestamp_runtime_policy() -> dict:
    return {
        "schema_version": TIMESTAMP_POLICY_SCHEMA,
        "timezone": TIMESTAMP_TIMEZONE,
        "network_first_default": TIMESTAMP_NETWORK_FIRST_DEFAULT,
        "network_time_in_normal_turn_default": TIMESTAMP_NETWORK_IN_NORMAL_TURN_DEFAULT,
        "local_fallback_allowed_default": TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT,
        "network_timeout_seconds": TIMESTAMP_NETWORK_TIMEOUT_SECONDS,
        "max_age_seconds": TIMESTAMP_MAX_AGE_SECONDS,
        "require_trusted_in_final_visible": TIMESTAMP_REQUIRE_TRUSTED_IN_FINAL_VISIBLE,
        "allow_degraded_local_visible": TIMESTAMP_ALLOW_DEGRADED_LOCAL_VISIBLE,
        "truth_boundary": (
            "Widoczny timestamp ma preferować czas sieciowy albo zaufany czas wstrzyknięty przez loader. "
            "Lokalny fallback jest dopuszczalny jako jawnie zdegradowany tryb awaryjny, "
            "który nie blokuje zwykłej rozmowy, ale nie jest pełnoprawnym aktualnym czasem internetowym."
        ),
    }
