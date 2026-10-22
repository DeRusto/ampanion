"""Scheduler: the nightly run, Refresh now, background Refreshes, and one run at a time.

Every run waits in one queue and runs on the scheduler's thread, one after another. Requests only queue a run, so
pages stay fast, and tests step the queue by calling `tick()` with a fake clock instead of starting the thread.
"""

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from random import Random

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ampanion import refresh
from ampanion.clock import Clock
from ampanion.gateway import Gateway, GatewayError, TokenExpired
from ampanion.mixes import MIXES, Mix, mix_cadence
from ampanion.store import Listener, Store

log = logging.getLogger(__name__)

CADENCES = ("daily", "weekly")
MONDAY = 0
POLL_SECONDS = 30
EXPIRED = "Plex sign-in expired"


def last_slot(now: datetime, run_time: time, tz: tzinfo, weekday: int | None = None) -> datetime:
    """The most recent nightly slot at or before now (on `weekday` only, if given), in TZ's wall-clock time."""
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), run_time, tzinfo=tz)
    if slot > local:
        slot -= timedelta(days=1)
    if weekday is not None:
        slot -= timedelta(days=(slot.weekday() - weekday) % 7)
    return slot


def next_slot(now: datetime, run_time: time, tz: tzinfo) -> datetime:
    return last_slot(now, run_time, tz) + timedelta(days=1)


@dataclass(frozen=True)
class Run:
    account_ids: list[int] | None  # None: every Listener, as they are when the run starts
    nightly: bool = False  # only a nightly run skips Mixes that aren't due
    slot: datetime | None = None  # the nightly slot this run is for, recorded as done once it has finished


class Scheduler:
    def __init__(self, store: Store, gateway: Gateway, clock: Clock, rng: Random, tz: tzinfo):
        self.store, self.gateway, self.clock, self.rng, self.tz = store, gateway, clock, rng, tz
        self._queue: deque[Run] = deque()  # the head is the run in progress
        self._lock = threading.Lock()  # guards the queue
        self._running = threading.Lock()  # one run at a time
        self._wake = threading.Event()
        self.progress: str | None = None  # what the run in progress is doing, for the Refresh buttons
        self.running = False  # a run has started and not finished (one merely queued reads everything when it starts)
        if store.last_slot is None:  # a fresh install has missed no run
            self.mark_slot_done()
        self._started = False  # the first tick checks the Owner token, on the scheduler's thread, not in startup

    @property
    def busy(self) -> bool:
        return bool(self._queue)

    def next_run(self) -> datetime:
        return next_slot(self.clock.now(), self.store.run_time, self.tz)

    def mark_slot_done(self) -> None:
        self.store.set_last_slot(last_slot(self.clock.now(), self.store.run_time, self.tz))

    def check_owner_token(self) -> None:
        """Ask plex.tv whether the Owner token still works. Only a 401 proves it dead; Plex being down doesn't."""
        if self.store.owner_token is None or self.store.owner_token_expired:
            return
        try:
            self.gateway.check_token(self.store.owner_token)
        except TokenExpired:
            self._owner_token_expired()
        except GatewayError:
            log.warning("Couldn't check the Plex sign-in with plex.tv")

    def _owner_token_expired(self) -> None:
        """Pause every Refresh, with one record saying so (not one per Listener)."""
        if self.store.owner_token_expired:
            return
        self.store.set_owner_token_expired()
        now = self.clock.now()
        self.store.finish_refresh(self.store.start_refresh(None, now), now, "failed", [], EXPIRED)

    def catch_up(self) -> None:
        """The Owner signed in again: one run, like a nightly one, for whatever was missed while paused."""
        with self._lock:
            self._queue.append(Run(None, nightly=True))
            self.progress = self.progress or "Refresh starting…"
        self._wake.set()

    def request(self, account_ids: list[int] | None) -> None:
        """Queue a Refresh of these Listeners (None: all) that rebuilds every enabled Mix."""
        with self._lock:
            self._queue.append(Run(account_ids))
            self.progress = self.progress or "Refresh starting…"
        self._wake.set()

    def when_idle(self, action: Callable[[], None]) -> bool:
        """Do action unless a run is in progress or waiting (False); no run can start while it runs."""
        with self._lock:
            if self._queue:
                return False
            action()
        return True

    def unless_running(self, action: Callable[[], None]) -> bool:
        """Do action unless a run is in progress (False); a queued run waits for it, then sees what it changed."""
        with self._lock:
            if self.running:
                return False
            action()
        return True

    def request_now(self, account_ids: list[int] | None) -> bool:
        """Refresh now: refused (False) while a run is in progress or waiting."""
        if not self.when_idle(lambda: self._queue.append(Run(account_ids))):
            return False
        self.progress = "Refresh starting…"
        self._wake.set()
        return True

    def tick(self) -> None:
        """Queue the nightly run if its slot has come (once, however many were missed), then work through the queue."""
        if not self._started:
            self._started = True
            self.check_owner_token()
        slot = last_slot(self.clock.now(), self.store.run_time, self.tz)
        with self._lock:
            if self.store.last_slot < slot and all(r.slot != slot for r in self._queue):
                self._queue.append(Run(None, nightly=True, slot=slot))
        if not self._running.acquire(blocking=False):
            return
        try:
            while self._queue:
                with self._lock:
                    run, self.running = self._queue[0], True
                self._run(run)
                # only now: a run cut short (ampanion killed) is caught up on the next start. Never backwards: a run
                # time saved during the run has already marked its own, later slot done.
                if run.slot and run.slot > self.store.last_slot:
                    self.store.set_last_slot(run.slot)
                with self._lock:
                    self._queue.popleft()
                    self.running = False
                    self.progress = "Refresh starting…" if self._queue else None
        finally:
            self.running = False
            self._running.release()

    def _run(self, run: Run) -> None:
        if run.nightly:
            self.check_owner_token()
        if self.store.owner_token_expired:  # paused: no Playlist is touched
            return
        listeners = self.store.listeners()
        if run.account_ids is not None:
            listeners = [l for l in listeners if l.account_id in run.account_ids]
        due = self._due() if run.nightly else (lambda account_id, mix: True)

        def on_listener(i: int, listener: Listener) -> None:
            self.progress = f"Refreshing {listener.name} ({i + 1} of {len(listeners)})"

        try:
            refresh.run(self.store, self.gateway, self.clock, self.rng, listeners, due, on_listener)
        except refresh.OwnerTokenExpired:
            self._owner_token_expired()
        except Exception:  # the queue must keep moving
            log.exception("Refresh run failed")

    def _due(self) -> refresh.Due:
        monday = last_slot(self.clock.now(), self.store.run_time, self.tz, MONDAY)

        def due(account_id: int, mix: Mix) -> bool:
            if mix_cadence(self.store, mix) == "daily":
                return True
            built = self.store.mix_built(account_id, mix.key)
            return built is None or built < monday

        return due

    def serve_forever(self) -> None:
        """The scheduler's thread: tick now, then whenever woken by a request or every POLL_SECONDS."""
        while True:
            self._wake.clear()
            try:
                self.tick()
            except Exception:
                log.exception("Scheduler tick failed")
            self._wake.wait(POLL_SECONDS)


def add_schedule_routes(app: FastAPI, *, store: Store, scheduler: Scheduler, templates: Jinja2Templates):
    def refresh_now(account_ids: list[int] | None, back: str):
        if store.owner_token_expired:
            raise HTTPException(409, f"{EXPIRED}, so Refreshes are paused. Sign in again to resume them.")
        if not scheduler.request_now(account_ids):
            raise HTTPException(409, "A Refresh is already running")
        return RedirectResponse(back, 303)

    @app.get("/refresh/button")
    def refresh_button(request: Request, account_id: int | None = None):
        """A Refresh now button as it is now: what a busy one polls for (HTMX), until the run ends."""
        return templates.TemplateResponse(request, "refresh_button.html", {"account_id": account_id})

    @app.post("/refresh")
    def refresh_all_now():
        return refresh_now(None, "/")

    @app.post("/listeners/{account_id}/refresh")
    def refresh_listener_now(account_id: int):
        if store.listener(account_id) is None:
            raise HTTPException(404, "Not a Listener")
        return refresh_now([account_id], f"/listeners/{account_id}")

    @app.post("/settings/schedule")
    async def save_schedule(request: Request):
        form = await request.form()
        try:
            run_time = datetime.strptime(form.get("run_time", ""), "%H:%M").time()
        except ValueError:
            raise HTTPException(400, "The run time must be a time like 04:00") from None
        cadences = {key: form.get(key, mix_cadence(store, mix)) for key, mix in MIXES.items()}
        if any(c not in CADENCES for c in cadences.values()):
            raise HTTPException(400, "A Mix's cadence must be daily or weekly")
        store.set_run_time(run_time)
        for key, cadence in cadences.items():
            store.set_cadence(key, cadence)
        scheduler.mark_slot_done()  # a new run time never means a run was missed
        return RedirectResponse("/settings", 303)
