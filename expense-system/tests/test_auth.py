from app.security import hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_register_login_logout_flow(client):
    r = client.post("/register", data={"username": "carol", "password": "secret1"})
    assert r.status_code == 200
    assert "工作台" in r.text  # landed on the dashboard, logged in

    # Logged-in session can reach a protected page.
    assert client.get("/expenses").status_code == 200

    # Logout clears the session -> protected routes redirect to /login.
    client.get("/logout")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_rejects_bad_credentials(client):
    client.post("/register", data={"username": "dave", "password": "secret1"})
    client.get("/logout")
    r = client.post("/login", data={"username": "dave", "password": "nope"})
    assert r.status_code == 200
    assert "用户名或密码错误" in r.text


def test_short_credentials_rejected(client):
    r = client.post("/register", data={"username": "ab", "password": "secret1"})
    assert "至少需要 3 个字符" in r.text
