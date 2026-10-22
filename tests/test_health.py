import time


def test_health_is_ok_once_app_and_database_are_up(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_the_app_starts_and_answers_without_waiting_for_plex_tv(start_app, gateway, signed_in):
    gateway.plex_tv_answers.clear()  # the startup check of the Owner token hangs
    started = time.monotonic()
    try:
        with start_app() as client:
            assert client.get("/health").status_code == 200
        assert time.monotonic() - started < 2
    finally:
        gateway.plex_tv_answers.set()
