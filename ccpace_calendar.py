"""Calendar observations, conditions, and collectors. No terminal rendering."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccpace as cc

SCENARIOS = (
    "mixed",
    "weekly",
    "scoped",
    "stale",
    "cold",
    "reset",
    "credits",
    "rebase",
    "weekly-only",
)
MAX_GAP = 1800


@dataclass(frozen=True)
class Meter:
    key: str
    name: str
    used: float
    reset: float
    duration: int

    def entry(self, now: datetime) -> dict:
        elapsed = self.duration - (self.reset - now.timestamp())
        pace = self.used / 100 / (elapsed / self.duration) if elapsed > 0 else None
        return {
            "name": self.name,
            "util": self.used,
            "reset_dt": datetime.fromtimestamp(self.reset, timezone.utc),
            "length": self.duration,
            "remaining": self.reset - now.timestamp(),
            "elapsed_frac": elapsed / self.duration,
            "pace": pace,
            "active": False,
        }


def meters(payload: dict) -> list[Meter]:
    found: dict[str, Meter] = {}

    def add(key, name, used, reset, duration):
        stamp = cc.parse_reset_dt(reset)
        if (
            isinstance(used, bool)
            or not isinstance(used, (int, float))
            or not math.isfinite(used)
            or used < 0
        ):
            return
        if stamp and stamp.tzinfo:
            found[key] = Meter(key, name, float(used), stamp.timestamp(), duration)

    for field_name, key, name, duration in (
        ("five_hour", "session", "5h", cc.WINDOW_5H_SEC),
        ("seven_day", "weekly_all", "7d all", cc.WINDOW_7D_SEC),
    ):
        item = payload.get(field_name)
        if isinstance(item, dict):
            add(key, name, item.get("utilization"), item.get("resets_at"), duration)
    for item in payload.get("limits") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind in ("session", "weekly_all"):
            add(
                kind,
                "5h" if kind == "session" else "7d all",
                item.get("percent"),
                item.get("resets_at"),
                cc.WINDOW_5H_SEC if kind == "session" else cc.WINDOW_7D_SEC,
            )
        elif kind == "weekly_scoped":
            scope = item.get("scope") or {}
            model = scope.get("model") or {}
            name = (
                model.get("display_name")
                or model.get("id")
                or scope.get("surface")
                or "scoped"
            )
            add(
                f"weekly_scoped:{name}",
                f"7d {name}",
                item.get("percent"),
                item.get("resets_at"),
                cc.WINDOW_7D_SEC,
            )
    return list(found.values())


@dataclass(frozen=True)
class Observation:
    at: float
    meter: Meter


class ObservationStore:
    def __init__(self):
        self.cache: dict[
            Path, tuple[tuple[int, int], dict[str, list[Observation]]]
        ] = {}

    def read(self, paths: list[Path], uuid: str) -> list[Observation]:
        # Directory aliases are placement, not identity. No profile means no history.
        if not uuid:
            return []
        result = []
        for path in paths:
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                cached = self.cache.get(path)
                if cached is None or cached[0] != signature:
                    partitioned: dict[str, list[Observation]] = {}
                    with path.open(encoding="utf-8", errors="replace") as stream:
                        for line in stream:
                            try:
                                row = json.loads(line)
                                if (
                                    not isinstance(row, dict)
                                    or row.get("type", "usage") != "usage"
                                ):
                                    continue
                                body = (
                                    row.get("usage")
                                    if isinstance(row.get("usage"), dict)
                                    else row
                                )
                                identity = (row.get("user") or {}).get("uuid") or (
                                    (row.get("account") or {}).get("account") or {}
                                ).get("uuid")
                                at = row.get("timestamp")
                                if at is None and row.get("ts"):
                                    parsed = cc.parse_reset_dt(row["ts"])
                                    at = parsed.timestamp() if parsed else None
                                if (
                                    not identity
                                    or not isinstance(at, (int, float))
                                    or not math.isfinite(at)
                                ):
                                    continue
                                partitioned.setdefault(identity, []).extend(
                                    Observation(at, m) for m in meters(body)
                                )
                            except (
                                ValueError,
                                TypeError,
                                AttributeError,
                                OverflowError,
                            ):
                                continue
                    self.cache[path] = signature, partitioned
                result.extend(self.cache[path][1].get(uuid, []))
            except OSError:
                continue
        return result


@dataclass(frozen=True)
class Interval:
    start: float
    end: float
    burn: float
    pool: tuple[int, int]
    uncertain: bool = False
    reason: str = ""


def intervals(observations: list[Observation], key: str, now: float) -> list[Interval]:
    rows = sorted(
        {
            (o.at, o.meter.reset, o.meter.used): o
            for o in observations
            if o.meter.key == key and o.at <= now
        }.values(),
        key=lambda o: (o.at, o.meter.used),
    )
    result = []
    anchor = None
    window = 0
    generation = 0
    lows = []
    for row in rows:
        meter = row.meter
        reset = round(meter.reset / 60) * 60
        if row.at >= meter.reset or reset < window:
            continue
        if reset != window:
            anchor, window, generation, lows = row, reset, 0, []
            continue
        if anchor is None or row.at <= anchor.at:
            continue
        if meter.used < anchor.meter.used:
            lows.append(row)
            if (
                len(lows) >= cc.FORECAST_RESET_CONFIRM
                and anchor.meter.used - min(o.meter.used for o in lows)
                >= cc.FORECAST_RESET_DROP
            ):
                generation += 1
                result.append(
                    Interval(
                        anchor.at,
                        row.at,
                        0,
                        (window, generation),
                        True,
                        "Counter rebase suspected",
                    )
                )
                anchor, lows = row, []
            continue
        reason = (
            "Observation gap"
            if row.at - anchor.at > MAX_GAP
            else "Counter regression"
            if lows
            else ""
        )
        result.append(
            Interval(
                anchor.at,
                row.at,
                meter.used - anchor.meter.used,
                (window, generation),
                bool(reason),
                reason,
            )
        )
        anchor, lows = row, []
    return result


@dataclass
class Snapshot:
    account: str
    uuid: str
    tier: str
    payload: dict
    observed: float
    observations: list[Observation] = field(default_factory=list)
    forecast: dict | None = None
    error: str = ""
    max_age: int = 1800
    access_end: datetime | None = None
    access_note: str = ""

    def stale(self, now: datetime) -> bool:
        return (
            bool(self.error)
            or self.observed <= 0
            or now.timestamp() - self.observed > self.max_age
        )

    @property
    def meters(self) -> list[Meter]:
        return meters(self.payload)


@dataclass(frozen=True)
class Bucket:
    burn: float | None
    coverage: float
    uncertain: bool
    forecast: float | None = None
    reset: bool = False
    partial: bool = False

    @property
    def label(self) -> str:
        if self.forecast is not None:
            return f"~{self.forecast:.1f}"
        if self.burn is None:
            return ""
        suffix = "+" if self.uncertain or self.partial else ""
        return f"{self.burn:.1f}{suffix}"


class CalendarModel:
    def __init__(self, snapshot: Snapshot, now: datetime, key: str = "weekly_all"):
        self.snapshot, self.now, self.key = snapshot, now, key
        self.intervals = intervals(snapshot.observations, key, now.timestamp())
        self.meter = next((m for m in snapshot.meters if m.key == key), None)
        self.resets = {
            round(o.meter.reset / 60) * 60
            for o in snapshot.observations
            if o.meter.key == key
        }
        if self.meter:
            self.resets.add(round(self.meter.reset / 60) * 60)

    def bucket(self, start: datetime, end: datetime) -> Bucket:
        a, b, now = start.timestamp(), end.timestamp(), self.now.timestamp()
        reset = any(a <= at < b for at in self.resets)
        if a >= now:
            forecast = None
            if (
                self.key == "weekly_all"
                and self.meter
                and not self.snapshot.stale(self.now)
                and b <= self.meter.reset
                and (
                    self.snapshot.access_end is None
                    or b <= self.snapshot.access_end.timestamp()
                )
                and self.meter.reset > now
            ):
                entry = self.meter.entry(self.now)
                before = cc.project_week(self.snapshot.forecast, entry, self.now, start)
                after = cc.project_week(self.snapshot.forecast, entry, self.now, end)
                if before and after:
                    forecast = max(0, after[0] - before[0])
            return Bucket(None, 0, False, forecast, reset)
        burn, covered, uncertain, pools = 0.0, 0.0, False, set()
        for item in self.intervals:
            overlap = min(b, item.end) - max(a, item.start)
            if overlap <= 0:
                continue
            pools.add(item.pool)
            if item.uncertain:
                uncertain = True
                continue
            if (item.start < a or item.end > b) and item.burn > 0:
                uncertain = True
                continue
            covered += overlap
            if item.start >= a and item.end <= b:
                burn += item.burn
        coverage = min(1.0, covered / max(1, min(b, now) - a))
        uncertain = uncertain or coverage < 0.9 or len(pools) > 1
        # Crossing pools changes the denominator. Keep the boundary visible.
        return Bucket(
            burn if covered and len(pools) <= 1 else None,
            coverage,
            uncertain,
            reset=reset or len(pools) > 1,
            partial=b > now,
        )


@dataclass(frozen=True)
class Condition:
    id: str
    event: str
    meter: str
    window: str
    message: str
    severity: str
    utilization: float
    reset_at: str
    observed_at: float
    provenance: str = "observed"


def evaluate(
    snapshot: Snapshot, now: datetime, threshold: int = 90
) -> tuple[list[Condition], list[str]]:
    conditions, advice = [], []
    if snapshot.stale(now):
        return [], [
            f"Observation unavailable: {snapshot.error or 'sample is old'}. Last values retained."
        ]
    if snapshot.access_end and snapshot.access_end <= now:
        return [], [
            f"{snapshot.access_note or 'Access boundary reached'}; forecast unavailable"
        ]

    def add(meter, event, message, severity="warning", provenance="observed"):
        reset = datetime.fromtimestamp(meter.reset, timezone.utc).isoformat()
        identity = f"claude:{snapshot.uuid or snapshot.account}:{meter.key}:{round(meter.reset / 60)}:{event}"
        conditions.append(
            Condition(
                identity,
                event,
                meter.key,
                meter.name,
                message,
                severity,
                meter.used,
                reset,
                snapshot.observed,
                provenance,
            )
        )

    live = [m for m in snapshot.meters if m.reset > now.timestamp()]
    for meter in live:
        reset = datetime.fromtimestamp(meter.reset, now.tzinfo).strftime("%a %H:%M")
        if meter.used >= 100:
            add(
                meter,
                "full",
                f"{meter.name} included allowance capped; resets {reset}",
                "critical",
            )
        elif meter.key != "weekly_all":
            entry = meter.entry(now)
            if entry["elapsed_frac"] >= cc.PACE_MIN_ELAPSED_FRAC and meter.used > 0:
                elapsed = meter.duration * entry["elapsed_frac"]
                cap = now + timedelta(seconds=(100 - meter.used) * elapsed / meter.used)
                if cap.timestamp() < meter.reset:
                    add(
                        meter,
                        "pace",
                        f"{meter.name} cap ~{cap:%a %H:%M}; resets {reset}",
                        provenance="window-average pace",
                    )
                    continue
            if meter.used >= threshold:
                add(
                    meter,
                    "threshold",
                    f"{meter.name} {meter.used:.0f}% used; resets {reset}",
                )

    weekly = next((m for m in live if m.key == "weekly_all"), None)
    if weekly and weekly.used < 100:
        entry = weekly.entry(now)
        projection = cc.project_week(snapshot.forecast, entry, now, snapshot.access_end)
        horizon = (
            min(weekly.reset, snapshot.access_end.timestamp())
            if snapshot.access_end
            else weekly.reset
        )
        boundary = (
            "reset"
            if horizon == weekly.reset
            else snapshot.access_note or "access boundary"
        )
        if projection:
            landing, dry = projection
            provenance = "on your pattern"
        elif entry["pace"] and entry["elapsed_frac"] >= cc.PACE_MIN_ELAPSED_FRAC:
            landing, dry, provenance = (
                min(
                    100,
                    weekly.used
                    + (horizon - now.timestamp())
                    * weekly.used
                    / (weekly.duration * entry["elapsed_frac"]),
                ),
                None,
                "at window-average pace",
            )
            if landing >= 100:
                elapsed = weekly.duration * entry["elapsed_frac"]
                dry = now + timedelta(
                    seconds=(100 - weekly.used) * elapsed / weekly.used
                )
        else:
            landing, dry, provenance = None, None, ""
        if landing is not None:
            message = f"7d lands ~{landing:.0f}% used; ~{100 - landing:.0f}% unused at {boundary} {provenance}"
            advice.append(message)
            if dry:
                add(
                    weekly,
                    "pace",
                    f"7d cap ~{dry:%a %H:%M}; resets {datetime.fromtimestamp(weekly.reset, now.tzinfo):%a %H:%M}",
                    provenance=provenance,
                )
            elif 100 - landing >= 20:
                add(weekly, "surplus", message, "info", provenance)
        else:
            advice.append("7d forecast unavailable; observations and reset clocks only")
    if not advice and not weekly:
        advice.append("Awaiting a current weekly observation")
    if weekly:
        entries = []
        for meter in live:
            entry = meter.entry(now)
            entry["name"] = (
                "7d" if meter.key == "weekly_all" else meter.name.removeprefix("7d ")
            )
            entries.append(entry)
        seven = next(e for e in entries if e["name"] == "7d")
        strand = cc.scoped_strand(entries, seven)
        if strand:
            name, reachable, remaining = strand
            scoped = next((m for m in live if m.name == f"7d {name}"), None)
            if scoped:
                message = f"{name}: ~{reachable} of {remaining} points left reachable at this mix"
                advice.append(message)
                add(scoped, "scoped_surplus", message, "info", "current model mix")
    if (snapshot.payload.get("extra_usage") or {}).get("is_enabled"):
        advice.append(
            "Paid extra usage enabled; included allowance caps are not a spend stop"
        )
    return conditions, advice


class AlertJournal:
    """Derived, bounded watch state; no raw provider payloads or credentials."""

    def __init__(self, path: Path | None = None, notifier: str | None = None):
        self.path, self.notifier = path, notifier
        self.state: dict = {"conditions": {}, "events": []}

    @property
    def events(self) -> list[dict]:
        return self.state["events"]

    def update(
        self, snapshots: list[Snapshot], now: datetime, threshold: int = 90
    ) -> list[dict]:
        return self._mutate(lambda: self._update(snapshots, now, threshold))

    def acknowledge(self, transition: str) -> None:
        def mark():
            for event in self.events:
                if event["id"] == transition:
                    event["acknowledged"] = True

        self._mutate(mark)

    def _mutate(self, operation):
        if self.path is None:
            return operation()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(self.path.read_text())
                if isinstance(state.get("conditions"), dict) and isinstance(
                    state.get("events"), list
                ):
                    self.state = state
            except (OSError, ValueError, AttributeError):
                pass
            emitted = operation()
            tmp = self.path.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(self.state, separators=(",", ":")))
            tmp.replace(self.path)
            return emitted

    def _update(self, snapshots, now, threshold):
        emitted = []
        for snapshot in snapshots:
            if snapshot.stale(now):
                continue
            conditions, _ = evaluate(snapshot, now, threshold)
            current = {c.id: c for c in conditions}
            identity = snapshot.uuid or snapshot.account
            states = self.state["conditions"]
            for condition in conditions:
                state = states.setdefault(
                    condition.id,
                    {"account": identity, "active": False, "hits": 0, "seen": 0},
                )
                if snapshot.observed <= state["seen"]:
                    continue
                state.update(
                    seen=snapshot.observed, missing=0, condition=asdict(condition)
                )
                state["hits"] += 1
                required = (
                    2 if condition.event in ("pace", "surplus", "scoped_surplus") else 1
                )
                if not state["active"] and state["hits"] >= required:
                    state["active"] = True
                    emitted.append(self._event(snapshot, condition, "entered", now))
            for key, state in list(states.items()):
                if (
                    state["account"] != identity
                    or key in current
                    or snapshot.observed <= state["seen"]
                ):
                    continue
                prior = Condition(**state["condition"])
                meter = next(
                    (
                        m
                        for m in snapshot.meters
                        if m.key == prior.meter and m.reset > now.timestamp()
                    ),
                    None,
                )
                if meter is None:
                    continue  # missing is not recovery
                if prior.event in ("pace", "threshold") and meter.used >= 100:
                    del states[key]  # escalation to a cap is not recovery
                    continue
                new_window = (
                    meter.reset > cc.parse_reset_dt(prior.reset_at).timestamp() + 60
                )
                # Hysteresis for numerical thresholds; forecast reversals need two observations.
                if (
                    not new_window
                    and prior.event == "threshold"
                    and meter.used >= threshold - 5
                ):
                    continue
                if not new_window and prior.event == "surplus":
                    projection = cc.project_week(
                        snapshot.forecast, meter.entry(now), now, snapshot.access_end
                    )
                    if projection is None:
                        entry = meter.entry(now)
                        if (
                            not entry["pace"]
                            or entry["elapsed_frac"] < cc.PACE_MIN_ELAPSED_FRAC
                        ):
                            continue
                        projection = (min(100, entry["pace"] * 100), None)
                    if 100 - projection[0] >= 15:
                        continue
                state["seen"] = snapshot.observed
                state["missing"] = state.get("missing", 0) + 1
                if state["missing"] < 2 and not new_window:
                    continue
                if state["active"]:
                    recovered = Condition(
                        prior.id,
                        "reset" if new_window else "recovery",
                        prior.meter,
                        prior.window,
                        f"{prior.window} {prior.event} cleared by a fresh observation",
                        "info",
                        meter.used,
                        datetime.fromtimestamp(meter.reset, timezone.utc).isoformat(),
                        snapshot.observed,
                    )
                    emitted.append(self._event(snapshot, recovered, "cleared", now))
                del states[key]
        self.state["events"] = self.state["events"][-200:]
        self.state["conditions"] = {
            k: v
            for k, v in self.state["conditions"].items()
            if now.timestamp() - v["seen"] < 8 * 86400
        }
        return emitted

    def _event(self, snapshot, condition, phase, now):
        transition = hashlib.sha256(
            f"{condition.id}:{phase}:{snapshot.observed}".encode()
        ).hexdigest()[:20]
        data = {
            **asdict(condition),
            "condition_id": condition.id,
            "transition_id": transition,
            "phase": phase,
            "reset_time": condition.reset_at,
            "timestamp": now.isoformat(),
            "provider": "claude",
        }
        event = {
            "id": transition,
            "event": condition.event,
            "account": snapshot.account,
            "data": data,
            "delivery": "demo" if self.path is None else "attempted",
        }
        self.state["events"].append(event)
        if self.path is not None:
            cc.send_notification(condition.event, snapshot.account, data, self.notifier)
        return event


class LiveSource:
    demo = False

    def __init__(
        self,
        credentials,
        *,
        interval=900,
        threshold=90,
        notifier=None,
        log_dir=None,
        no_log=False,
        trace=False,
    ):
        self.credentials, self.interval = (
            credentials,
            max(cc.MIN_WATCH_INTERVAL, interval),
        )
        self.threshold, self.log_dir, self.no_log, self.trace = (
            threshold,
            log_dir,
            no_log,
            trace,
        )
        self.tokens = {str(path): token for path, token in credentials}
        self.terminal, self.profiles, self.snapshots = {}, {}, []
        self.store = ObservationStore()
        self.journal = AlertJournal(cc.data_root() / "calendar-alerts.json", notifier)
        self.profile_at = 0.0

    @property
    def now(self):
        return datetime.now(cc.primary_tz() or timezone.utc)

    def load(self, force=False):
        now = self.now
        if now.timestamp() - self.profile_at > cc.PROFILE_RETRY_SEC:
            self.profiles.update(
                asyncio.run(cc.fetch_all_profiles_async(self.credentials))
            )
            self.profile_at = now.timestamp()
        snapshots = []
        try:
            if force:
                cc.FORCE_FETCH.update(str(p) for p, _ in self.credentials)
            for path, _ in self.credentials:
                label, alias = str(path), cc.get_alias_from_label(str(path))
                profile = cc.profile_with_credential_tier(
                    self.profiles.get(label), path
                )
                data, cached, code = cc.fetch_or_use_cache(
                    label, path, self.terminal, self.tokens, now, self.trace
                )
                data = data or {}
                if data and not cached:
                    cc.attach_prepaid(data, profile, self.tokens.get(label), label)
                if not self.no_log and data:
                    cc.log_usage_jsonl(label, data, cached, self.log_dir, profile)
                if data:
                    cc.update_terminal_cache(label, data, self.terminal)
                uuid = ((profile or {}).get("account") or {}).get("uuid") or ""
                observations = self.store.read(cc.all_store_paths(self.log_dir), uuid)
                observed = float(
                    data.get("fetched_at") or cc.LAST_FETCH_AT.get(label) or 0
                )
                stale = data.get("_stale") or {}
                error = str(
                    stale.get("code") or ("fetch failed" if code != cc.EXIT_OK else "")
                )
                if not data:
                    error = "no usage observation"
                if uuid and observed:
                    observations.extend(Observation(observed, m) for m in meters(data))
                forecast = cc.read_forecast_cache(alias) if uuid else None
                if forecast and (forecast.get("corpus") or {}).get("uuid") not in (
                    None,
                    uuid,
                ):
                    forecast = None
                if forecast is None and uuid:
                    samples, corpus = cc.load_account_corpus(alias, uuid, self.log_dir)
                    forecast = cc.weekday_burn_forecast(samples, uuid, now)
                    if forecast:
                        forecast["corpus"] = corpus.stamp()
                        cc.write_forecast_cache(alias, forecast)
                tier = cc.get_account_tier_label(profile)
                access = cc.get_access_end(profile, now)
                snapshots.append(
                    Snapshot(
                        cc.display_alias(label),
                        uuid,
                        tier,
                        data,
                        observed,
                        observations,
                        forecast,
                        error,
                        max(120, 2 * self.interval),
                        access[0] if access else None,
                        access[1] if access else "",
                    )
                )
            self.snapshots = snapshots
            try:
                self.journal.update(snapshots, self.now, self.threshold)
            except (OSError, ValueError, KeyError, TypeError):
                cc.LOGGER.warning(
                    "calendar alert journal unavailable; observations retained"
                )
            return snapshots
        finally:
            cc.FORCE_FETCH.clear()


class DemoSource:
    demo, interval, threshold = True, 900, 90

    def __init__(self, scenario="mixed"):
        self.scenario = scenario
        self.now = datetime(2026, 9, 8, 14, 20, tzinfo=cc.primary_tz() or timezone.utc)
        self.journal = AlertJournal()

    def load(self, force=False):
        if force:
            self.now += timedelta(minutes=5)
        now = self.now
        weekly_reset = now.replace(hour=9, minute=0, second=0) + timedelta(days=1)
        weekly_start = weekly_reset - timedelta(days=7)
        five_reset = now.replace(hour=17, minute=0, second=0)
        five, week, scope = 88, 38, 57
        if self.scenario == "weekly":
            five, week, scope = 48, 100, 63
        elif self.scenario == "scoped":
            five, week, scope = 52, 62, 100
        elif self.scenario == "reset":
            five, five_reset = 4, now + timedelta(hours=5)
        elif self.scenario == "credits":
            five = 100
        elif self.scenario == "rebase":
            week, scope = 12, 18
        payload = {
            "five_hour": {"utilization": five, "resets_at": five_reset.isoformat()},
            "seven_day": {"utilization": week, "resets_at": weekly_reset.isoformat()},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "percent": scope,
                    "resets_at": weekly_reset.isoformat(),
                    "scope": {"model": {"display_name": "Fable"}},
                }
            ],
            "extra_usage": {
                "is_enabled": self.scenario == "credits",
                "used_credits": 1240,
                "monthly_limit": 10000,
                "currency": "USD",
                "decimal_places": 2,
            },
        }
        if self.scenario == "weekly-only":
            payload.pop("five_hour")
        elapsed = (now.timestamp() - weekly_start.timestamp()) / cc.WINDOW_7D_SEC
        observations = []
        cursor = weekly_start - timedelta(days=14)
        while cursor <= now:
            # A deliberate observation gap, visible in either layout.
            if not (cursor.weekday() == 5 and 2 <= cursor.hour < 12):
                weeks = math.floor(
                    (cursor.timestamp() - weekly_start.timestamp()) / cc.WINDOW_7D_SEC
                )
                reset = weekly_reset + timedelta(weeks=weeks)
                phase = (
                    cursor.timestamp() - (reset - timedelta(days=7)).timestamp()
                ) / cc.WINDOW_7D_SEC
                shaped = max(0, phase + 0.005 * math.sin(phase * math.pi * 14))
                for key, name, target in (
                    ("weekly_all", "7d all", week),
                    ("weekly_scoped:Fable", "7d Fable", scope),
                ):
                    current_shape = elapsed + 0.005 * math.sin(elapsed * math.pi * 14)
                    used = min(
                        100, shaped * (target / current_shape if weeks == 0 else 83)
                    )
                    if (
                        self.scenario == "rebase"
                        and weeks == 0
                        and cursor < now - timedelta(hours=6)
                    ):
                        used += 35
                    observations.append(
                        Observation(
                            cursor.timestamp(),
                            Meter(key, name, used, reset.timestamp(), cc.WINDOW_7D_SEC),
                        )
                    )
                short_start = five_reset.timestamp() - cc.WINDOW_5H_SEC
                period = math.floor(
                    (cursor.timestamp() - short_start) / cc.WINDOW_5H_SEC
                )
                short_reset = five_reset.timestamp() + period * cc.WINDOW_5H_SEC
                short_elapsed = cursor.timestamp() - short_reset + cc.WINDOW_5H_SEC
                short_used = min(100, 100 * short_elapsed / cc.WINDOW_5H_SEC)
                if period == 0:
                    short_used = min(
                        100,
                        five * short_elapsed / max(1, now.timestamp() - short_start),
                    )
                observations.append(
                    Observation(
                        cursor.timestamp(),
                        Meter(
                            "session", "5h", short_used, short_reset, cc.WINDOW_5H_SEC
                        ),
                    )
                )
            cursor += timedelta(minutes=15)
        observations.extend(Observation(now.timestamp(), m) for m in meters(payload))
        forecast = {
            "schema": cc.FORECAST_SCHEMA,
            "computed_at": int(now.timestamp()),
            "days_history": 21,
            "weekday_profile": {str(d): 29.5 for d in range(7)},
            "recent_24h": 8,
            "recent_48h": 15,
            "hour_profile": {str(h): 1.0 for h in range(24)},
        }
        if self.scenario == "cold":
            observations, forecast = [], None
        stale = self.scenario == "stale"
        snapshot = Snapshot(
            "work",
            "demo-work",
            "max 20x",
            payload,
            now.timestamp() - (7200 if stale else 0),
            observations,
            forecast,
            "429" if stale else "",
        )
        secondary = Snapshot(
            "personal",
            "demo-personal",
            "pro",
            {
                "five_hour": {
                    "utilization": 21,
                    "resets_at": (now + timedelta(hours=3)).isoformat(),
                },
                "seven_day": {
                    "utilization": 72,
                    "resets_at": (now + timedelta(days=3)).isoformat(),
                },
            },
            now.timestamp(),
        )
        self.journal.update([snapshot, secondary], now)
        return [snapshot, secondary]
