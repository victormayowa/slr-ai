from sqlalchemy import select

import models
from database import SessionLocal


def test_me_returns_the_signed_in_user(client, make_user):
    email, headers = make_user(first_name="Ada", last_name="Lovelace")

    me = client.get("/api/auth/me", headers=headers).json()

    assert me["email"] == email
    assert me["name"] == "Ada Lovelace"
    assert me["organizations"] == []


def test_deactivated_account_is_locked_out(client, make_user, login):
    email, headers = make_user()
    with SessionLocal() as db:
        db.scalar(select(models.User).where(models.User.email == email)).is_active = False
        db.commit()

    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert (
        client.post("/api/auth/login", json={"identifier": email, "password": "correct horse battery"}).status_code
        == 401
    )
