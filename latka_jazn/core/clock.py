from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from time import monotonic_ns, perf_counter
from typing import Any, Mapping
import email.utils
import json, os, platform, urllib.request

from .timestamp_policy import (
    TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT,
    TIMESTAMP_MAX_AGE_SECONDS,
    TIMESTAMP_NETWORK_FIRST_DEFAULT,
    TIMESTAMP_NETWORK_TIMEOUT_SECONDS,
    timestamp_runtime_policy,
)

POLISH_WEEKDAYS = {
    0: "poniedziałek", 1: "wtorek", 2: "środa", 3: "czwartek",
    4: "piątek", 5: "sobota", 6: "niedziela"
}

# Canonical env var plus host-loader aliases.  These values must be supplied
# explicitly by the ChatGPT/OpenAI host, wrapper, or another trusted launcher.
# The runtime never promotes the local machine clock to trusted host time.
TRUSTED_HOST_TIME_ISO_ENV_NAMES = (
    "JAZN_TRUSTED_TIME_ISO",
    "JAZN_HOST_TIME_ISO",
    "CHATGPT_HOST_TIME_ISO",
    "OPENAI_HOST_TIME_ISO",
)
TRUSTED_HOST_TIME_SOURCE_ENV_NAMES = (
    "JAZN_TRUSTED_TIME_SOURCE",
    "JAZN_HOST_TIME_SOURCE",
    "CHATGPT_HOST_TIME_SOURCE",
    "OPENAI_HOST_TIME_SOURCE",
)
TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES = (
    "JAZN_TRUSTED_TIME_ANCHOR_MONOTONIC_NS",
)
TRUSTED_HOST_TIME_MAX_AGE_ENV_NAMES = (
    "JAZN_TRUSTED_TIME_MAX_AGE_SECONDS",
    "JAZN_HOST_TIME_MAX_AGE_SECONDS",
    "CHATGPT_HOST_TIME_MAX_AGE_SECONDS",
    "OPENAI_HOST_TIME_MAX_AGE_SECONDS",
)
TRUSTED_HOST_TIME_SOURCE_PREFIXES = (
    "chatgpt_web_time",
    "chatgpt_web_time_tool",
    "chatgpt_loader_time",
    "chatgpt_host_time",
    "openai_web_time_tool",
    "openai_host_time",
    "external_trusted_time",
    "injected_trusted_time",
    "host_injected_time",
)
NETWORK_TIME_SOURCE_PREFIXES = (
    "http://",
    "https://",
    "network_",
    "ntp_",
    "test_network",
)

@dataclass(slots=True)
class TimeSample:
    dt: datetime
    source: str
    trusted: bool
    error: str | None = None


@dataclass(slots=True)
class NetworkTimeCheckResult:
    status: str
    source: str | None = None
    datetime_iso: str | None = None
    error: str | None = None
    elapsed_ms: int = 0
    timeout_seconds: float = 1.5
    urls_tried: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    does_not_block_startup: bool = True
    time_trust_state: str = "unknown_time_source"
    fallback_sample: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _last_sunday_utc(year: int, month: int) -> datetime:
    """Return the last Sunday of a month at 01:00 UTC.

    This is used only for the emergency Europe/Warsaw fallback when the
    platform has no IANA timezone database and the optional tzdata package is
    not installed. It is not a replacement for ZoneInfo/tzdata.
    """
    candidate = datetime(year, month, 31, 1, 0, 0, tzinfo=timezone.utc)
    while candidate.weekday() != 6:  # Sunday
        candidate -= timedelta(days=1)
    return candidate


def _fallback_warsaw_timezone(now_utc: datetime | None = None) -> timezone:
    """Best-effort fixed-offset fallback for current Europe/Warsaw time.

    The correct path is ZoneInfo("Europe/Warsaw") backed by system tzdata or
    the Python tzdata package. On Windows this data may be missing. In that
    case we prefer a clearly degraded fixed-offset fallback over crashing at
    startup. The fallback uses the modern EU DST boundaries for the current
    date, but it does not provide full historical/future IANA rules.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    start_dst = _last_sunday_utc(now_utc.year, 3)
    end_dst = _last_sunday_utc(now_utc.year, 10)
    offset_hours = 2 if start_dst <= now_utc < end_dst else 1
    return timezone(
        timedelta(hours=offset_hours),
        name=f"Europe/Warsaw-fallback-fixed-UTC+{offset_hours:02d}",
    )


def resolve_timezone(timezone_name: str = "Europe/Warsaw"):
    """Return an IANA timezone or a controlled fallback instead of crashing startup."""
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Europe/Warsaw":
            return _fallback_warsaw_timezone()
        return timezone.utc


@dataclass(slots=True)
class TimeSourceResolution:
    platform_system: str
    os_name: str
    shell: str
    terminal: str
    timestamp_source: str
    timestamp_source_detail: str
    timestamp_trusted: bool
    timestamp_freshness_ok: bool
    timestamp_freshness_seconds: int
    timezone_key: str
    utc_iso: str
    local_iso: str
    human_time_header: str
    status: str
    timezone_status: str
    degradation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TimeSourceResolver:
    """Central, environment-aware timestamp resolver.

    Python's aware UTC clock is the local baseline. Network and injected time
    may raise trust, but the resolver never labels system time as network time.
    Missing IANA data degrades the Warsaw conversion without crashing runtime.
    """

    def __init__(
        self,
        timezone_name: str = "Europe/Warsaw",
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.timezone_name = timezone_name
        self.env = env if env is not None else os.environ
        self.timezone_degraded = False
        self.timezone_degradation_reason: str | None = None
        try:
            self.tz = ZoneInfo(timezone_name)
            self.timezone_status = "iana_zoneinfo"
        except ZoneInfoNotFoundError:
            self.tz = resolve_timezone(timezone_name)
            self.timezone_degraded = True
            self.timezone_status = "degraded_fixed_offset" if timezone_name == "Europe/Warsaw" else "degraded_utc"
            self.timezone_degradation_reason = (
                f"ZoneInfo timezone '{timezone_name}' is unavailable; install the tzdata package on Windows or provide an IANA tzdb. "
                "Runtime uses a controlled fallback and does not claim full timezone accuracy."
            )

    def environment(self) -> dict[str, str]:
        return {
            "platform_system": platform.system() or "unknown",
            "os_name": os.name or "unknown",
            "shell": self._detect_shell(self.env),
            "terminal": self._detect_terminal(self.env),
        }

    @staticmethod
    def _detect_shell(env: Mapping[str, str]) -> str:
        shell = str(env.get("SHELL") or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
        if shell:
            if "bash" in shell:
                return "bash"
            if "zsh" in shell:
                return "zsh"
            if "fish" in shell:
                return "fish"
            return shell
        if env.get("PSModulePath") or env.get("POWERSHELL_DISTRIBUTION_CHANNEL"):
            return "powershell"
        comspec = str(env.get("ComSpec") or env.get("COMSPEC") or "").replace("\\", "/").lower()
        if comspec.endswith("/cmd.exe"):
            return "cmd"
        return "unknown"

    @staticmethod
    def _detect_terminal(env: Mapping[str, str]) -> str:
        if env.get("WT_SESSION"):
            return "windows_terminal"
        term_program = str(env.get("TERM_PROGRAM") or "").strip().lower()
        if term_program:
            return term_program
        term = str(env.get("TERM") or "").strip().lower()
        return term or "unknown"

    @staticmethod
    def _classify_source(sample: TimeSample) -> str:
        source = str(sample.source or "").strip().lower()
        if sample.trusted and (source.startswith(NETWORK_TIME_SOURCE_PREFIXES) or "#http-date" in source):
            return "network"
        if sample.trusted and source.startswith(TRUSTED_HOST_TIME_SOURCE_PREFIXES):
            return "host_injected"
        if source in {"system_utc", "utc_system_clock"}:
            return "system_utc"
        if source in {"local_fallback", "system_local", "local_machine"}:
            return "system_local"
        if source:
            return "runtime_fallback"
        return "unavailable"

    @staticmethod
    def _header(local_dt: datetime, timezone_name: str) -> str:
        offset_seconds = int(local_dt.utcoffset().total_seconds()) if local_dt.utcoffset() else 0
        offset_hours = offset_seconds // 3600
        sign = "+" if offset_hours >= 0 else ""
        return f"[🕒 {local_dt:%Y-%m-%d %H:%M:%S} GMT{sign}{offset_hours}, {POLISH_WEEKDAYS[local_dt.weekday()]}, {timezone_name}]"

    def resolve(self, sample: TimeSample, *, max_age_seconds: int = TIMESTAMP_MAX_AGE_SECONDS) -> TimeSourceResolution:
        environment = self.environment()
        degradation: list[str] = []
        sample_dt = sample.dt
        if sample_dt.tzinfo is None:
            sample_dt = sample_dt.replace(tzinfo=timezone.utc)
            degradation.append("naive_datetime_assumed_utc")
        utc_now = datetime.now(timezone.utc)
        sample_utc = sample_dt.astimezone(timezone.utc)
        local_dt = sample_utc.astimezone(self.tz)
        freshness_seconds = abs(int((utc_now - sample_utc).total_seconds()))
        freshness_ok = freshness_seconds <= max_age_seconds
        source_class = self._classify_source(sample)
        trusted = bool(sample.trusted and source_class in {"network", "host_injected"} and freshness_ok)
        if self.timezone_degradation_reason:
            degradation.append(self.timezone_degradation_reason)
        if sample.error:
            degradation.append(str(sample.error))
        if not freshness_ok:
            degradation.append(f"timestamp_stale:{freshness_seconds}s>{max_age_seconds}s")
        if source_class == "host_injected":
            degradation.append("network_time_unavailable_using_host_injected_time")
        elif source_class != "network":
            degradation.append("local_machine_time_unverified")
        status = "active_trusted" if trusted and not self.timezone_degraded else "active_degraded"
        if source_class == "unavailable":
            status = "unavailable"
        return TimeSourceResolution(
            **environment,
            timestamp_source=source_class,
            timestamp_source_detail=str(sample.source or "unavailable"),
            timestamp_trusted=trusted,
            timestamp_freshness_ok=freshness_ok,
            timestamp_freshness_seconds=freshness_seconds,
            timezone_key=self.timezone_name,
            utc_iso=sample_utc.isoformat(),
            local_iso=local_dt.isoformat(),
            human_time_header=self._header(local_dt, self.timezone_name),
            status=status,
            timezone_status=self.timezone_status,
            degradation_reason="; ".join(dict.fromkeys(degradation)) or None,
        )


class WarsawClock:
    def __init__(self, timezone_name: str = "Europe/Warsaw") -> None:
        self.timezone_name = timezone_name
        self.resolver = TimeSourceResolver(timezone_name)
        self.tz = self.resolver.tz
        self.degraded = self.resolver.timezone_degraded
        self.degraded_reason = self.resolver.timezone_degradation_reason
        self.last_sample: TimeSample | None = None

    def now(
        self,
        network_first: bool = TIMESTAMP_NETWORK_FIRST_DEFAULT,
        *,
        allow_fallback: bool = TIMESTAMP_LOCAL_FALLBACK_ALLOWED_DEFAULT,
        timeout_seconds: float = TIMESTAMP_NETWORK_TIMEOUT_SECONDS,
    ) -> TimeSample:
        injected = self._injected_trusted_time()
        if injected:
            self.last_sample = injected
            return injected
        if network_first:
            sample = self._network_time(timeout_seconds=timeout_seconds)
            if sample:
                self.last_sample = sample
                return sample
            if not allow_fallback:
                sample = TimeSample(
                    datetime.now(self.tz),
                    "network_time_unavailable_no_fallback",
                    False,
                    error="network time unavailable and local fallback disabled",
                )
                self.last_sample = sample
                return sample
        sample = self._local_time_sample()
        self.last_sample = sample
        return sample

    def network_time_check(self, *, timeout_seconds: float = 1.5) -> dict[str, Any]:
        started = perf_counter()
        urls_tried: list[str] = []
        attempts: list[dict[str, Any]] = []
        try:
            injected = self._injected_trusted_time()
            if injected:
                elapsed_ms = int((perf_counter() - started) * 1000)
                return NetworkTimeCheckResult(
                    status="ok",
                    source=injected.source,
                    datetime_iso=injected.dt.isoformat(),
                    elapsed_ms=elapsed_ms,
                    timeout_seconds=timeout_seconds,
                    urls_tried=[injected.source],
                    attempts=[{
                        "url": injected.source,
                        "status": "trusted_host_time",
                        "elapsed_ms": elapsed_ms,
                    }],
                    does_not_block_startup=True,
                    time_trust_state="trusted_host_time_network_unavailable",
                ).to_dict()
            sample = self._network_time(
                timeout_seconds=timeout_seconds,
                urls_tried=urls_tried,
                attempts=attempts,
            )
            elapsed_ms = int((perf_counter() - started) * 1000)
            if sample is None:
                fallback = self._local_time_sample()
                return NetworkTimeCheckResult(
                    status="unavailable",
                    error="network time unavailable; using explicit local machine fallback; this does not block runtime startup",
                    elapsed_ms=elapsed_ms,
                    timeout_seconds=timeout_seconds,
                    urls_tried=urls_tried,
                    attempts=attempts,
                    does_not_block_startup=True,
                    time_trust_state="network_time_unavailable_local_machine_unverified",
                    fallback_sample={
                        "source": fallback.source,
                        "trusted": fallback.trusted,
                        "datetime_iso": fallback.dt.isoformat(),
                        "error": fallback.error,
                    },
                ).to_dict()
            return NetworkTimeCheckResult(
                status="ok",
                source=sample.source,
                datetime_iso=sample.dt.isoformat(),
                elapsed_ms=elapsed_ms,
                timeout_seconds=timeout_seconds,
                urls_tried=urls_tried,
                attempts=attempts,
                does_not_block_startup=True,
                time_trust_state="trusted_time",
            ).to_dict()
        except Exception as exc:
            elapsed_ms = int((perf_counter() - started) * 1000)
            return NetworkTimeCheckResult(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed_ms,
                timeout_seconds=timeout_seconds,
                urls_tried=urls_tried,
                attempts=attempts,
                does_not_block_startup=True,
                time_trust_state="network_time_check_error_local_machine_available",
                fallback_sample={
                    "source": self._local_time_sample().source,
                    "trusted": False,
                    "datetime_iso": self._local_time_sample().dt.isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            ).to_dict()

    def _network_time(
        self,
        *,
        timeout_seconds: float = 1.5,
        urls_tried: list[str] | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> TimeSample | None:
        # WorldTimeAPI was sunset in 2026.  Keep one documented JSON time API
        # and two independent RFC 9110 Date-header probes.  A short provider
        # set prevents a network-denied sandbox from multiplying the timeout by
        # a long list of unrelated websites.
        json_urls = [
            "https://timeapi.io/api/TimeZone/zone?timeZone=Europe/Warsaw",
        ]
        http_date_urls = [
            "https://api.github.com",
            "https://www.google.com/generate_204",
        ]
        headers = {
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "User-Agent": "LatkaJazn-TimeProbe/15.0.2",
        }

        def note(url: str, status: str, started: float, **extra: Any) -> None:
            if attempts is None:
                return
            payload: dict[str, Any] = {
                "url": url,
                "status": status,
                "elapsed_ms": int((perf_counter() - started) * 1000),
            }
            payload.update(extra)
            attempts.append(payload)

        for url in json_urls:
            if urls_tried is not None:
                urls_tried.append(url)
            started = perf_counter()
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                    date_header = response.headers.get("Date")
                    body = response.read(256_000).decode("utf-8", errors="replace")
                data = json.loads(body)
                raw = data.get("datetime") or data.get("currentLocalTime") or data.get("dateTime")
                candidates: list[TimeSample] = []
                if raw:
                    normalized = str(raw).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(normalized)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=self.tz)
                    candidates.append(TimeSample(dt.astimezone(self.tz), url, True))
                if date_header:
                    parsed = email.utils.parsedate_to_datetime(date_header)
                    candidates.append(TimeSample(parsed.astimezone(self.tz), url + "#http-date", True))
                if not candidates:
                    note(url, "invalid_response", started, error="no supported time field or Date header")
                    continue
                now_utc = datetime.now(timezone.utc)
                freshest = min(
                    candidates,
                    key=lambda candidate: abs(
                        int((now_utc - candidate.dt.astimezone(timezone.utc)).total_seconds())
                    ),
                )
                freshest_age = abs(
                    int((now_utc - freshest.dt.astimezone(timezone.utc)).total_seconds())
                )
                if freshest_age <= TIMESTAMP_MAX_AGE_SECONDS:
                    note(url, "ok", started, source=freshest.source, freshness_seconds=freshest_age)
                    return freshest
                note(
                    url,
                    "stale",
                    started,
                    source=freshest.source,
                    freshness_seconds=freshest_age,
                    max_age_seconds=TIMESTAMP_MAX_AGE_SECONDS,
                )
            except Exception as exc:
                note(url, "error", started, error=f"{type(exc).__name__}: {exc}")

        # RFC 9110 defines Date as the message origination time.  HEAD keeps the
        # probe small; no response body is needed.  We still reject a stale Date
        # rather than silently trusting cached or delayed metadata.
        for url in http_date_urls:
            if urls_tried is not None:
                urls_tried.append(url)
            started = perf_counter()
            try:
                req = urllib.request.Request(url, headers=headers, method="HEAD")
                with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                    date_header = response.headers.get("Date")
                if not date_header:
                    note(url, "invalid_response", started, error="missing Date header")
                    continue
                parsed = email.utils.parsedate_to_datetime(date_header).astimezone(self.tz)
                freshness_seconds = abs(
                    int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
                )
                if freshness_seconds > TIMESTAMP_MAX_AGE_SECONDS:
                    note(
                        url,
                        "stale",
                        started,
                        source=url + "#http-date",
                        freshness_seconds=freshness_seconds,
                        max_age_seconds=TIMESTAMP_MAX_AGE_SECONDS,
                    )
                    continue
                sample = TimeSample(parsed, url + "#http-date", True)
                note(url, "ok", started, source=sample.source, freshness_seconds=freshness_seconds)
                return sample
            except Exception as exc:
                note(url, "error", started, error=f"{type(exc).__name__}: {exc}")
        return None

    def _first_env_value(self, names: tuple[str, ...]) -> tuple[str, str] | tuple[None, None]:
        for name in names:
            value = str(os.environ.get(name, "")).strip()
            if value:
                return value, name
        return None, None

    def _injected_trusted_time(self) -> TimeSample | None:
        raw, raw_env_name = self._first_env_value(TRUSTED_HOST_TIME_ISO_ENV_NAMES)
        if not raw:
            return None
        source, _source_env_name = self._first_env_value(TRUSTED_HOST_TIME_SOURCE_ENV_NAMES)
        if not source:
            source = "chatgpt_loader_time" if raw_env_name == "JAZN_TRUSTED_TIME_ISO" else f"{str(raw_env_name).lower()}_alias"
        max_age_seconds = self._injected_time_max_age_seconds() or TIMESTAMP_MAX_AGE_SECONDS
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            # A trusted host timestamp is an anchor, not a frozen wall-clock value.
            # When the loader records a monotonic anchor together with the sample,
            # advance the sample by elapsed monotonic time. This preserves the
            # externally established wall-clock offset while avoiding a stale
            # timestamp after a long-lived daemon has been running for > max_age.
            anchor_raw, _anchor_env_name = self._first_env_value(TRUSTED_HOST_TIME_MONOTONIC_ANCHOR_ENV_NAMES)
            if anchor_raw:
                anchor_ns = int(anchor_raw)
                current_ns = monotonic_ns()
                if anchor_ns < 0 or current_ns < anchor_ns:
                    return None
                dt = dt + timedelta(microseconds=(current_ns - anchor_ns) // 1_000)

            freshness_seconds = abs(int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
            if freshness_seconds > max_age_seconds:
                return None
            return TimeSample(dt.astimezone(self.tz), source, True)
        except Exception:
            return None

    def _injected_time_max_age_seconds(self) -> int | None:
        raw, _env_name = self._first_env_value(TRUSTED_HOST_TIME_MAX_AGE_ENV_NAMES)
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _source_is_injected_trusted_time(source: str | None) -> bool:
        value = str(source or "").strip().lower()
        return value.startswith(TRUSTED_HOST_TIME_SOURCE_PREFIXES) or value == "host_injected"

    def _local_time_sample(self) -> TimeSample:
        return TimeSample(datetime.now(self.tz), "local_fallback", False)

    def header(self, sample: TimeSample | None = None, *, network_first: bool = TIMESTAMP_NETWORK_FIRST_DEFAULT) -> str:
        # P0 timestamp: gdy nie przekazano próbki, header sam próbuje czasu sieciowego.
        # Lokalny fallback pozostaje jawnie nieufny w TimeSample.trusted/source.
        sample = sample or self.now(network_first=network_first)
        return self.resolver.resolve(sample).human_time_header

    def sample_contract(self, sample: TimeSample | None = None) -> dict[str, Any]:
        sample = sample or self.last_sample or self.now(network_first=TIMESTAMP_NETWORK_FIRST_DEFAULT)
        policy = timestamp_runtime_policy()
        injected_max_age = self._injected_time_max_age_seconds()
        if injected_max_age is not None and self._source_is_injected_trusted_time(sample.source):
            # Keep injected-time freshness validation consistent with the clock acceptance policy.
            policy["max_age_seconds"] = injected_max_age
        resolution = self.resolver.resolve(sample, max_age_seconds=int(policy["max_age_seconds"]))
        return {
            **policy,
            **resolution.to_dict(),
            "timestamp_header": resolution.human_time_header,
            "sample_iso": sample.dt.isoformat(),
            "source": sample.source,
            "trusted": sample.trusted,
            "error": sample.error,
        }
