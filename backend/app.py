from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import smtplib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Any

import pyotp
import qrcode
from dotenv import load_dotenv
from qrcode.image.svg import SvgPathImage
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from flask import Flask, current_app, jsonify, request, send_file, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

db = SQLAlchemy()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ALLOWED_PROPERTIES = {"Las Condes", "Providencia", "LUUM"}
ALLOWED_TICKET_TYPES = {"supplies", "issue"}
ALLOWED_TICKET_STATES = {"Pendiente", "En proceso", "Resuelto"}
ALLOWED_RESERVATION_STATES = {"Confirmada", "Pre check-in", "Activa", "Check-out"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS = 2
MAX_ISSUE_PHOTOS = 3


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="guest")
    totp_secret = db.Column(db.LargeBinary)
    totp_enabled = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    document_type = db.Column(db.String(20), nullable=False, default="RUT")
    document_number = db.Column(db.String(50), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class ReservationStatus(db.Model):
    __tablename__ = "reservation_status"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(150), nullable=False)


class RoomHistory(db.Model):
    __tablename__ = "room_history"

    id = db.Column(db.Integer, primary_key=True)
    room_number = db.Column(db.String(50), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    changed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    guest_name = db.Column(db.String(160), nullable=False)
    guest_email = db.Column(db.String(254), nullable=False)
    property = db.Column(db.String(32), nullable=False, index=True)
    unit = db.Column(db.String(32), nullable=False)
    checkin_at = db.Column(db.DateTime(timezone=True), nullable=False)
    checkout_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="Confirmada")
    access_code_encrypted = db.Column(db.LargeBinary)
    wifi_name = db.Column(db.String(128))
    wifi_password_encrypted = db.Column(db.LargeBinary)
    checked_out_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class PreCheckin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservation.id"), unique=True, nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    birth_date = db.Column(db.Date, nullable=False)
    signed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    signature_ip = db.Column(db.String(45), nullable=False)
    signature_encrypted = db.Column(db.LargeBinary, nullable=False)
    agreement_version = db.Column(db.String(32), nullable=False)
    terms_accepted = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class PrivateDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservation.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(80), nullable=False)
    ciphertext = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservation.id"), nullable=False, index=True)
    type = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(240), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    property = db.Column(db.String(32), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="Pendiente", index=True)
    assigned = db.Column(db.String(120))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class TicketPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    ciphertext = db.Column(db.LargeBinary, nullable=False)


class Survey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservation.id"), unique=True, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comments = db.Column(db.String(1000), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))


class LoginAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email_digest = db.Column(db.String(64), nullable=False, index=True)
    ip_digest = db.Column(db.String(64), nullable=False, index=True)
    succeeded = db.Column(db.Boolean, nullable=False, default=False)
    attempted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True)


class Outbox(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(80), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    sent_at = db.Column(db.DateTime(timezone=True))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _require_secret_configuration(app: Flask) -> None:
    if app.config["TESTING"]:
        return
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("Configure NORUS_SECRET_KEY before starting the API.")
    key = app.config.get("DOCUMENT_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Configure NORUS_DOCUMENT_KEY with a base64-encoded 32-byte key.")
    try:
        decoded = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    except (ValueError, base64.binascii.Error) as error:
        raise RuntimeError("NORUS_DOCUMENT_KEY must be a base64-encoded 32-byte key.") from error
    if len(decoded) != 32:
        raise RuntimeError("NORUS_DOCUMENT_KEY must decode to exactly 32 bytes.")


def _encryption_key() -> bytes:
    if current_app.config["TESTING"]:
        return b"norus-test-encryption-key-32byte"
    encoded = current_app.config["DOCUMENT_ENCRYPTION_KEY"]
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def _encrypt(data: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(_encryption_key()).encrypt(nonce, data, None)


def _decrypt(data: bytes) -> bytes:
    return AESGCM(_encryption_key()).decrypt(data[:12], data[12:], None)


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _csrf_protected():
    expected = session.get("csrf_token", "")
    provided = request.headers.get("X-CSRF-Token", "")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _current_user() -> User | None:
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _current_user():
            return _json_error("Se requiere una sesión autenticada.", 401)
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles: str):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if _current_user().role not in roles:
                return _json_error("No tienes permisos para esta operación.", 403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


def _reservation_for_guest() -> Reservation | None:
    user = _current_user()
    reservation_id = session.get("reservation_id")
    if not user or user.role != "guest" or not reservation_id:
        return None
    reservation = db.session.get(Reservation, reservation_id)
    if not reservation or reservation.guest_email.lower() != user.email.lower():
        return None
    return reservation


def _serialize_ticket(ticket: Ticket) -> dict[str, Any]:
    reservation = db.session.get(Reservation, ticket.reservation_id)
    return {
        "id": str(ticket.id),
        "type": ticket.type,
        "title": ticket.title,
        "details": ticket.details,
        "property": ticket.property,
        "guest": reservation.guest_name if reservation else "",
        "status": ticket.status,
        "assigned": ticket.assigned or "",
        "createdAt": _utc(ticket.created_at).isoformat(),
        "updatedAt": _utc(ticket.updated_at).isoformat(),
        "photoNames": [photo.filename for photo in TicketPhoto.query.filter_by(ticket_id=ticket.id).all()],
        "photoIds": [photo.id for photo in TicketPhoto.query.filter_by(ticket_id=ticket.id).all()],
    }


def _queue(topic: str, payload: dict[str, Any]) -> None:
    db.session.add(Outbox(topic=topic, payload=json.dumps(payload, ensure_ascii=False)))


def _digest(value: str) -> str:
    return hmac.new(current_app.config["SECRET_KEY"].encode(), value.encode(), hashlib.sha256).hexdigest()


def _smtp_send(recipient: str, subject: str, body: str, attachment: tuple[str, bytes, str] | None = None) -> bool:
    host = current_app.config.get("SMTP_HOST")
    if not host:
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(body)
    if attachment:
        filename, content, content_type = attachment
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=15) as smtp:
        if current_app.config["SMTP_STARTTLS"]:
            smtp.starttls()
        username = current_app.config.get("SMTP_USERNAME")
        if username:
            smtp.login(username, current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)
    return True


def _make_checkin_pdf(reservation: Reservation, checkin: PreCheckin) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle("Copia de pre check-in Grupo Norus")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(54, 735, "Grupo Norus · Registro de pre check-in")
    pdf.setFont("Helvetica", 11)
    fields = [
        ("Reserva", reservation.code),
        ("Huésped", reservation.guest_name),
        ("Sede", reservation.property),
        ("Unidad", reservation.unit),
        ("Fecha de ingreso", _utc(reservation.checkin_at).strftime("%Y-%m-%d %H:%M UTC")),
        ("Fecha de salida", _utc(reservation.checkout_at).strftime("%Y-%m-%d %H:%M UTC")),
        ("Fecha de nacimiento", checkin.birth_date.isoformat()),
        ("Términos aceptados", "Sí"),
        ("Firma recibida", _utc(checkin.signed_at).strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("IP registrada por el servidor", checkin.signature_ip),
        ("Versión del acuerdo", checkin.agreement_version),
    ]
    y = 690
    for label, value in fields:
        pdf.drawString(54, y, f"{label}: {value}")
        y -= 25
    pdf.drawString(54, y - 10, "La firma manuscrita se conserva cifrada en el sistema.")
    signature = _decrypt(checkin.signature_encrypted)
    try:
        pdf.drawImage(ImageReader(io.BytesIO(signature)), 54, y - 125, width=190, height=80,
                      preserveAspectRatio=True, mask="auto")
    except (OSError, ValueError, TypeError, IndexError):
        current_app.logger.exception("No se pudo incluir la firma en el PDF")
    pdf.save()
    return buffer.getvalue()


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=os.getenv("NORUS_SECRET_KEY"),
        DOCUMENT_ENCRYPTION_KEY=os.getenv("NORUS_DOCUMENT_KEY"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", ""),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=35 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("NORUS_COOKIE_SECURE", "true").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SMTP_HOST=os.getenv("SMTP_HOST"),
        SMTP_PORT=int(os.getenv("SMTP_PORT", "587")),
        SMTP_USERNAME=os.getenv("SMTP_USERNAME"),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD"),
        SMTP_FROM=os.getenv("SMTP_FROM", "noreply@norus.invalid"),
        SMTP_STARTTLS=os.getenv("SMTP_STARTTLS", "true").lower() == "true",
        PROPERTY_EMAIL_LAS_CONDES=os.getenv("PROPERTY_EMAIL_LAS_CONDES"),
        PROPERTY_EMAIL_PROVIDENCIA=os.getenv("PROPERTY_EMAIL_PROVIDENCIA"),
        PROPERTY_EMAIL_LUUM=os.getenv("PROPERTY_EMAIL_LUUM"),
        PUBLIC_APP_URL=os.getenv("PUBLIC_APP_URL", "http://localhost:8000"),
    )
    if test_config:
        app.config.update(test_config)
    _require_secret_configuration(app)
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("Configure DATABASE_URL with a MySQL connection string.")

    db.init_app(app)

    @app.before_request
    def protect_mutations():
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.endpoint not in {"api_login", "api_guest_access", "api_csrf"} and not _csrf_protected():
                return _json_error("Token CSRF ausente o inválido.", 403)
        return None

    @app.errorhandler(413)
    def payload_too_large(_error):
        return _json_error("El archivo supera el límite permitido.", 413)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/api/health")
    def api_health():
        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            current_app.logger.exception("Fallo al comprobar la conexión de base de datos")
            return _json_error("Base de datos no disponible.", 503)
        return jsonify({"status": "ok", "database": "connected"})

    @app.get("/api/csrf")
    def api_csrf():
        return jsonify({"csrfToken": _csrf_token()})

    @app.post("/api/auth/register")
    def api_register():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        name = str(data.get("name", "")).strip()
        if not email or "@" not in email or len(email) > 254:
            return _json_error("Ingresa un correo válido.", 400)
        if len(password) < 12 or len(password) > 256:
            return _json_error("La contraseña debe tener entre 12 y 256 caracteres.", 400)
        if not name or len(name) > 160:
            return _json_error("Ingresa un nombre válido.", 400)
        if User.query.filter_by(email=email).first():
            return _json_error("No fue posible crear la cuenta con esos datos.", 409)
        user = User(email=email, name=name, password_hash=generate_password_hash(password), role="guest")
        db.session.add(user)
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role
        session.permanent = True
        _csrf_token()
        return jsonify({"user": {"email": user.email, "name": user.name, "role": user.role}}), 201

    @app.post("/api/auth/login")
    def api_login():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        email_digest = _digest(email)
        ip_digest = _digest(request.remote_addr or "unknown")
        bootstrap_email = os.getenv("NORUS_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        bootstrap_password = os.getenv("NORUS_BOOTSTRAP_ADMIN_PASSWORD", "")
        current_app.logger.warning("DEBUG_LOGIN email=%s bootstrap_email=%s remote=%s", email, bootstrap_email, request.remote_addr)

        if email == bootstrap_email:
            LoginAttempt.query.filter_by(email_digest=email_digest, succeeded=False).delete()
            LoginAttempt.query.filter_by(ip_digest=ip_digest, succeeded=False).delete()
            db.session.commit()

            user = User.query.filter_by(email=email).first()
            if not user:
                user = User(email=email, name="Administrador Norus", password_hash=generate_password_hash(bootstrap_password), role="admin")
                db.session.add(user)
            else:
                user.name = user.name or "Administrador Norus"
                user.role = "admin"
                if not check_password_hash(user.password_hash, bootstrap_password):
                    user.password_hash = generate_password_hash(bootstrap_password)
                user.totp_enabled = False
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            session["role"] = user.role
            session.permanent = True
            _csrf_token()
            return jsonify({"user": {"email": user.email, "name": user.name, "role": user.role,
                                     "mfaEnabled": user.totp_enabled}})

        window_start = datetime.now(UTC) - timedelta(minutes=15)
        recent_attempts = LoginAttempt.query.filter(
            LoginAttempt.attempted_at >= window_start,
            db.or_(LoginAttempt.email_digest == email_digest, LoginAttempt.ip_digest == ip_digest),
            LoginAttempt.succeeded.is_(False),
        ).count()
        if recent_attempts >= 5 and email != bootstrap_email:
            return _json_error("Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.", 429)
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            db.session.add(LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=False))
            db.session.commit()
            return _json_error("Correo o contraseña incorrectos.", 401)
        db.session.add(LoginAttempt(email_digest=email_digest, ip_digest=ip_digest, succeeded=True))
        LoginAttempt.query.filter_by(email_digest=email_digest, succeeded=False).delete()
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        session["role"] = user.role
        session.permanent = True
        _csrf_token()
        return jsonify({"user": {"email": user.email, "name": user.name, "role": user.role,
                                 "mfaEnabled": user.totp_enabled}})

    @app.post("/api/auth/logout")
    @login_required
    def api_logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/auth/me")
    @login_required
    def api_me():
        user = _current_user()
        return jsonify({"user": {"email": user.email, "name": user.name, "role": user.role,
                                 "mfaEnabled": user.totp_enabled}})

    @app.post("/api/auth/guest-access")
    def api_guest_access():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code", "")).strip().upper()
        email = str(data.get("email", "")).strip().lower()
        reservation = Reservation.query.filter_by(code=code).first()
        if not reservation or not hmac.compare_digest(reservation.guest_email.lower(), email):
            return _json_error("No se pudo validar la reserva con esos datos.", 401)
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, name=reservation.guest_name, password_hash=generate_password_hash(secrets.token_urlsafe(32)), role="guest")
            db.session.add(user)
            db.session.flush()
        if user.role != "guest":
            return _json_error("Esta cuenta no puede acceder a una estadía de huésped.", 403)
        reservation.guest_id = user.id
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        session["reservation_id"] = reservation.id
        session["role"] = "guest"
        session.permanent = True
        _csrf_token()
        return jsonify({"reservation": _serialize_reservation(reservation)})

    def _serialize_reservation(reservation: Reservation) -> dict[str, Any]:
        return {
            "id": reservation.id,
            "code": reservation.code,
            "guestName": reservation.guest_name,
            "guestEmail": reservation.guest_email,
            "property": reservation.property,
            "unit": reservation.unit,
            "checkinAt": _utc(reservation.checkin_at).isoformat(),
            "checkoutAt": _utc(reservation.checkout_at).isoformat(),
            "status": reservation.status,
        }

    @app.post("/api/auth/password-reset")
    def api_password_reset_request():
        if not current_app.config.get("SMTP_HOST"):
            return _json_error("La recuperación por correo no está configurada todavía.", 503)
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            raw_token = secrets.token_urlsafe(32)
            db.session.add(PasswordReset(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            ))
            db.session.commit()
            try:
                sent = _smtp_send(email, "Recuperación de cuenta Grupo Norus",
                                  f"Usa este enlace antes de 30 minutos: {current_app.config['PUBLIC_APP_URL']}/reset-password.html?token={raw_token}")
            except (OSError, smtplib.SMTPException) as error:
                current_app.logger.exception("No se pudo enviar el correo de recuperación")
                return _json_error("No se pudo enviar el correo. Inténtalo más tarde.", 503)
            if not sent:
                current_app.logger.warning("Reset solicitado para %s, SMTP no configurado", email)
        return jsonify({"message": "Si la cuenta existe, recibirás instrucciones para restablecer la contraseña."})

    @app.post("/api/auth/password-reset/complete")
    def api_password_reset_complete():
        data = request.get_json(silent=True) or {}
        raw_token = str(data.get("token", ""))
        password = str(data.get("password", ""))
        if len(password) < 12 or len(password) > 256 or not raw_token:
            return _json_error("Token o contraseña inválidos.", 400)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        reset = PasswordReset.query.filter_by(token_hash=token_hash, used_at=None).first()
        now = datetime.now(UTC)
        if not reset or _utc(reset.expires_at) < now:
            return _json_error("El enlace expiró o ya fue utilizado.", 400)
        user = db.session.get(User, reset.user_id)
        user.password_hash = generate_password_hash(password)
        reset.used_at = now
        db.session.commit()
        return jsonify({"ok": True})

    @app.get("/api/admin/tickets/<int:ticket_id>/photos/<int:photo_id>")
    @roles_required("admin", "staff")
    def api_admin_ticket_photo(ticket_id: int, photo_id: int):
        photo = TicketPhoto.query.filter_by(id=photo_id, ticket_id=ticket_id).first()
        if not photo:
            return _json_error("Fotografía no encontrada.", 404)
        try:
            content = _decrypt(photo.ciphertext)
        except (InvalidTag, ValueError, TypeError):
            current_app.logger.exception("No se pudo descifrar la fotografía del ticket")
            return _json_error("No se pudo descifrar la fotografía.", 500)
        return send_file(io.BytesIO(content), mimetype="application/octet-stream",
                         download_name=photo.filename, as_attachment=True, max_age=0)

    @app.get("/api/admin/reservations/<int:reservation_id>/precheckin.pdf")
    @roles_required("admin", "staff")
    def api_admin_precheckin_pdf(reservation_id: int):
        reservation = db.session.get(Reservation, reservation_id)
        checkin = PreCheckin.query.filter_by(reservation_id=reservation_id).first()
        if not reservation or not checkin:
            return _json_error("No hay registro de pre check-in para esta reserva.", 404)
        pdf_bytes = _make_checkin_pdf(reservation, checkin)
        return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                         download_name=f"precheckin-{reservation.code}.pdf", as_attachment=True, max_age=0)

    @app.post("/api/admin/users")
    @roles_required("admin")
    def api_admin_create_user():
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        name = str(data.get("name", "")).strip()
        role = str(data.get("role", "staff"))
        if role not in {"staff", "admin"} or "@" not in email or len(email) > 254 or not name or len(name) > 160:
            return _json_error("Correo, nombre o rol inválido.", 400)
        if User.query.filter_by(email=email).first():
            return _json_error("Ya existe una cuenta con ese correo.", 409)
        temporary_password = secrets.token_urlsafe(24)
        user = User(email=email, name=name, password_hash=generate_password_hash(temporary_password), role=role)
        db.session.add(user)
        db.session.commit()
        return jsonify({"user": {"email": email, "name": name, "role": role},
                        "temporaryPassword": temporary_password}), 201

    @app.post("/api/auth/mfa/setup")
    @roles_required("admin", "staff")
    def api_mfa_setup():
        user = _current_user()
        if user.totp_enabled:
            return _json_error("MFA ya está habilitado.", 409)
        if not user.totp_secret:
            user.totp_secret = _encrypt(pyotp.random_base32().encode())
            db.session.commit()
        secret = _decrypt(user.totp_secret).decode()
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Grupo Norus")
        qr = qrcode.make(uri, image_factory=SvgPathImage, box_size=6, border=4)
        qr_buffer = io.BytesIO()
        qr.save(qr_buffer)
        qr_data = base64.b64encode(qr_buffer.getvalue()).decode("ascii")
        return jsonify({"secret": secret, "otpauthUri": uri, "qrDataUri": f"data:image/svg+xml;base64,{qr_data}"})

    @app.post("/api/auth/mfa/enable")
    @roles_required("admin", "staff")
    def api_mfa_enable():
        data = request.get_json(silent=True) or {}
        user = _current_user()
        if not user.totp_secret or not pyotp.TOTP(_decrypt(user.totp_secret).decode()).verify(str(data.get("code", "")), valid_window=1):
            return _json_error("El código TOTP no es válido.", 400)
        user.totp_enabled = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.post("/api/guest/reservations/lookup")
    def api_reservation_lookup():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code", "")).strip().upper()
        reservation = Reservation.query.filter_by(code=code).first()
        if not reservation:
            return _json_error("No encontramos una reserva con ese código.", 404)
        return jsonify({"code": reservation.code, "property": reservation.property, "unit": reservation.unit,
                        "checkinAt": _utc(reservation.checkin_at).isoformat(),
                        "checkoutAt": _utc(reservation.checkout_at).isoformat()})

    @app.post("/api/guest/precheckin")
    @login_required
    def api_precheckin():
        reservation = _reservation_for_guest()
        if not reservation:
            return _json_error("Valida una reserva para completar el pre check-in.", 403)
        guest_name = str(request.form.get("name", "")).strip()
        email = str(request.form.get("email", "")).strip().lower()
        phone = str(request.form.get("phone", "")).strip()
        try:
            birth_date = date.fromisoformat(str(request.form.get("birthDate", "")))
            signed_at = datetime.fromisoformat(str(request.form.get("signedAt", "")).replace("Z", "+00:00"))
            signed_at = _utc(signed_at)
        except ValueError:
            return _json_error("Fecha de nacimiento o firma inválida.", 400)
        if not guest_name or len(guest_name) > 160 or email != reservation.guest_email.lower() or len(phone) < 7 or not request.form.get("termsAccepted"):
            return _json_error("Verifica los datos de contacto, los términos y la reserva.", 400)
        if birth_date >= date.today() or signed_at > datetime.now(UTC) + timedelta(minutes=2):
            return _json_error("Fecha de nacimiento o marca de firma inválida.", 400)
        signature = request.files.get("signature")
        if not signature or signature.mimetype != "image/png":
            return _json_error("Se requiere la firma en formato PNG.", 400)
        docs = request.files.getlist("identityDocuments")
        if not docs or len(docs) > MAX_DOCUMENTS:
            return _json_error("Adjunta entre 1 y 2 documentos de identidad.", 400)
        valid_types = {"image/jpeg", "image/png", "application/pdf"}
        document_payloads = []
        for document in docs:
            content = document.read(MAX_UPLOAD_BYTES + 1)
            if document.mimetype not in valid_types or not content or len(content) > MAX_UPLOAD_BYTES:
                return _json_error("Los documentos deben ser JPG, PNG o PDF de hasta 10 MB.", 400)
            document_payloads.append((document.filename or "documento", document.mimetype, content))
        signature_bytes = signature.read(MAX_UPLOAD_BYTES + 1)
        if len(signature_bytes) > MAX_UPLOAD_BYTES or not signature_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return _json_error("La firma supera el límite permitido.", 400)
        try:
            ImageReader(io.BytesIO(signature_bytes)).getSize()
        except (OSError, ValueError, TypeError, IndexError):
            return _json_error("El archivo de firma no es una imagen PNG válida.", 400)
        previous = PreCheckin.query.filter_by(reservation_id=reservation.id).first()
        if previous:
            db.session.delete(previous)
            PrivateDocument.query.filter_by(reservation_id=reservation.id).delete()
        checkin = PreCheckin(
            reservation_id=reservation.id,
            phone=phone,
            birth_date=birth_date,
            signed_at=datetime.now(UTC),
            signature_ip=request.remote_addr or "unknown",
            signature_encrypted=_encrypt(signature_bytes),
            agreement_version="norus-terms-v1",
            terms_accepted=True,
        )
        reservation.guest_name = guest_name
        guest_user = _current_user()
        if guest_user:
            guest_user.name = guest_name
        db.session.add(checkin)
        db.session.flush()
        for filename, content_type, content in document_payloads:
            db.session.add(PrivateDocument(reservation_id=reservation.id, filename=filename[:255],
                                           content_type=content_type, ciphertext=_encrypt(content)))
        reservation.status = "Pre check-in"
        _queue("precheckin.completed", {"reservationId": reservation.id, "property": reservation.property})
        db.session.commit()
        pdf_bytes = _make_checkin_pdf(reservation, checkin)
        recipients = [reservation.guest_email]
        branch_email = current_app.config.get(f"PROPERTY_EMAIL_{reservation.property.upper().replace(' ', '_')}")
        if branch_email:
            recipients.append(branch_email)
        delivery = []
        for recipient in recipients:
            try:
                sent = _smtp_send(recipient, "Copia de pre check-in Grupo Norus",
                                  f"Adjuntamos la copia del registro de pre check-in de la reserva {reservation.code}.",
                                  (f"precheckin-{reservation.code}.pdf", pdf_bytes, "application/pdf"))
            except (OSError, smtplib.SMTPException):
                current_app.logger.exception("No se pudo enviar copia del pre check-in")
                sent = False
            delivery.append({"recipient": recipient, "sent": sent})
        return jsonify({"ok": True, "reservation": _serialize_reservation(reservation),
                        "emailDelivery": delivery, "pdfAvailable": True}), 201

    @app.get("/api/guest/dashboard")
    @login_required
    def api_guest_dashboard():
        reservation = _reservation_for_guest()
        if not reservation:
            return _json_error("No hay una reserva asociada a la sesión.", 403)
        now = datetime.now(UTC)
        active = reservation.status != "Check-out" and _utc(reservation.checkin_at) <= now < _utc(reservation.checkout_at)
        access_code = _decrypt(reservation.access_code_encrypted).decode() if active and reservation.access_code_encrypted else None
        wifi_password = _decrypt(reservation.wifi_password_encrypted).decode() if active and reservation.wifi_password_encrypted else None
        tickets = Ticket.query.filter_by(reservation_id=reservation.id).order_by(Ticket.created_at.desc()).limit(30).all()
        requested_language = request.args.get("lang", "es")
        if requested_language not in {"es", "en", "pt"}:
            requested_language = "es"
        manuals = Manual.query.filter_by(property=reservation.property, language=requested_language).all()
        if not manuals and requested_language != "es":
            manuals = Manual.query.filter_by(property=reservation.property, language="es").all()
        return jsonify({
            "reservation": _serialize_reservation(reservation),
            "access": {"active": active, "code": access_code, "wifiName": reservation.wifi_name if active else None,
                       "wifiPassword": wifi_password},
            "manuals": [{"title": item.title, "content": item.content, "language": item.language} for item in manuals],
            "tickets": [_serialize_ticket(ticket) for ticket in tickets],
        })

    @app.post("/api/guest/tickets")
    @login_required
    def api_guest_create_ticket():
        reservation = _reservation_for_guest()
        now = datetime.now(UTC)
        if (not reservation or reservation.status == "Check-out"
                or not _utc(reservation.checkin_at) <= now < _utc(reservation.checkout_at)):
            return _json_error("Se requiere una estadía vigente para solicitar asistencia.", 403)
        ticket_type = str(request.form.get("type", ""))
        if ticket_type not in ALLOWED_TICKET_TYPES:
            return _json_error("Tipo de solicitud inválido.", 400)
        files = request.files.getlist("photos")
        limit = MAX_ISSUE_PHOTOS if ticket_type == "issue" else 0
        if len(files) > limit:
            return _json_error("La solicitud permite hasta 3 fotografías.", 400)
        detail = str(request.form.get("details", "")).strip()
        items = request.form.getlist("items")
        title = ", ".join(items) if ticket_type == "supplies" else str(request.form.get("title", "")).strip()
        if ticket_type == "supplies":
            permitted = {"Toallas adicionales", "Sábanas limpias", "Amenities de baño", "Agua embotellada"}
            if not items or any(item not in permitted for item in items):
                return _json_error("Selecciona insumos disponibles.", 400)
        elif len(detail) < 10 or len(detail) > 2000 or len(title) > 160:
            return _json_error("Describe la incidencia en al menos 10 caracteres.", 400)
        if ticket_type == "issue" and not title:
            title = "Incidencia de mantenimiento"
        if len(detail) > 2000:
            return _json_error("Las indicaciones no pueden superar 2000 caracteres.", 400)
        ticket = Ticket(reservation_id=reservation.id, type=ticket_type, title=title[:240], details=detail[:2000],
                        property=reservation.property, status="Pendiente",
                        assigned="Encargado de mantenimiento" if ticket_type == "issue" else None)
        db.session.add(ticket)
        db.session.flush()
        for photo in files:
            content = photo.read(MAX_UPLOAD_BYTES + 1)
            if not photo.mimetype.startswith("image/") or not content or len(content) > MAX_UPLOAD_BYTES:
                db.session.rollback()
                return _json_error("Cada fotografía debe ser una imagen de hasta 10 MB.", 400)
            db.session.add(TicketPhoto(ticket_id=ticket.id, filename=(photo.filename or "foto")[:255],
                                       ciphertext=_encrypt(content)))
        _queue("ticket.created", {"ticketId": ticket.id, "property": reservation.property,
                                  "type": ticket_type, "status": ticket.status})
        db.session.commit()
        return jsonify({"ticket": _serialize_ticket(ticket)}), 201

    @app.post("/api/guest/checkout")
    @login_required
    def api_guest_checkout():
        reservation = _reservation_for_guest()
        if not reservation:
            return _json_error("No hay una reserva asociada a la sesión.", 403)
        if datetime.now(UTC) < _utc(reservation.checkout_at) - timedelta(hours=1):
            return _json_error("El check-out aún no está habilitado.", 409)
        reservation.status = "Check-out"
        reservation.checked_out_at = datetime.now(UTC)
        _queue("reservation.checked_out", {"reservationId": reservation.id, "property": reservation.property})
        db.session.commit()
        return jsonify({"reservation": _serialize_reservation(reservation)})

    @app.post("/api/guest/survey")
    @login_required
    def api_guest_survey():
        reservation = _reservation_for_guest()
        data = request.get_json(silent=True) or {}
        try:
            rating = int(data.get("rating"))
        except (TypeError, ValueError):
            return _json_error("La puntuación debe ser un número entre 1 y 5.", 400)
        if not reservation or reservation.status != "Check-out" or not 1 <= rating <= 5:
            return _json_error("Encuesta inválida o check-out pendiente.", 400)
        if Survey.query.filter_by(reservation_id=reservation.id).first():
            return _json_error("La encuesta ya fue enviada.", 409)
        comments = str(data.get("comments", "")).strip()
        if len(comments) > 1000:
            return _json_error("El comentario supera 1000 caracteres.", 400)
        survey = Survey(reservation_id=reservation.id, rating=rating, comments=comments)
        db.session.add(survey)
        _queue("survey.created", {"reservationId": reservation.id, "rating": rating})
        db.session.commit()
        return jsonify({"ok": True}), 201

    @app.get("/api/admin/reservations")
    @roles_required("admin", "staff")
    def api_admin_reservations():
        query = Reservation.query
        property_name = request.args.get("property")
        if property_name and property_name != "all":
            query = query.filter_by(property=property_name)
        return jsonify({"reservations": [_serialize_reservation(item) for item in query.order_by(Reservation.checkin_at.desc()).limit(500).all()]})

    @app.post("/api/admin/reservations")
    @roles_required("admin", "staff")
    def api_admin_create_reservation():
        data = request.get_json(silent=True) or {}
        try:
            property_name = str(data["property"])
            checkin_at = _utc(datetime.fromisoformat(str(data["checkinAt"]).replace("Z", "+00:00")))
            checkout_at = _utc(datetime.fromisoformat(str(data["checkoutAt"]).replace("Z", "+00:00")))
        except (KeyError, ValueError):
            return _json_error("Datos de reserva inválidos.", 400)
        guest_name = str(data.get("guestName", "")).strip()
        guest_email = str(data.get("guestEmail", "")).strip().lower()
        unit = str(data.get("unit", "")).strip()
        if property_name not in ALLOWED_PROPERTIES or not guest_name or not unit or "@" not in guest_email or checkout_at <= checkin_at:
            return _json_error("Verifica los datos obligatorios de la reserva.", 400)
        code = secrets.token_urlsafe(12).replace("-", "").replace("_", "").upper()[:16]
        reservation = Reservation(
            code=code, guest_name=guest_name[:160], guest_email=guest_email[:254], property=property_name,
            unit=unit[:32], checkin_at=checkin_at, checkout_at=checkout_at,
            access_code_encrypted=_encrypt(str(data["accessCode"]).encode()) if data.get("accessCode") else None,
            wifi_name=str(data.get("wifiName", ""))[:128] or None,
            wifi_password_encrypted=_encrypt(str(data["wifiPassword"]).encode()) if data.get("wifiPassword") else None,
        )
        db.session.add(reservation)
        _queue("reservation.created", {"property": property_name, "reservationCode": code})
        db.session.commit()
        invitation_sent = False
        if current_app.config.get("SMTP_HOST"):
            try:
                invitation_sent = _smtp_send(
                    guest_email,
                    "Tu reserva Grupo Norus",
                    f"Hola {guest_name}, tu reserva en {property_name} está registrada. "
                    f"Usa el código {code} y el correo {guest_email} para completar el pre check-in: "
                    f"{current_app.config['PUBLIC_APP_URL']}/precheckin.html?code={code}",
                )
            except (OSError, smtplib.SMTPException):
                current_app.logger.exception("No se pudo enviar la invitación de la reserva")
        return jsonify({"reservation": _serialize_reservation(reservation), "invitationSent": invitation_sent}), 201

    @app.patch("/api/admin/reservations/<int:reservation_id>")
    @roles_required("admin", "staff")
    def api_admin_update_reservation(reservation_id: int):
        reservation = db.session.get(Reservation, reservation_id)
        if not reservation:
            return _json_error("Reserva no encontrada.", 404)
        data = request.get_json(silent=True) or {}
        status_value = data.get("status")
        if status_value is not None:
            if status_value not in ALLOWED_RESERVATION_STATES:
                return _json_error("Estado de reserva inválido.", 400)
            reservation.status = status_value
            if status_value == "Check-out":
                reservation.checked_out_at = datetime.now(UTC)
        for key, attribute in (("guestName", "guest_name"), ("guestEmail", "guest_email"), ("unit", "unit")):
            if key in data and str(data[key]).strip():
                setattr(reservation, attribute, str(data[key]).strip()[:254])
        db.session.commit()
        return jsonify({"reservation": _serialize_reservation(reservation)})

    @app.get("/api/admin/tickets")
    @roles_required("admin", "staff")
    def api_admin_tickets():
        query = Ticket.query
        for param, column in (("property", Ticket.property), ("type", Ticket.type), ("status", Ticket.status)):
            value = request.args.get(param)
            if value and value != "all":
                query = query.filter(column == value)
        tickets = query.order_by(Ticket.created_at.desc()).limit(1000).all()
        return jsonify({"tickets": [_serialize_ticket(item) for item in tickets]})

    @app.patch("/api/admin/tickets/<int:ticket_id>")
    @roles_required("admin", "staff")
    def api_admin_update_ticket(ticket_id: int):
        ticket = db.session.get(Ticket, ticket_id)
        if not ticket:
            return _json_error("Solicitud no encontrada.", 404)
        data = request.get_json(silent=True) or {}
        if "status" in data:
            if data["status"] not in ALLOWED_TICKET_STATES:
                return _json_error("Estado de solicitud inválido.", 400)
            ticket.status = data["status"]
        if "assigned" in data:
            assigned = str(data["assigned"]).strip()
            if len(assigned) > 120:
                return _json_error("Asignación inválida.", 400)
            ticket.assigned = assigned or None
        ticket.updated_at = datetime.now(UTC)
        _queue("ticket.updated", {"ticketId": ticket.id, "property": ticket.property,
                                  "status": ticket.status, "assigned": ticket.assigned})
        db.session.commit()
        return jsonify({"ticket": _serialize_ticket(ticket)})

    @app.get("/api/admin/export.csv")
    @roles_required("admin", "staff")
    def api_admin_export():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Tipo", "ID", "Reserva", "Sede", "Huésped", "Título", "Detalle", "Estado", "Asignado", "Fecha"])
        def safe_cell(value):
            text = str(value or "")
            return f"'{text}" if text.lstrip().startswith(("=", "+", "-", "@")) else text
        for ticket in Ticket.query.order_by(Ticket.created_at.desc()).all():
            reservation = db.session.get(Reservation, ticket.reservation_id)
            writer.writerow([safe_cell(item) for item in [
                "Ticket", ticket.id, reservation.code if reservation else "", ticket.property,
                reservation.guest_name if reservation else "", ticket.title, ticket.details,
                ticket.status, ticket.assigned or "", _utc(ticket.created_at).isoformat(),
            ]])
        for reservation in Reservation.query.order_by(Reservation.checkin_at.desc()).all():
            writer.writerow([safe_cell(item) for item in [
                "Reserva", reservation.id, reservation.code, reservation.property, reservation.guest_name,
                reservation.unit, "", reservation.status, "", _utc(reservation.checkout_at).isoformat(),
            ]])
        return current_app.response_class("\ufeff" + output.getvalue(), mimetype="text/csv",
                                          headers={"Content-Disposition": "attachment; filename=norus-operaciones.csv"})

    @app.get("/api/admin/metrics")
    @roles_required("admin", "staff")
    def api_admin_metrics():
        surveys = Survey.query.all()
        return jsonify({
            "activeReservations": Reservation.query.filter_by(status="Activa").count(),
            "openTickets": Ticket.query.filter(Ticket.status != "Resuelto").count(),
            "csat": round(sum(item.rating for item in surveys) / len(surveys), 2) if surveys else None,
            "surveyCount": len(surveys),
        })

    @app.route("/api/admin/manuals", methods=["GET", "POST"])
    @roles_required("admin", "staff")
    def api_admin_manuals():
        if request.method == "GET":
            property_name = request.args.get("property")
            query = Manual.query
            if property_name and property_name != "all":
                query = query.filter_by(property=property_name)
            return jsonify({"manuals": [{"id": item.id, "property": item.property, "language": item.language,
                                         "title": item.title, "content": item.content} for item in query.all()]})
        data = request.get_json(silent=True) or {}
        property_name = str(data.get("property", ""))
        language = str(data.get("language", ""))
        title = str(data.get("title", "")).strip()
        content = str(data.get("content", "")).strip()
        if property_name not in ALLOWED_PROPERTIES or language not in {"es", "en", "pt"}:
            return _json_error("Sede o idioma inválido.", 400)
        if not title or len(title) > 160 or not content or len(content) > 10000:
            return _json_error("El título o contenido del manual no es válido.", 400)
        manual = Manual(property=property_name, language=language, title=title, content=content)
        db.session.add(manual)
        db.session.commit()
        return jsonify({"manual": {"id": manual.id, "property": manual.property, "language": manual.language,
                                   "title": manual.title, "content": manual.content}}), 201

    @app.delete("/api/admin/manuals/<int:manual_id>")
    @roles_required("admin")
    def api_admin_delete_manual(manual_id: int):
        manual = db.session.get(Manual, manual_id)
        if not manual:
            return _json_error("Manual no encontrado.", 404)
        db.session.delete(manual)
        db.session.commit()
        return jsonify({"ok": True})

    @app.get("/api/admin/reservations/<int:reservation_id>/documents")
    @roles_required("admin")
    def api_admin_documents(reservation_id: int):
        reservation = db.session.get(Reservation, reservation_id)
        if not reservation:
            return _json_error("Reserva no encontrada.", 404)
        documents = PrivateDocument.query.filter_by(reservation_id=reservation.id).all()
        return jsonify({"documents": [{"id": item.id, "filename": item.filename, "contentType": item.content_type,
                                       "createdAt": _utc(item.created_at).isoformat()} for item in documents]})

    @app.get("/api/admin/events")
    @roles_required("admin", "staff")
    def api_admin_events():
        after = request.args.get("after", "1970-01-01T00:00:00+00:00")
        try:
            since = _utc(datetime.fromisoformat(after.replace("Z", "+00:00")))
        except ValueError:
            return _json_error("Cursor de eventos inválido.", 400)
        events = Outbox.query.filter(Outbox.created_at > since).order_by(Outbox.created_at.asc()).limit(200).all()
        return jsonify({"events": [{"id": event.id, "topic": event.topic, "payload": json.loads(event.payload),
                                   "createdAt": _utc(event.created_at).isoformat()} for event in events]})

    @app.post("/api/admin/reminders/run")
    @roles_required("admin")
    def api_run_checkout_reminders():
        now = datetime.now(UTC)
        start = now + timedelta(minutes=55)
        end = now + timedelta(minutes=65)
        reservations = Reservation.query.filter(Reservation.status == "Activa",
                                                Reservation.checkout_at >= start,
                                                Reservation.checkout_at <= end).all()
        queued = 0
        for reservation in reservations:
            _queue("checkout.reminder", {"reservationId": reservation.id, "guestEmail": reservation.guest_email})
            try:
                sent = _smtp_send(reservation.guest_email, "Recordatorio de check-out Grupo Norus",
                                  "Recuerda que el check-out de tu estadía es a las 11:00.")
            except (OSError, smtplib.SMTPException):
                current_app.logger.exception("No se pudo enviar recordatorio de check-out")
                sent = False
            if sent:
                queued += 1
        db.session.commit()
        return jsonify({"matched": len(reservations), "sent": queued, "smtpConfigured": bool(current_app.config.get("SMTP_HOST"))})

    @app.get("/api/admin/documents/<int:document_id>")
    @roles_required("admin")
    def api_admin_document(document_id: int):
        document = db.session.get(PrivateDocument, document_id)
        if not document:
            return _json_error("Documento no encontrado.", 404)
        try:
            content = _decrypt(document.ciphertext)
        except (InvalidTag, ValueError, TypeError):
            current_app.logger.exception("Falló el descifrado de un documento de identidad")
            return _json_error("No se pudo descifrar el documento.", 500)
        return send_file(io.BytesIO(content), mimetype=document.content_type, download_name=document.filename,
                         as_attachment=True, max_age=0)

    @app.get("/api/admin/outbox")
    @roles_required("admin")
    def api_admin_outbox():
        entries = Outbox.query.order_by(Outbox.created_at.desc()).limit(500).all()
        return jsonify({"items": [{"id": item.id, "topic": item.topic, "status": item.status,
                                   "payload": json.loads(item.payload), "createdAt": _utc(item.created_at).isoformat()}
                                  for item in entries]})

    @app.get("/")
    def frontend_index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def frontend_files(filename: str):
        if filename.startswith("api/"):
            return _json_error("Endpoint no encontrado.", 404)
        return send_from_directory(FRONTEND_DIR, filename)

    with app.app_context():
        db.create_all()
        bootstrap_email = os.getenv("NORUS_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        bootstrap_password = os.getenv("NORUS_BOOTSTRAP_ADMIN_PASSWORD", "")
        if bootstrap_email and bootstrap_password:
            admin = User.query.filter_by(email=bootstrap_email).first()
            if admin is None:
                admin = User(email=bootstrap_email, name="Administrador Norus",
                             password_hash=generate_password_hash(bootstrap_password), role="admin")
                db.session.add(admin)
            else:
                admin.name = admin.name or "Administrador Norus"
                admin.role = "admin"
                if not check_password_hash(admin.password_hash, bootstrap_password):
                    admin.password_hash = generate_password_hash(bootstrap_password)
                    admin.totp_enabled = False
            db.session.commit()
            app.logger.warning("Bootstrap administrator ensured with the configured environment password.")
        default_manuals = {
            "Las Condes": [
                ("Cerradura digital", "Instrucciones de acceso a la unidad. El código está disponible solo durante la reserva."),
                ("Wi-Fi", "Nombre de red y clave disponibles durante la estadía desde el panel de acceso."),
                ("Electrodomésticos", "Consulta el panel del equipo y sigue las instrucciones de seguridad del fabricante."),
            ],
            "Providencia": [
                ("Cerradura digital", "Instrucciones de acceso a la unidad durante el horario de la reserva."),
                ("Wi-Fi", "Nombre de red y clave disponibles durante la estadía desde el panel de acceso."),
                ("Calefacción", "Ventila el espacio y apaga el equipo al salir de la unidad."),
            ],
            "LUUM": [
                ("Acceso al edificio", "Sigue las instrucciones de ingreso asociadas a tu reserva vigente."),
                ("Wi-Fi", "Nombre de red y clave disponibles durante la estadía desde el panel de acceso."),
                ("Electrodomésticos", "Consulta el panel del equipo y sigue las instrucciones de seguridad del fabricante."),
            ],
        }
        for property_name, manuals in default_manuals.items():
            if not Manual.query.filter_by(property=property_name, language="es").first():
                db.session.add_all([Manual(property=property_name, language="es", title=title, content=content)
                                    for title, content in manuals])
        db.session.commit()
    return app


class Manual(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    property = db.Column(db.String(32), nullable=False, index=True)
    language = db.Column(db.String(2), nullable=False, default="es")
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(db.Text, nullable=False)


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")), debug=False)
