import uuid


def test_first_run_creates_the_database_in_config(start_app, config_dir):
    with start_app():
        pass

    assert (config_dir / "ampanion.db").is_file()


def test_first_run_generates_a_client_identifier_and_session_secret(start_app):
    with start_app() as client:
        store = client.app.state.store

        uuid.UUID(store.client_identifier)  # a valid UUID
        assert len(store.session_secret) >= 32


def test_restart_keeps_the_client_identifier_and_session_secret(start_app):
    with start_app() as client:
        first = (client.app.state.store.client_identifier, client.app.state.store.session_secret)

    with start_app() as client:
        again = (client.app.state.store.client_identifier, client.app.state.store.session_secret)

    assert again == first
