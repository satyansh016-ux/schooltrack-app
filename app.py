"""
SchoolTrack Pro â€” Production Backend
Multi-school WhatsApp attendance & homework system
With Super Admin Dashboard for managing all client schools
"""
from flask import Flask, request, jsonify, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, date, timedelta
from functools import wraps
import requests, os, calendar

app = Flask(name)
app.secret_key = os.environ.get("SECRET_KEY", "schooltrack_secret_2026_change_me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///schooltrack.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db     = SQLAlchemy(app)
bcrypt = Bcrypt(app)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "schooltrack_webhook_2026")
SUPER_ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
SUPER_ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin@2026")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "")


def send_to_make(event_type, data):
    """Fire a webhook to Make.com only for absent-marking and homework events."""
    if not MAKE_WEBHOOK_URL:
        return
    try:
        payload = {"event": event_type, **data}
        requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        pass

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MODELS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class School(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    city        = db.Column(db.String(80))
    username    = db.Column(db.String(50), unique=True, nullable=False)
    password    = db.Column(db.String(200), nullable=False)
    wa_token    = db.Column(db.String(300), default="")
    wa_phone_id = db.Column(db.String(100), default="")

    # â”€â”€ Admin / billing fields â”€â”€
    plan        = db.Column(db.String(20), default="basic")   # basic / pro
    status      = db.Column(db.String(20), default="trial")   # trial / active / suspended / expired
    monthly_fee = db.Column(db.Integer, default=999)
    expiry_date = db.Column(db.Date, default=lambda: date.today() + timedelta(days=14))
    notes       = db.Column(db.Text, default="")

    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class Class(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    name      = db.Column(db.String(30), nullable=False)
    teacher   = db.Column(db.String(100))


class Student(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    school_id    = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    class_id     = db.Column(db.Integer, db.ForeignKey("class.id"))
    name         = db.Column(db.String(100), nullable=False)
    roll_no      = db.Column(db.String(20))
    parent_name  = db.Column(db.String(100))
    parent_phone = db.Column(db.String(20))
    parent_email = db.Column(db.String(120))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class Attendance(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    school_id  = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    class_id   = db.Column(db.Integer, db.ForeignKey("class.id"))
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))
    status     = db.Column(db.String(10))          # P / A / L
    att_date   = db.Column(db.Date, default=date.today)
    wa_sent    = db.Column(db.Boolean, default=False)
    locked     = db.Column(db.Boolean, default=False)   # locked after end of day
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
