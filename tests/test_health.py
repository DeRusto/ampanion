def test_health_is_ok_once_app_and_database_are_up(client):
    response = client.get("/health")

    assert response.status_code == 200
