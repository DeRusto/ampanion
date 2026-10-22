def test_home_shows_the_ampanion_layout_with_home_and_settings_in_the_sidebar(client, signed_in):
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<nav class="sidebar"' in html
    assert 'href="/"' in html and ">Home<" in html
    assert 'href="/settings"' in html and ">Settings<" in html
    assert '<span class="amp">Amp</span><span class="anion">anion</span>' in html
    assert "<h1>Home</h1>" in html


def test_settings_page_uses_the_same_layout(client, signed_in):
    response = client.get("/settings")

    assert response.status_code == 200
    assert '<nav class="sidebar"' in response.text
    assert "<h1>Settings</h1>" in response.text


def test_layout_is_styled_in_plex_colours(client, signed_in):
    css = client.get("/static/ampanion.css")

    assert css.status_code == 200
    assert "#e5a00d" in css.text.lower()  # Plex amber
