import base64
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pyotp
import pytest
from PIL import Image
from werkzeug.datastructures import MultiDict
from werkzeug.security import check_password_hash, generate_password_hash

from backend.app import LoginAttempt, Reservation, User, _decrypt, _digest, _encrypt, create_app, db


@pytest.fixture()
def app():
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DOCUMENT_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"norus-test-encryption-key-32byte").decode(),
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SESSION_COOKIE_SECURE": False,
    })
    with application.app_context():
        db.drop_all()
        db.create_all()
        guest = User(email="guest@example.com", name="Guest", password_hash=generate_password_hash("strong-password-123"), role="guest")
        admin = User(email="admin@example.com", name="Admin", password_hash=generate_password_hash("strong-password-123"),
                     role="admin", totp_secret=_encrypt(pyotp.random_base32().encode()), totp_enabled=True)
        db.session.add_all([guest, admin])
        db.session.flush()
        reservation = Reservation(
            code="NOR-TEST-8421", guest_id=guest.id, guest_name=guest.name, guest_email=guest.email,
            property="Las Condes", unit="402", checkin_at=datetime.now(UTC) - timedelta(hours=3),
            checkout_at=datetime.now(UTC) + timedelta(days=1), status="Activa",
        )
        db.session.add(reservation)
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    return client.get("/api/csrf").json["csrfToken"]


def test_guest_access_requires_matching_reservation_and_email(client):
    response = client.post("/api/auth/guest-access", json={"code": "NOR-TEST-8421", "email": "wrong@example.com"})
    assert response.status_code == 401
    response = client.post("/api/auth/guest-access", json={"code": "NOR-TEST-8421", "email": "guest@example.com"})
    assert response.status_code == 200
    assert response.json["reservation"]["property"] == "Las Condes"
    assert client.get("/api/guest/dashboard").status_code == 200


def test_guest_precheckin_accepts_valid_signed_at_timestamp(client):
    response = client.post("/api/auth/guest-access", json={"code": "NOR-TEST-8421", "email": "guest@example.com"})
    assert response.status_code == 200
    buffer = BytesIO()
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(buffer, format="PNG")
    png_bytes = buffer.getvalue()
    precheckin = client.post(
        "/api/guest/precheckin",
        data=MultiDict([
            ("name", "Guest"),
            ("email", "guest@example.com"),
            ("phone", "987654321"),
            ("birthDate", "1990-01-15"),
            ("signedAt", datetime.now(UTC).isoformat()),
            ("termsAccepted", "true"),
            ("signature", (BytesIO(png_bytes), "firma.png", "image/png")),
            ("identityDocuments", (BytesIO(b"doc-data"), "documento.pdf", "application/pdf")),
        ]),
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert precheckin.status_code == 201
    assert precheckin.json["ok"] is True


def test_guest_cannot_access_admin_api(client):
    client.post("/api/auth/guest-access", json={"code": "NOR-TEST-8421", "email": "guest@example.com"})
    response = client.get("/api/admin/tickets")
    assert response.status_code == 403


def test_admin_login_and_access_work_without_mfa(client, app):
    response = client.post("/api/auth/login", json={
        "email": "admin@example.com", "password": "strong-password-123"
    })
    assert response.status_code == 200
    assert response.json["user"]["role"] == "admin"
    token = client.get("/api/csrf").json["csrfToken"]
    denied = client.post("/api/admin/reservations", json={}, headers={"X-CSRF-Token": "wrong"})
    assert denied.status_code == 403
    authorized = client.get("/api/admin/tickets")
    assert authorized.status_code == 200
    assert token


def test_staff_can_access_without_mfa(client, app):
    with app.app_context():
        staff = User(email="staff@example.com", name="Staff", password_hash=generate_password_hash("strong-password-123"), role="staff")
        db.session.add(staff)
        db.session.commit()

    login = client.post("/api/auth/login", json={"email": "staff@example.com", "password": "strong-password-123"})
    assert login.status_code == 200
    assert login.json["user"]["role"] == "staff"
    assert client.get("/api/admin/tickets").status_code == 200


def test_bootstrap_admin_can_access_without_mfa(client, app):
    with app.app_context():
        bootstrap = User(
            email="bootstrap@example.com",
            name="Bootstrap Admin",
            password_hash=generate_password_hash("bootstrap-password-123"),
            role="admin",
        )
        db.session.add(bootstrap)
        db.session.commit()

    login = client.post("/api/auth/login", json={
        "email": "bootstrap@example.com", "password": "bootstrap-password-123",
    })
    assert login.status_code == 200
    assert login.json["user"]["role"] == "admin"
    assert client.get("/api/auth/me").json["user"]["mfaEnabled"] is False
    assert client.get("/api/admin/tickets").status_code == 200


def test_bootstrap_admin_password_is_reset_when_the_env_value_changes(app):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        user.password_hash = generate_password_hash("stale-password-999")
        db.session.commit()

    previous_email = os.environ.get("NORUS_BOOTSTRAP_ADMIN_EMAIL")
    previous_password = os.environ.get("NORUS_BOOTSTRAP_ADMIN_PASSWORD")
    os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
    os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = "fresh-admin-password-123"
    try:
        refreshed = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DOCUMENT_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"norus-test-encryption-key-32byte").decode(),
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SESSION_COOKIE_SECURE": False,
        })
        with refreshed.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            assert check_password_hash(user.password_hash, "fresh-admin-password-123")
    finally:
        if previous_email is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_EMAIL", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = previous_email
        if previous_password is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_PASSWORD", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = previous_password


def test_admin_bypass_rate_limit_for_bootstrap_account(client, app):
    old_email = os.environ.get("NORUS_BOOTSTRAP_ADMIN_EMAIL")
    old_password = os.environ.get("NORUS_BOOTSTRAP_ADMIN_PASSWORD")
    os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
    os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = "strong-password-123"
    try:
        with app.app_context():
            email_digest = _digest("admin@example.com")
            ip_digest = _digest("127.0.0.1")
            db.session.add_all([
                LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False),
                LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False),
                LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False),
                LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False),
                LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False),
            ])
            db.session.commit()

        response = client.post("/api/auth/login", json={
            "email": "admin@example.com", "password": "strong-password-123",
        })
        assert response.status_code == 200
        assert response.json["user"]["role"] == "admin"
    finally:
        if old_email is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_EMAIL", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = old_email
        if old_password is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_PASSWORD", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = old_password


def test_bootstrap_admin_login_uses_env_password_when_database_hash_is_stale(client, app):
    old_email = os.environ.get("NORUS_BOOTSTRAP_ADMIN_EMAIL")
    old_password = os.environ.get("NORUS_BOOTSTRAP_ADMIN_PASSWORD")
    os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = "admin@example.com"
    os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = "fresh-admin-password-123"
    try:
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            user.password_hash = generate_password_hash("totally-old-password")
            db.session.commit()

        response = client.post("/api/auth/login", json={
            "email": "admin@example.com",
            "password": "fresh-admin-password-123",
        })
        assert response.status_code == 200
        assert response.json["user"]["role"] == "admin"
        with app.app_context():
            user = User.query.filter_by(email="admin@example.com").first()
            assert check_password_hash(user.password_hash, "fresh-admin-password-123")
    finally:
        if old_email is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_EMAIL", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_EMAIL"] = old_email
        if old_password is None:
            os.environ.pop("NORUS_BOOTSTRAP_ADMIN_PASSWORD", None)
        else:
            os.environ["NORUS_BOOTSTRAP_ADMIN_PASSWORD"] = old_password


def test_admin_can_create_staff_account_once(client):
    with client.application.app_context():
        admin = User.query.filter_by(email="admin@example.com").first()
        code = pyotp.TOTP(_decrypt(admin.totp_secret).decode()).now()
    login = client.post("/api/auth/login", json={
        "email": "admin@example.com", "password": "strong-password-123", "totp": code,
    })
    assert login.status_code == 200
    token = csrf(client)
    payload = {"email": "newstaff@example.com", "name": "New Staff", "role": "staff"}
    created = client.post("/api/admin/users", json=payload, headers={"X-CSRF-Token": token})
    assert created.status_code == 201
    assert len(created.json["temporaryPassword"]) >= 24
    duplicate = client.post("/api/admin/users", json=payload, headers={"X-CSRF-Token": token})
    assert duplicate.status_code == 409


def test_ticket_requires_authenticated_active_reservation_and_csrf(client):
    client.post("/api/auth/guest-access", json={"code": "NOR-TEST-8421", "email": "guest@example.com"})
    denied = client.post("/api/guest/tickets", data={"type": "issue", "title": "Acceso", "details": "La cerradura no responde"})
    assert denied.status_code == 403
    response = client.post("/api/guest/tickets", data={
        "type": "issue", "title": "Acceso", "details": "La cerradura no responde",
    }, headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 201
    assert response.json["ticket"]["status"] == "Pendiente"
