"""Account-scoped provider collectors. Codex credentials are read, never switched."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import base64
import fcntl
import hashlib
import json
import math
import os
import secrets
import ssl

import certifi
import httpx

import ccpace as cc
from ccpace_calendar import AlertJournal, LiveSource, Meter, Observation, Snapshot

CODEX_BASE = "https://chatgpt.com/backend-api/wham/"


def tls_context() -> ssl.SSLContext:
    # TLS client mode verifies the certificate chain and hostname. Loading the
    # explicitly configured trust store also supports CLI proxy CAs whose root
    # certificates predate Python 3.13's extra strict extension requirements.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(
        cafile=os.getenv("SSL_CERT_FILE") or certifi.where(),
        capath=os.getenv("SSL_CERT_DIR"),
    )
    if extra := os.getenv("NODE_EXTRA_CA_CERTS"):
        context.load_verify_locations(cafile=extra)
    return context


def number(value):
    return (
        float(value)
        if isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
        else None
    )


def timestamp(value):
    if (numeric := number(value)) is not None:
        return numeric if numeric > 0 else None
    parsed = cc.parse_reset_dt(value)
    return parsed.timestamp() if parsed and parsed.tzinfo else None


def codex_meters(payload: dict) -> list[Meter]:
    result = []
    groups = [("", "", payload.get("rate_limit"))]
    if payload.get("code_review_rate_limit"):
        groups.append(("code-review", "Code review", payload["code_review_rate_limit"]))
    for item in payload.get("additional_rate_limits") or []:
        if isinstance(item, dict):
            name = str(item.get("limit_name") or item.get("metered_feature") or "Model")
            groups.append(
                (str(item.get("metered_feature") or name), name, item.get("rate_limit"))
            )
    for group, label, limits in groups:
        if not isinstance(limits, dict):
            continue
        for slot in ("primary_window", "secondary_window"):
            window = limits.get(slot)
            if not isinstance(window, dict):
                continue
            duration, used = (
                number(window.get("limit_window_seconds")),
                number(window.get("used_percent")),
            )
            if duration is None or duration <= 0 or used is None or used < 0:
                continue
            role = (
                "session"
                if duration == 18000
                else "weekly_all"
                if duration == 604800
                else slot
            )
            key = role if not group else f"codex:{group}:{role}"
            if any(m.key == key for m in result):
                key += f":{slot}"
            span = (
                "5h"
                if duration == 18000
                else "7d"
                if duration == 604800
                else f"{duration / 3600:g}h"
            )
            name = (
                f"{label} {span}"
                if label
                else "7d all"
                if role == "weekly_all"
                else span
            )
            reset = timestamp(window.get("reset_at"))
            result.append(
                Meter(
                    key,
                    name,
                    used,
                    reset,
                    int(duration),
                    "active"
                    if reset is not None
                    else "inactive"
                    if used == 0
                    else "unavailable",
                )
            )
    return result


def atomic_json(path: Path, value):
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with os.fdopen(
            os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as stream:
            json.dump(value, stream, separators=(",", ":"))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def codex_files(explicit=None):
    if explicit:
        return list(dict.fromkeys(Path(p).expanduser().resolve() for p in explicit))
    root = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return sorted(
        p
        for p in root.glob("auth*.json")
        if p.name == "auth.json" or p.name.startswith("auth.")
    )


def user_identity(tokens):
    for token in (tokens.get("access_token"), tokens.get("id_token")):
        if not isinstance(token, str) or token.count(".") != 2:
            continue
        try:
            part = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            auth = claims.get("https://api.openai.com/auth") or {}
            identity = auth.get("chatgpt_user_id") or claims.get("sub")
            if isinstance(identity, str) and identity:
                return identity
        except (ValueError, TypeError, AttributeError):
            continue
    return None


class CodexSource:
    def __init__(self, path: Path, *, interval=900, no_log=False, client=None):
        self.path = path
        self.account = (
            path.name.removesuffix(".json").removeprefix("auth.")
            if path.name != "auth.json"
            else "default"
        )
        self.source_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        self.interval, self.no_log, self.client = max(60, interval), no_log, client
        self.previous = None
        self.retry_at = 0.0

    def _history(self, root, owner):
        result = []
        for path in (root / "usage.jsonl.1", root / "usage.jsonl"):
            try:
                with path.open() as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            if (
                                row.get("account") == owner
                                and row.get("provider") == "codex"
                                and number(row.get("timestamp")) is not None
                            ):
                                result.extend(
                                    Observation(row["timestamp"], m)
                                    for m in codex_meters(row["usage"])
                                )
                        except (ValueError, KeyError, TypeError, AttributeError):
                            continue
            except OSError:
                continue
        return result

    def _snapshot(self, cache, root, owner, now):
        data = cache["usage"]
        readings = self._history(root, owner)
        limits = codex_meters(data)
        readings.extend(Observation(cache["timestamp"], m) for m in limits)
        samples = [
            cc.Sample(o.at, 0, o.meter.used, owner, round(o.meter.reset / 60) * 60)
            for o in readings
            if o.meter.key == "weekly_all" and o.meter.timed
        ]
        forecast = cc.weekday_burn_forecast(samples, owner, now) if samples else None
        credits = data.get("credits") or {}
        return Snapshot(
            self.account,
            owner,
            str(data.get("plan_type") or ""),
            data,
            cache["timestamp"],
            readings,
            forecast,
            max_age=max(120, self.interval * 2),
            provider="codex",
            source_id=self.source_id,
            limits=limits,
            reset_inventory=data.get("rate_limit_reset_credits"),
            credit_balance=str(credits["balance"])
            if isinstance(credits, dict) and credits.get("balance") is not None
            else None,
        )

    def load(self, force=False):
        now = datetime.now(cc.primary_tz() or timezone.utc)
        owner = ""
        try:
            auth = json.loads(self.path.read_text())
            tokens = auth.get("tokens") or {}
            token, account_id = tokens.get("access_token"), tokens.get("account_id")
            if (
                not isinstance(token, str)
                or not isinstance(account_id, str)
                or not token
                or not account_id
            ):
                raise ValueError("OAuth token and account ID required")
            # Team members can share a workspace while owning separate allowances.
            # JWT claims provide routing identity, not a substitute for server auth.
            owner = hashlib.sha256(
                json.dumps(
                    [account_id, user_identity(tokens) or self.source_id]
                ).encode()
            ).hexdigest()
            root = cc.data_root() / "providers" / "codex" / "accounts" / owner[:24]
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (root / "usage.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    cache = json.loads((root / "usage.cache").read_text())
                    if (
                        cache.get("account") != owner
                        or not isinstance(cache.get("usage"), dict)
                        or number(cache.get("timestamp")) is None
                    ):
                        cache = None
                except (OSError, ValueError, AttributeError):
                    cache = None
                fresh = (
                    cache
                    and 0
                    <= now.timestamp() - cache.get("timestamp", 0)
                    < cc.SHARED_USAGE_FRESH_SEC
                )
                if cache:
                    self.previous = self._snapshot(cache, root, owner, now)
                if now.timestamp() < self.retry_at and not fresh:
                    raise ValueError("Rate limited; retry after cooldown")
                if not fresh:
                    owned_client = self.client is None
                    client = self.client or httpx.Client(
                        verify=tls_context(), timeout=20, follow_redirects=False
                    )
                    try:
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "ChatGPT-Account-Id": account_id,
                            "Accept": "application/json",
                            "User-Agent": f"ccpace/{cc.__version__}",
                        }
                        response = client.get(CODEX_BASE + "usage", headers=headers)
                        if response.status_code in (401, 403):
                            raise ValueError(
                                "Sign in again through the source of this Codex auth file"
                            )
                        if response.status_code == 429:
                            retry = response.headers.get("Retry-After", "60")
                            self.retry_at = now.timestamp() + (
                                int(retry) if retry.isdigit() else 60
                            )
                            raise ValueError("Rate limited; retry after cooldown")
                        response.raise_for_status()
                        payload = response.json()
                        if (
                            not isinstance(payload, dict)
                            or payload.get("account_id", account_id) != account_id
                        ):
                            raise ValueError(
                                "Usage account does not match the selected auth file"
                            )
                        # Persist quota fields only; no tokens, email, or provider identity.
                        data = {
                            key: payload[key]
                            for key in (
                                "plan_type",
                                "rate_limit",
                                "code_review_rate_limit",
                                "additional_rate_limits",
                                "credits",
                                "spend_control",
                                "rate_limit_reached_type",
                                "rate_limit_reset_credits",
                            )
                            if key in payload
                        }
                        inventory = data.get("rate_limit_reset_credits")
                        if not isinstance(inventory, dict):
                            try:
                                extra = client.get(
                                    CODEX_BASE + "rate-limit-reset-credits",
                                    headers=headers,
                                )
                                inventory = (
                                    extra.json() if extra.status_code == 200 else None
                                )
                            except (httpx.HTTPError, ValueError):
                                inventory = None
                        if isinstance(inventory, dict):
                            data["rate_limit_reset_credits"] = {
                                "available_count": inventory.get("available_count"),
                                "credits": [
                                    {
                                        "expires_at": item.get("expires_at"),
                                        "status": item.get("status"),
                                    }
                                    for item in inventory.get("credits") or []
                                    if isinstance(item, dict)
                                ],
                            }
                        cache = {
                            "schema": 1,
                            "provider": "codex",
                            "account": owner,
                            "timestamp": datetime.now(timezone.utc).timestamp(),
                            "usage": data,
                        }
                        atomic_json(root / "usage.cache", cache)
                        if not self.no_log:
                            log = root / "usage.jsonl"
                            if (
                                log.exists()
                                and log.stat().st_size >= cc.USAGE_LOG_MAX_BYTES
                            ):
                                log.replace(root / "usage.jsonl.1")
                            with os.fdopen(
                                os.open(
                                    log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                                ),
                                "a",
                            ) as stream:
                                stream.write(
                                    json.dumps(cache, separators=(",", ":")) + "\n"
                                )
                    finally:
                        if owned_client:
                            client.close()
                self.previous = self._snapshot(cache, root, owner, now)
                return [self.previous]
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            httpx.HTTPError,
        ) as error:
            # Keep exception text bounded and never include request/credential objects.
            message = (
                str(error)
                if isinstance(error, ValueError)
                else f"Usage unavailable ({type(error).__name__})"
            )
            if self.previous and self.previous.uuid == owner:
                return [replace(self.previous, error=message)]
            return [
                Snapshot(
                    self.account,
                    owner,
                    "",
                    {},
                    0,
                    error=message,
                    provider="codex",
                    source_id=self.source_id,
                    limits=[],
                )
            ]


class FleetSource:
    demo = False

    def __init__(
        self, sources, *, interval=900, threshold=90, notifier=None, notify=True
    ):
        self.sources, self.interval, self.threshold = (
            sources,
            max(60, interval),
            threshold,
        )
        self.journal = AlertJournal(cc.data_root() / "calendar-alerts.json", notifier)
        self.snapshots = []
        self.notify = notify

    @property
    def now(self):
        return datetime.now(cc.primary_tz() or timezone.utc)

    def load(self, force=False):
        grouped = {}
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(self.sources)))) as pool:
            jobs = {
                pool.submit(source.load, force): index
                for index, source in enumerate(self.sources)
            }
            for future in as_completed(jobs):
                index = jobs[future]
                try:
                    grouped[index] = future.result()
                except Exception:
                    source = self.sources[index]
                    grouped[index] = [
                        replace(s, error="Provider refresh failed")
                        for s in getattr(source, "snapshots", [])
                    ]
        # Prefer fresh evidence when two credential sources identify the same pool.
        unique = {}
        for index in sorted(grouped):
            for snapshot in grouped[index]:
                previous = unique.get(snapshot.identity)
                if previous is None or (
                    previous.stale(self.now) and not snapshot.stale(self.now)
                ):
                    unique[snapshot.identity] = snapshot
        self.snapshots = list(unique.values())
        labels = Counter((s.provider, s.account) for s in self.snapshots)
        self.snapshots = [
            replace(s, account=f"{s.account} [{s.uuid[:6] or s.source_id[:6]}]")
            if labels[(s.provider, s.account)] > 1
            else s
            for s in self.snapshots
        ]
        try:
            if self.notify:
                self.journal.update(self.snapshots, self.now, self.threshold)
        except (OSError, ValueError, KeyError, TypeError):
            cc.LOGGER.warning("Calendar alerts unavailable; observations retained")
        return self.snapshots


def build_source(args, notifier=None, *, notify=True):
    sources = []
    interval = cc.env_int("CCPACE_INTERVAL", args.interval)
    if args.provider != "codex":
        explicit = [p for group in args.files or [] for p in group]
        files = explicit or cc.discover_credential_files()
        if files or os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or cc.get_os() == "darwin":
            try:
                credentials = cc.get_all_credentials(files, bool(explicit))
            except SystemExit:
                credentials = []
            if credentials:
                sources.append(
                    LiveSource(
                        credentials,
                        interval=interval,
                        no_log=args.no_log,
                        log_dir=Path(args.log_dir).expanduser()
                        if args.log_dir
                        else None,
                        notify=False,
                    )
                )
    if args.provider != "claude":
        paths = [p for group in args.codex_files or [] for p in group]
        sources.extend(
            CodexSource(path, interval=interval, no_log=args.no_log)
            for path in codex_files(paths)
        )
    return FleetSource(
        sources,
        interval=interval,
        threshold=cc.env_int("CCPACE_THRESHOLD", args.threshold),
        notifier=notifier,
        notify=notify,
    )


def snapshot_json(snapshot, now):
    return {
        "provider": snapshot.provider,
        "account": snapshot.account,
        "identity": snapshot.identity,
        "plan": snapshot.tier,
        "observed_at": snapshot.observed or None,
        "stale": snapshot.stale(now),
        "error": snapshot.error or None,
        "limits": [asdict(m) for m in snapshot.meters],
        "reset_inventory": snapshot.reset_inventory,
        "credit_balance": snapshot.credit_balance,
    }
