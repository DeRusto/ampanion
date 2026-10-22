"""Nightly schedule: the nightly run at the run time in TZ, due Mixes, Refresh all now, one run at a time, catch-up.

Here TZ is New York. The clock starts on Monday 2026-01-05 at 07:00 there (12:00 UTC), after that day's 04:00 run.
Background work only happens when a test calls `background(client)`, standing in for the scheduler's thread.
"""

from zoneinfo import ZoneInfo

import pytest

from tests.test_listeners import add_listener, sidebar
from tests.test_on_repeat import PAL, background, play, on_repeat, pal, refresh, save_mix  # noqa: F401
from tests.test_sign_in import csrf_from


@pytest.fixture
def tz():
    return ZoneInfo("America/New_York")


def until(clock, utc_hour, day):
    """Advance the fake clock to 2026-01-<day> at utc_hour:00 UTC."""
    clock.advance(seconds=(clock.now().replace(day=day, hour=utc_hour, minute=0) - clock.now()).total_seconds())


def history(client, account_id=8):
    return client.get(f"/listeners/{account_id}?tab=history").text


def runs(client, account_id=8):
    return history(client, account_id).count("<tr class=")


def set_schedule(client, run_time="04:00", **cadences):
    csrf = csrf_from(client.get("/settings").text)
    return client.post("/settings/schedule", data={"csrf_token": csrf, "run_time": run_time} | cadences)


def refresh_all(client):
    return client.post("/refresh", data={"csrf_token": csrf_from(client.get("/").text)})


def test_the_nightly_run_fires_at_the_run_time_in_tz(client, gateway, clock, pal):
    background(client)  # Pal's first Refresh, from being added
    assert "Next nightly Refresh: Tue 06 Jan 04:00" in client.get("/").text
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)

    until(clock, 8, day=6)  # 03:00 in New York
    background(client)
    assert on_repeat(gateway) is None

    until(clock, 9, day=6)  # 04:00 in New York
    background(client)
    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]
    assert "2026-01-06 04:00" in history(client)  # times show in TZ
    assert "Next nightly Refresh: Wed 07 Jan 04:00" in client.get("/").text


def test_the_playlists_tab_shows_refresh_times_in_tz(client, gateway, clock, pal):
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)

    refresh(client)  # at 12:00 UTC, 07:00 in New York

    html = client.get("/listeners/8?tab=playlists").text
    assert "2026-01-05 07:00" in html and "UTC" not in html


def test_the_nightly_run_refreshes_every_listener_once(client, gateway, clock, pal):
    add_listener(client, 42)
    background(client)

    until(clock, 9, day=6)
    background(client)
    background(client)

    assert runs(client, 8) == 2 and runs(client, 42) == 2  # added, then nightly; no second nightly


def test_a_weekly_mix_is_rebuilt_only_after_the_monday_slot(client, gateway, clock, pal):
    set_schedule(client, on_repeat="weekly")
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    background(client)  # built on Monday after the slot
    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]
    play(gateway, clock, 8, "t9", times=9)

    until(clock, 9, day=6)  # Tuesday's run: not due
    background(client)
    assert "t9" not in on_repeat(gateway).items
    assert "On Repeat" not in history(client).split("</tr>")[1]  # Tuesday's record, newest first after the header

    until(clock, 9, day=12)  # next Monday's run: due
    background(client)
    assert on_repeat(gateway).items[0] == "t9"


def test_refresh_now_rebuilds_weekly_mixes_without_moving_the_weekly_schedule(client, gateway, clock, pal):
    set_schedule(client, on_repeat="weekly")
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    background(client)
    play(gateway, clock, 8, "t9", times=9)

    until(clock, 15, day=7)  # Wednesday
    refresh(client)
    assert on_repeat(gateway).items[0] == "t9"

    play(gateway, clock, 8, "t10", times=20)
    until(clock, 9, day=12)  # still rebuilt on Monday
    background(client)
    assert on_repeat(gateway).items[0] == "t10"


def test_refresh_all_now_refreshes_every_listener_in_the_background(client, gateway, clock, pal):
    add_listener(client, 42)
    background(client)

    response = refresh_all(client)
    assert response.url.path == "/"
    home = response.text
    assert 'role="status"' in home and "disabled" in home.split("Refresh all now")[0].rsplit("<button", 1)[1]
    assert "disabled" in client.get("/listeners/8").text.split("Refresh now")[0].rsplit("<button", 1)[1]
    assert refresh_all(client).status_code == 409  # one run at a time
    assert client.post("/listeners/8/refresh", data={"csrf_token": csrf_from(home)}).status_code == 409

    background(client)
    assert runs(client, 8) == 2 and runs(client, 42) == 2
    assert "disabled" not in client.get("/").text.split("Refresh all now")[0].rsplit("<button", 1)[1]


def test_run_time_and_cadence_are_edited_in_settings_and_shown_on_the_mixes_tab(client, gateway, clock, pal):
    html = client.get("/settings").text
    assert 'name="run_time" value="04:00"' in html
    assert "Refreshed daily" in client.get("/listeners/8").text

    assert set_schedule(client, "23:30", on_repeat="weekly").status_code == 200
    assert set_schedule(client, "25:00").status_code == 400
    assert set_schedule(client, on_repeat="hourly").status_code == 400

    assert 'name="run_time" value="23:30"' in client.get("/settings").text
    assert "Refreshed weekly on Mondays" in client.get("/listeners/8").text
    assert 'name="on_repeat"' not in client.get("/listeners/8").text  # read-only there
    assert "Next nightly Refresh: Mon 05 Jan 23:30" in client.get("/").text

    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    background(client)
    assert on_repeat(gateway).summary == "Made by ampanion: On Repeat. Refreshed weekly; edits are replaced."


def test_changing_the_run_time_doesnt_start_a_run(client, gateway, clock, pal):
    background(client)
    set_schedule(client, "06:00")  # today's 06:00 has passed, but it never was the run time
    background(client)

    assert runs(client) == 1


def test_a_missed_run_is_caught_up_once_on_startup(start_app, gateway, clock, sign_in):
    gateway.add_account("dee", token="owner-token", account_id=42)
    gateway.add_server(owner_token="owner-token")
    with start_app() as client:
        sign_in(client, "owner-token")
        add_listener(client, 42)
        background(client)

    until(clock, 12, day=9)  # down through four nightly runs
    with start_app() as client:
        background(client)
        background(client)
        sign_in(client, "owner-token")
        assert runs(client, 42) == 2


def test_a_nightly_run_killed_midway_is_caught_up_on_startup(start_app, gateway, clock, sign_in):
    gateway.add_account("dee", token="owner-token", account_id=42)
    gateway.add_server(owner_token="owner-token")
    with start_app() as client:
        sign_in(client, "owner-token")
        add_listener(client, 42)
        background(client)
        until(clock, 9, day=6)  # the 04:00 run starts, and ampanion dies during it
        gateway.killed_reading_history = True
        with pytest.raises(SystemExit):
            background(client)

    gateway.killed_reading_history = False
    with start_app() as client:
        background(client)
        sign_in(client, "owner-token")
        assert "<tr class=\"ok\"" in history(client, 42).split("</tr>")[1]  # the newest row: the caught-up run


def test_a_fresh_install_has_nothing_to_catch_up(client, gateway, clock, pal):
    background(client)

    assert runs(client) == 1  # only the Refresh from adding Pal


def test_adding_a_listener_starts_a_background_refresh(client, gateway, clock, pal):
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)
    assert on_repeat(gateway) is None

    background(client)

    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]


def test_enabling_a_mix_starts_a_background_refresh(client, gateway, clock, pal):
    csrf = csrf_from(client.get("/listeners/8").text)
    client.post("/listeners/8/mixes/on_repeat", data={"csrf_token": csrf, "window_days": "30", "length": "30"})
    background(client)
    for n in range(1, 6):
        play(gateway, clock, 8, f"t{n}", times=2, days_ago=n)

    save_mix(client, length="20")  # turned back on
    background(client)
    assert on_repeat(gateway).items == ["t1", "t2", "t3", "t4", "t5"]

    save_mix(client, length="25")  # already on: no Refresh
    background(client)
    assert runs(client) == 2


def test_one_listeners_failure_doesnt_stop_the_others_and_isnt_retried(client, gateway, clock, pal):
    add_listener(client, 42)
    background(client)
    for n in range(1, 6):
        play(gateway, clock, 1, f"t{n}", times=2, days_ago=n)
    gateway.unshare(8)  # Pal's share is gone, so Pal's Refresh fails

    until(clock, 9, day=6)
    background(client)
    clock.advance(hours=1)
    background(client)

    assert on_repeat(gateway, "owner-token").items == ["t1", "t2", "t3", "t4", "t5"]
    assert "No library share on this server" in history(client)
    assert runs(client, 8) == 2
    assert 'class="status failed"' in sidebar(client.get("/").text)


def test_refresh_records_older_than_90_days_are_pruned(client, gateway, clock, pal):
    background(client)  # Monday 2026-01-05
    clock.advance(days=89)
    refresh(client)
    assert "2026-01-05" in history(client)

    clock.advance(days=2)
    refresh(client)
    assert "2026-01-05" not in history(client)


def test_reset_is_unavailable_while_a_refresh_runs(client, gateway, clock, pal):
    page = client.get("/settings/reset").text  # Pal's first Refresh hasn't finished
    assert "disabled" in page.split("Reset ampanion</button>")[0].rsplit("<button", 1)[1]
    assert client.post("/settings/reset", data={"csrf_token": csrf_from(page)}).status_code == 409

    background(client)
    page = client.get("/settings/reset").text
    assert "disabled" not in page.split("Reset ampanion</button>")[0].rsplit("<button", 1)[1]
    response = client.post("/settings/reset", data={"csrf_token": csrf_from(page)}, follow_redirects=False)
    assert response.headers["location"] == "/signin"

    until(clock, 9, day=6)
    background(client)  # the scheduler carries on after a Reset


def test_mix_settings_and_turning_off_wait_for_a_running_refresh(client, gateway, clock, pal):
    seen = {}

    def owner_clicks():  # while Pal's first Refresh is reading their history
        page = client.get("/listeners/8").text
        seen["save disabled"] = "disabled" in page.split(">Save</button>")[0].rsplit("<button", 1)[1]
        seen["off"] = save_mix(client, enabled="").status_code
        seen["save"] = save_mix(client, length="10").status_code
        page = client.get("/listeners/8/turn-off").text
        seen["turn off disabled"] = "disabled" in page.split("Turn off Pal</button>")[0].rsplit("<button", 1)[1]
        seen["turn off"] = client.post("/listeners/8/turn-off", data={"csrf_token": csrf_from(page)}).status_code

    gateway.while_reading_history = owner_clicks
    background(client)
    gateway.while_reading_history = lambda: None

    assert seen == {"save disabled": True, "off": 409, "save": 409, "turn off disabled": True, "turn off": 409}
    assert 'name="length" value="30"' in client.get("/listeners/8").text
    assert save_mix(client, length="10").status_code == 200
    assert 'name="length" value="10"' in client.get("/listeners/8").text
    page = client.get("/listeners/8/turn-off").text
    assert client.post("/listeners/8/turn-off", data={"csrf_token": csrf_from(page)}).url.path == "/"
