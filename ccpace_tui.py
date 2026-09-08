"""Usage calendar with hourly patterns and evidence on selection."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Button, DataTable, Footer, Select, Static, Tab, Tabs

import ccpace as cc
from ccpace_calendar import CalendarModel, DemoSource, SCENARIOS, evaluate

THEMES = {
    "spectrum": "ccpace-spectrum",
    "quiet": "ccpace-dark",
    "paper": "ccpace-light",
}


class CalendarApp(App):
    TITLE = "ccpace"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; color: $foreground; }
    #brand { height: 1; margin: 1 2 0 2; }
    #controls { height: 1; margin: 1 1; }
    Select { border: none; margin: 0 1; }
    SelectCurrent { padding: 0 1; }
    #account { width: 1fr; }
    #metric { width: 22; }
    #palette { width: 17; }
    #refresh { width: 9; min-width: 9; height: 1; border: none; margin: 0 1; }
    #limits { height: auto; margin: 0 2; }
    #advice { height: auto; min-height: 2; margin: 1 2 0 2; }
    Tabs { margin: 0 1; }
    #workspace { height: 1fr; margin: 0 2; }
    #primary { width: 1fr; }
    #period { height: 2; align-vertical: middle; }
    #period Button { height: 1; min-width: 3; width: 3; border: none; margin: 0 1; }
    #period #today { width: 7; }
    #range { width: 1fr; height: 1; color: $text-muted; }
    #table { height: 1fr; background: $background; }
    DataTable > .datatable--header { background: $surface; color: $text-muted; text-style: none; }
    DataTable > .datatable--odd-row { background: $background; }
    DataTable > .datatable--even-row { background: $background; }
    DataTable > .datatable--cursor { background: $primary; color: $background; text-style: bold; }
    DataTable > .datatable--header-cursor { background: $surface; color: $foreground; }
    DataTable > .datatable--fixed { background: $background; color: $text-muted; }
    #legend { height: 1; color: $text-muted; }
    #inspector { width: 35; margin: 0 0 0 2; padding: 1 0 0 2; border-left: solid $surface-lighten-2; }
    #detail { height: auto; }
    #bottom-detail { height: 2; margin: 0 2; color: $text-muted; }
    Footer { background: $surface; }
    .compact #brand { margin: 0 1; }
    .compact #controls { margin: 0 0 1 0; }
    .compact #limits, .compact #advice { margin: 0 1; }
    .compact #workspace { margin: 0 1; }
    .compact #period { height: 1; }
    .narrow #metric { width: 17; }
    .narrow #palette { width: 13; }
    .narrow #controls { margin: 0; }
    .narrow Select { margin: 0; }
    .narrow #refresh { display: none; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("enter", "inspect", "Inspect", show=False),
        Binding("x", "acknowledge", "Acknowledge"),
        Binding("escape", "back", "Back", show=False),
        Binding("t", "today", "Today", show=False),
        Binding("left_square_bracket", "previous_week", "Previous week", show=False),
        Binding("right_square_bracket", "next_week", "Next week", show=False),
        Binding("1", "view('calendar')", "Calendar", show=False),
        Binding("2", "view('history')", "History", show=False),
        Binding("3", "view('alerts')", "Alerts", show=False),
        Binding("a", "account", "Account", show=False),
        Binding("m", "metric", "Meter", show=False),
        Binding("d", "scenario", "Scenario", show=False),
        Binding("ctrl+t", "theme_mode", "Theme", show=False),
    ]

    def __init__(self, source, *, theme=None):
        super().__init__()
        self.source = source
        self.snapshots = []
        self.account_index = 0
        self.metric = "weekly_all"
        self.selected_date = source.now.date()
        self.week = self.selected_date - timedelta(days=self.selected_date.weekday())
        self.band = min(5, source.now.hour // 4)
        self.view = "calendar"
        self.loading = False
        self.load_error = ""
        self.next_poll = 0.0
        self.rebuilding = False
        self.model = None
        self.row_metadata = []
        self.last_size = (0, 0)
        self.register_theme(
            Theme(
                name="ccpace-spectrum",
                primary="#62d9e8",
                secondary="#a0dca4",
                accent="#ed9abd",
                foreground="#e6e7ec",
                background="#17181d",
                surface="#272930",
                panel="#272930",
                warning="#f0c578",
                error="#f18a8c",
                success="#a0dca4",
                dark=True,
            )
        )
        self.register_theme(
            Theme(
                name="ccpace-dark",
                primary="#68c7bd",
                secondary="#a3b6a8",
                accent="#68c7bd",
                foreground="#e3e8e5",
                background="#161a19",
                surface="#252b29",
                panel="#252b29",
                warning="#e5bd73",
                error="#e47c78",
                success="#90bf95",
                dark=True,
            )
        )
        self.register_theme(
            Theme(
                name="ccpace-light",
                primary="#226e66",
                secondary="#526c5d",
                accent="#226e66",
                foreground="#25312b",
                background="#fafcfb",
                surface="#e9efeb",
                panel="#e9efeb",
                warning="#8b5a14",
                error="#b23c37",
                success="#346844",
                dark=False,
            )
        )
        self.theme = (
            THEMES[theme]
            if theme
            else (
                "ccpace-light"
                if os.getenv("COLORFGBG", "").split(";")[-1] in ("7", "15")
                else "ccpace-spectrum"
            )
        )
        self.freshness_state = None

    def compose(self) -> ComposeResult:
        yield Static("ccpace", id="brand", markup=False)
        with Horizontal(id="controls"):
            yield Select(
                [("Loading accounts", "loading")],
                value="loading",
                allow_blank=False,
                id="account",
                compact=True,
            )
            yield Select(
                [("7d all", "weekly_all")],
                value="weekly_all",
                allow_blank=False,
                id="metric",
                compact=True,
            )
            yield Select(
                [(key.title(), value) for key, value in THEMES.items()],
                value=self.theme,
                allow_blank=False,
                id="palette",
                compact=True,
            )
            yield Button("Refresh", id="refresh", tooltip="Refresh observations")
        yield Static("Loading observations...", id="limits", markup=False)
        yield Static("", id="advice", markup=False)
        yield Tabs(
            Tab("Calendar", id="calendar"),
            Tab("History", id="history"),
            Tab("Alerts", id="alerts"),
            id="views",
        )
        with Horizontal(id="workspace"):
            with Vertical(id="primary"):
                with Horizontal(id="period"):
                    yield Static("", id="range", markup=False)
                    yield Button("\u2039", id="previous", tooltip="Previous week")
                    yield Button("Today", id="today")
                    yield Button("\u203a", id="next", tooltip="Next week")
                yield DataTable(
                    id="table", cursor_type="cell", cell_padding=1, show_row_labels=True
                )
                yield Static("", id="legend", markup=False)
            with VerticalScroll(id="inspector"):
                yield Static("", id="detail", markup=False)
        yield Static("", id="bottom-detail", markup=False)
        yield Footer()

    def on_mount(self):
        self.responsive()
        self.set_interval(1, self.tick)
        self.fetch()

    def on_resize(self):
        if self.is_mounted:
            self.call_after_refresh(self.responsive)

    def responsive(self):
        size = (self.size.width, self.size.height)
        if size == self.last_size:
            return
        self.last_size = size
        self.screen.set_class(self.size.height <= 28, "compact")
        self.screen.set_class(self.size.width < 70, "narrow")
        self.query_one("#inspector").display = self.size.width >= 115
        self.query_one("#bottom-detail").display = self.size.width < 115
        if self.snapshots:
            self.draw_table()
            self.draw_limits()

    def tick(self):
        now = self.source.now
        mode = f"DEMO / {self.source.scenario}" if self.source.demo else "LIVE"
        status = "refreshing" if self.loading else self.load_error or mode
        title = Text("ccpace", style="bold")
        title.append(f"  {status}", style="dim")
        clock = now.strftime(
            "%H:%M %Z" if self.size.width < 70 else "%a %d %b %H:%M %Z"
        )
        title.append(f"    {clock}", style="dim")
        self.query_one("#brand", Static).update(title)
        if self.snapshots:
            state = (
                self.snapshot.stale(now),
                tuple(m.reset <= now.timestamp() for m in self.snapshot.meters),
            )
            if state != self.freshness_state:
                self.freshness_state = state
                self.redraw()
        if (
            not self.source.demo
            and not self.loading
            and now.timestamp() >= self.next_poll
        ):
            self.fetch()

    @work(exclusive=True)
    async def fetch(self, force=False):
        if self.loading:
            return
        self.loading = True
        self.tick()
        try:
            snapshots = await asyncio.to_thread(self.source.load, force)
            first_load = not self.snapshots
            previous = self.snapshot.account if self.snapshots else None
            self.snapshots = snapshots
            self.account_index = next(
                (i for i, s in enumerate(snapshots) if s.account == previous), 0
            )
            self.load_error = ""
            self.rebuilding = True
            account = self.query_one("#account", Select)
            account.set_options(
                [(f"{s.account} / {s.tier}", str(i)) for i, s in enumerate(snapshots)]
            )
            account.value = str(self.account_index)
            self.sync_metrics()
            self.rebuilding = False
            self.redraw()
            if first_load:
                self.query_one("#table", DataTable).focus()
        except Exception as error:
            self.load_error = f"refresh failed ({type(error).__name__})"
            self.query_one("#advice", Static).update(
                f"{self.load_error}; previous observations retained"
            )
        finally:
            self.rebuilding = False
            self.loading = False
            now = self.source.now
            delay = cc.jittered(self.source.interval)
            resets = [
                m.reset - now.timestamp() + cc.RESET_POLL_GRACE
                for s in self.snapshots
                for m in s.meters
                if m.reset > now.timestamp()
            ]
            self.next_poll = now.timestamp() + min([delay, *resets])
            self.tick()

    @property
    def snapshot(self):
        return self.snapshots[self.account_index]

    def sync_metrics(self):
        options = [(m.name, m.key) for m in self.snapshot.meters if m.key != "session"]
        options = options or [("7d all", "weekly_all")]
        if self.metric not in {key for _, key in options}:
            self.metric = options[0][1]
        selector = self.query_one("#metric", Select)
        selector.set_options(options)
        selector.value = self.metric

    def redraw(self):
        if self.snapshots:
            self.model = CalendarModel(self.snapshot, self.source.now, self.metric)
            self.draw_limits()
            self.draw_table()

    def draw_limits(self):
        snapshot, now = self.snapshot, self.source.now
        narrow = self.size.width < 70
        table = Table.grid(padding=(0, 2 if narrow else 3), expand=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True, justify="right")
        if not narrow:
            table.add_column(ratio=1)
        table.add_column(no_wrap=True)
        headings = [Text("LIMIT", style="dim"), Text("USED", style="dim")]
        if not narrow:
            headings.append(Text(""))
        headings.append(Text("RESET", style="dim"))
        table.add_row(*headings)
        for meter in snapshot.meters:
            expired = meter.reset <= now.timestamp()
            style = (
                "dim"
                if snapshot.stale(now) or expired
                else f"bold {self.current_theme.error}"
                if meter.used >= 100
                else self.current_theme.warning
                if meter.used >= 80
                else ""
            )
            used = "cap" if meter.used >= 100 else f"{meter.used:.0f}%"
            identity = (
                self.current_theme.accent
                if meter.key.startswith("weekly_scoped")
                else self.current_theme.primary
            )
            row = [Text(meter.name, style=identity), Text(used, style=style)]
            if not narrow:
                fill = min(24, round(meter.used / 100 * 24))
                bar = Text("\u2501" * fill, style=style or identity)
                bar.append("\u2500" * (24 - fill), style="dim")
                row.append(bar)
            reset = datetime.fromtimestamp(meter.reset, now.tzinfo).strftime(
                "%a %d %H:%M"
            )
            row.append(
                Text(reset + (" *" if expired else ""), style="dim" if expired else "")
            )
            table.add_row(*row)
        if not snapshot.meters:
            table.add_row(Text("No usage observation", style="yellow"))
        self.query_one("#limits", Static).update(table)
        conditions, advice = evaluate(snapshot, now, self.source.threshold)
        text = Text()
        pressure = [c for c in conditions if c.severity != "info"]
        if pressure:
            condition = sorted(
                pressure, key=lambda c: (c.severity != "critical", c.meter != "session")
            )[0]
            text.append(
                "! " + condition.message,
                style=f"bold {self.current_theme.error}"
                if condition.severity == "critical"
                else self.current_theme.warning,
            )
            if condition.provenance != "observed" and self.size.width >= 105:
                text.append(" / " + condition.provenance, style="dim")
        else:
            text.append("! " if snapshot.stale(now) else "  ")
            text.append(
                advice.pop(0)
                if snapshot.stale(now) and advice
                else "No current pressure warning",
                style="dim",
            )
        text.append("\n")
        weekly_capped = any(
            c.event == "full" and c.meter == "weekly_all" for c in conditions
        )
        text.append(
            "  "
            + (
                advice[0]
                if advice
                else "Weekly included allowance exhausted"
                if weekly_capped
                else "Forecast unavailable"
            ),
            style=self.current_theme.success
            if any(c.event == "surplus" for c in conditions)
            else "dim",
        )
        extra = snapshot.payload.get("extra_usage") or {}
        if extra.get("is_enabled"):
            amount = extra.get("used_credits")
            limit = extra.get("monthly_limit")
            if isinstance(amount, (int, float)) and isinstance(limit, (int, float)):
                divisor = 10 ** int(extra.get("decimal_places", 2))
                text.append(
                    f"\n  Paid usage: {amount / divisor:.2f} / {limit / divisor:.2f} {extra.get('currency', 'USD')}",
                    style="dim",
                )
            else:
                text.append("\n  Paid extra usage enabled", style="dim")
        self.query_one("#advice", Static).update(text)

    def midnight(self, day):
        return datetime.combine(day, time(), self.source.now.tzinfo)

    def cell(self, bucket):
        if bucket.forecast is not None:
            style = f"dim {self.current_theme.primary}"
        elif bucket.burn is None:
            style = "dim"
        else:
            style = (
                self.current_theme.success
                if bucket.burn >= 2
                else self.current_theme.secondary
            )
        return Text(bucket.label + (" |" if bucket.reset else ""), style=style)

    def hour_strip(self, day, start_hour=0, hours=24):
        strip = Text()
        for hour in range(start_hour, start_hour + hours):
            cursor = self.midnight(day) + timedelta(hours=hour)
            bucket = self.model.bucket(cursor, cursor + timedelta(hours=1))
            if bucket.forecast is not None:
                strip.append("\u2591", style=f"dim {self.current_theme.primary}")
            elif bucket.burn is None or bucket.uncertain:
                strip.append(" ")
            else:
                strip.append(
                    cc.burn_glyph(bucket.burn * 4), style=self.current_theme.secondary
                )
        return strip

    def unavailable_label(self, start, bucket):
        if bucket.reset:
            return "Quota boundary"
        if start.timestamp() >= self.source.now.timestamp():
            if self.model.meter and start.timestamp() >= self.model.meter.reset:
                return "Next quota period"
            return "No forecast"
        return "No observations"

    def draw_table(self):
        if not self.snapshots:
            return
        self.model = CalendarModel(self.snapshot, self.source.now, self.metric)
        table = self.query_one("#table", DataTable)
        self.rebuilding = True
        old_row = table.cursor_row
        table.clear(columns=True)
        table.cursor_type = "cell"
        table.show_row_labels = True
        self.row_metadata = []
        name = self.model.meter.name if self.model.meter else self.metric
        start, end = self.week, self.week + timedelta(days=6)
        label = f"{start:%d %b} - {end:%d %b %Y} / {name}"
        legend = "points used / + partial / ~ forecast / | reset"
        if self.view == "history":
            self.history_table(table)
            label, legend = (
                f"Quota periods / {name}",
                "Used is the last observation, not a claim about usage at reset",
            )
        elif self.view == "alerts":
            self.alerts_table(table)
            label, legend = (
                "Condition history",
                "Delivery attempted does not confirm receipt by an agent",
            )
        elif self.view == "day":
            self.day_table(table)
            label = f"{self.selected_date:%A %d %b} / hourly detail"
        elif self.size.width < 65:
            self.agenda_table(table)
        else:
            width = max(5, (table.size.width - 9) // 7 - 2)
            for day in range(7):
                date = self.week + timedelta(days=day)
                table.add_column(
                    Text(
                        date.strftime("%a %d"),
                        style=f"bold {self.current_theme.primary}"
                        if date == self.source.now.date()
                        else "dim"
                        if date > self.source.now.date()
                        else "",
                    ),
                    width=width,
                )
            for band in range(6):
                cells = []
                for day in range(7):
                    cursor = self.midnight(self.week + timedelta(days=day)) + timedelta(
                        hours=band * 4
                    )
                    cell = self.cell(
                        self.model.bucket(cursor, cursor + timedelta(hours=4))
                    )
                    if self.size.height >= 32:
                        cell.append("\n")
                        cell.append_text(self.hour_strip(cursor.date(), band * 4, 4))
                    cells.append(cell)
                table.add_row(
                    *cells,
                    label=f"{band * 4:02}-{band * 4 + 4:02}",
                    height=2 if self.size.height >= 32 else 1,
                )
            table.move_cursor(
                row=self.band,
                column=max(0, min(6, (self.selected_date - self.week).days)),
                animate=False,
            )
        if self.view in ("history", "alerts") and table.row_count:
            table.move_cursor(row=min(old_row, table.row_count - 1), animate=False)
        self.query_one("#range", Static).update(label)
        self.query_one("#legend", Static).update(
            legend if self.size.width >= 70 else "points used / + partial / ~ forecast"
        )
        if not table.row_count:
            self.query_one("#legend", Static).update(
                "No alert transitions"
                if self.view == "alerts"
                else "No recorded history for this account"
            )
        self.rebuilding = False
        self.refresh_bindings()
        self.draw_detail()
        if self.focused is None:
            table.focus()

    def agenda_table(self, table):
        table.cursor_type = "row"
        table.add_columns("Day", "Points", "Coverage")
        for day in range(7):
            date = self.week + timedelta(days=day)
            bucket = self.model.bucket(
                self.midnight(date), self.midnight(date + timedelta(days=1))
            )
            table.add_row(
                date.strftime("%a %d"),
                self.cell(bucket),
                f"{bucket.coverage:.0%}" if bucket.burn is not None else "",
            )
        table.move_cursor(
            row=max(0, min(6, (self.selected_date - self.week).days)), animate=False
        )

    def day_table(self, table):
        available = self.snapshot.meters
        for meter in available:
            table.add_column(meter.name)
        models = [
            CalendarModel(self.snapshot, self.source.now, m.key) for m in available
        ]
        start = self.midnight(self.selected_date).astimezone(timezone.utc)
        end = self.midnight(self.selected_date + timedelta(days=1)).astimezone(
            timezone.utc
        )
        cursor = start
        while cursor < end:
            local = cursor.astimezone(self.source.now.tzinfo)
            self.row_metadata.append(cursor)
            table.add_row(
                *(
                    self.cell(
                        model.bucket(cursor, min(end, cursor + timedelta(hours=1)))
                    )
                    for model in models
                ),
                label=local.strftime("%H:%M %z"),
            )
            cursor += timedelta(hours=1)
        table.move_cursor(row=min(table.row_count - 1, self.band * 4), animate=False)

    def history_table(self, table):
        table.cursor_type = "row"
        table.show_row_labels = False
        table.add_columns("Reset", "Last seen", "Used", "Samples", "State")
        periods = {}
        for observation in self.snapshot.observations:
            if (
                observation.meter.key == self.metric
                and observation.at <= self.source.now.timestamp()
            ):
                periods.setdefault(round(observation.meter.reset / 60), []).append(
                    observation
                )
        for _, rows in sorted(periods.items(), reverse=True):
            rows = sorted(
                {(o.at, o.meter.used): o for o in rows}.values(), key=lambda o: o.at
            )
            last = rows[-1]
            reset = datetime.fromtimestamp(last.meter.reset, self.source.now.tzinfo)
            self.row_metadata.append(
                datetime.fromtimestamp(last.at, self.source.now.tzinfo).date()
            )
            table.add_row(
                reset.strftime("%d %b %H:%M"),
                datetime.fromtimestamp(last.at, self.source.now.tzinfo).strftime(
                    "%d %b %H:%M"
                ),
                f"{last.meter.used:.1f}%",
                str(len(rows)),
                "rebase"
                if any(
                    i.pool[0] == round(last.meter.reset / 60) * 60 and i.pool[1] > 0
                    for i in self.model.intervals
                )
                else "observed",
            )

    def alerts_table(self, table):
        table.cursor_type = "row"
        table.show_row_labels = False
        table.add_columns("Time", "Limit", "Condition", "Delivery")
        for event in reversed(self.source.journal.events):
            if event["account"] != self.snapshot.account:
                continue
            data = event["data"]
            self.row_metadata.append(event)
            table.add_row(
                datetime.fromtimestamp(
                    data["observed_at"], self.source.now.tzinfo
                ).strftime("%d %b %H:%M"),
                data["window"],
                f"{event['event']} / {data['phase']}",
                "acknowledged" if event.get("acknowledged") else event["delivery"],
            )

    def draw_detail(self):
        if not self.snapshots:
            return
        table = self.query_one("#table", DataTable)
        now, snapshot = self.source.now, self.snapshot
        detail = Text()
        if self.view == "alerts" and self.row_metadata:
            event = self.row_metadata[min(table.cursor_row, len(self.row_metadata) - 1)]
            detail.append(event["data"]["message"] + "\n\n", style="bold")
            detail.append(event["data"]["provenance"] + "\n", style="dim")
            detail.append("condition\n" + event["data"]["condition_id"], style="dim")
        else:
            start = self.midnight(self.selected_date) + timedelta(hours=self.band * 4)
            end = start + timedelta(hours=4)
            if self.view == "day" and self.row_metadata:
                start = self.row_metadata[
                    min(table.cursor_row, len(self.row_metadata) - 1)
                ]
                end = start + timedelta(hours=1)
            bucket = self.model.bucket(start, end)
            detail.append(f"{self.selected_date:%A %d %b}\n", style="bold")
            detail.append(
                f"{start.astimezone(now.tzinfo):%H:%M} - {end.astimezone(now.tzinfo):%H:%M %Z}\n\n",
                style="dim",
            )
            name = self.model.meter.name if self.model.meter else self.metric
            if bucket.forecast is not None:
                detail.append(
                    f"~{bucket.forecast:.1f} {name} points\n",
                    style=self.current_theme.primary,
                )
                detail.append("Forecast on your pattern\n", style="dim")
            elif bucket.burn is not None:
                detail.append(
                    f"{bucket.burn:.1f} {name} points observed\n", style="bold"
                )
            else:
                detail.append(self.unavailable_label(start, bucket) + "\n", style="dim")
            if bucket.burn is not None and start.timestamp() < now.timestamp():
                detail.append(f"{bucket.coverage:.0%} interval coverage\n", style="dim")
            if bucket.uncertain and bucket.burn is not None:
                detail.append(
                    "Partial coverage; observed amount only\n",
                    style="dim",
                )
            elif bucket.partial:
                detail.append("Interval still in progress\n", style="dim")
            if bucket.reset:
                detail.append(
                    "Quota boundary in this interval\n",
                    style=self.current_theme.primary,
                )
            overlapping = [
                i
                for i in self.model.intervals
                if i.start < end.timestamp()
                and i.end > start.timestamp()
                and (
                    i.uncertain
                    or i.start < start.timestamp()
                    or i.end > end.timestamp()
                )
            ]
            if overlapping:
                item = overlapping[0]
                a, b, burn = item.start, item.end, item.burn
                detail.append(
                    f"\n{datetime.fromtimestamp(a, now.tzinfo):%d %b %H:%M} - {datetime.fromtimestamp(b, now.tzinfo):%d %b %H:%M}\n",
                    style="dim",
                )
                detail.append(
                    (
                        item.reason
                        if item.reason == "Counter rebase suspected"
                        else f"{burn:.1f} points across observations"
                    )
                    + "\n",
                    style="dim",
                )
            strip = self.hour_strip(self.selected_date)
            if strip.plain.strip():
                detail.append("\nDay pattern\n", style="bold")
                detail.append("00                    24\n", style="dim")
                detail.append_text(strip)
                detail.append("\n\n", style="dim")
                for hour in range(self.band * 4, self.band * 4 + 4):
                    cursor = self.midnight(self.selected_date) + timedelta(hours=hour)
                    item = self.model.bucket(cursor, cursor + timedelta(hours=1))
                    if item.label:
                        detail.append(
                            f"{hour:02}:00  {item.label} points\n", style="dim"
                        )
        observed = (
            datetime.fromtimestamp(snapshot.observed, now.tzinfo).strftime(
                "%d %b %H:%M %Z"
            )
            if snapshot.observed
            else "unavailable"
        )
        detail.append(f"\nAccount observed\n{observed}\n", style="dim")
        if snapshot.error:
            detail.append(
                "Stale / " + snapshot.error + "\n", style=self.current_theme.warning
            )
        if snapshot.forecast:
            detail.append(
                f"{snapshot.forecast.get('days_history', 0)} days in forecast\n",
                style="dim",
            )
        self.query_one("#detail", Static).update(detail)
        start = self.midnight(self.selected_date) + timedelta(hours=self.band * 4)
        end = start + timedelta(hours=4)
        if self.view == "day" and self.row_metadata:
            start = self.row_metadata[min(table.cursor_row, len(self.row_metadata) - 1)]
            end = start + timedelta(hours=1)
        if self.view == "alerts" and self.row_metadata:
            compact = detail.plain.splitlines()[0]
        else:
            bucket = self.model.bucket(start, end)
            amount = (
                f"{bucket.label} points"
                if bucket.label
                else self.unavailable_label(start, bucket)
            )
            compact = f"{self.selected_date:%a %d} {start.astimezone(now.tzinfo):%H:%M}-{end.astimezone(now.tzinfo):%H:%M} / {amount}"
            if self.size.width >= 70 and bucket.burn is not None:
                compact += f" / coverage {bucket.coverage:.0%}"
        compact += f"\nObserved {observed}" + (
            f" / stale {snapshot.error}" if snapshot.error else ""
        )
        self.query_one("#bottom-detail", Static).update(Text(compact, style="dim"))

    @on(DataTable.CellHighlighted, "#table")
    def cell_highlighted(self, event):
        if self.rebuilding or not self.snapshots:
            return
        row, col = event.coordinate
        if self.view == "calendar" and self.size.width >= 65:
            day, self.band = col, row
            self.selected_date = self.week + timedelta(days=min(6, day))
        self.draw_detail()

    @on(DataTable.RowHighlighted, "#table")
    def row_highlighted(self, event):
        if self.rebuilding or not self.snapshots:
            return
        if self.view == "calendar":
            self.selected_date = self.week + timedelta(days=min(6, event.cursor_row))
        self.draw_detail()

    @on(DataTable.CellSelected, "#table")
    @on(DataTable.RowSelected, "#table")
    def selected(self):
        self.action_inspect()

    @on(Select.Changed)
    def select_changed(self, event):
        if self.rebuilding or not self.snapshots or event.value is Select.BLANK:
            return
        if event.select.id == "palette":
            self.theme = str(event.value)
        elif event.select.id == "account" and event.value != "loading":
            if self.account_index == int(event.value):
                return
            self.account_index = int(event.value)
            self.sync_metrics()
        elif event.select.id == "metric":
            if self.metric == event.value:
                return
            self.metric = str(event.value)
        self.redraw()

    @on(Tabs.TabActivated, "#views")
    def tab_changed(self, event):
        self.view = event.tab.id
        self.draw_table()

    @on(Button.Pressed)
    def button_pressed(self, event):
        actions = {
            "refresh": self.action_refresh_data,
            "previous": self.action_previous_week,
            "next": self.action_next_week,
            "today": self.action_today,
        }
        if event.button.id in actions:
            actions[event.button.id]()

    def action_refresh_data(self):
        if not self.loading:
            self.fetch(True)

    def check_action(self, action, parameters):
        if action == "acknowledge":
            return (
                True
                if self.view == "alerts" and self.row_metadata and not self.loading
                else False
            )
        return True

    def action_acknowledge(self):
        if self.view == "alerts" and self.row_metadata and not self.loading:
            event = self.row_metadata[self.query_one("#table", DataTable).cursor_row]
            try:
                self.source.journal.acknowledge(event["id"])
                self.draw_table()
            except OSError:
                self.notify("Could not record acknowledgement", severity="error")

    def action_inspect(self):
        if self.view == "history" and self.row_metadata:
            self.selected_date = self.row_metadata[
                self.query_one("#table", DataTable).cursor_row
            ]
            self.week = self.selected_date - timedelta(
                days=self.selected_date.weekday()
            )
        if self.view in ("calendar", "history"):
            self.view = "day"
            self.draw_table()

    def action_back(self):
        self.action_view("calendar")

    def action_view(self, view):
        self.view = view
        self.query_one("#views", Tabs).active = view
        self.draw_table()
        self.query_one("#table", DataTable).focus()

    def shift_week(self, delta):
        self.week += timedelta(days=delta)
        self.selected_date += timedelta(days=delta)
        self.view = "calendar"
        self.query_one("#views", Tabs).active = "calendar"
        self.draw_table()

    def action_previous_week(self):
        self.shift_week(-7)

    def action_next_week(self):
        self.shift_week(7)

    def action_today(self):
        self.selected_date = self.source.now.date()
        self.week = self.selected_date - timedelta(days=self.selected_date.weekday())
        self.band = self.source.now.hour // 4
        self.action_view("calendar")

    def action_account(self):
        if self.snapshots:
            self.query_one("#account", Select).value = str(
                (self.account_index + 1) % len(self.snapshots)
            )

    def action_metric(self):
        if self.snapshots:
            keys = [m.key for m in self.snapshot.meters if m.key != "session"]
            if keys:
                self.query_one("#metric", Select).value = keys[
                    (keys.index(self.metric) + 1) % len(keys)
                ]

    def action_scenario(self):
        if self.source.demo and not self.loading:
            self.source = DemoSource(
                SCENARIOS[(SCENARIOS.index(self.source.scenario) + 1) % len(SCENARIOS)]
            )
            self.fetch()

    def action_theme_mode(self):
        themes = list(THEMES.values())
        self.query_one("#palette", Select).value = themes[
            (themes.index(self.theme) + 1) % len(themes)
        ]
