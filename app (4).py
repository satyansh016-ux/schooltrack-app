"""
SchoolTrack Pro — Production Backend
Multi-school WhatsApp attendance & homework system
With Super Admin Dashboard for managing all client schools
"""
from flask import Flask, request, jsonify, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, date, timedelta
from functools import wraps
import requests, os, calendar

app = Flask(__name__)
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

# ── Cost tracker settings (edit these via Render Environment Variables) ──
AISENSY_PLAN_FEE     = float(os.environ.get("AISENSY_PLAN_FEE", 1500))     # ₹/month
AISENSY_UTILITY_RATE = float(os.environ.get("AISENSY_UTILITY_RATE", 0.145)) # ₹/message
MAKE_PLAN_FEE        = float(os.environ.get("MAKE_PLAN_FEE", 0))           # ₹/month (0 = free tier)
MAKE_FREE_OPS        = int(os.environ.get("MAKE_FREE_OPS", 1000))          # ops included in free tier

# ── Principal email alert settings ──
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ABSENT_ALERT_THRESHOLD = int(os.environ.get("ABSENT_ALERT_THRESHOLD", 8))


def send_principal_email(school, student, absent_count):
    """Email the principal when a student crosses the monthly absent threshold."""
    if not (SMTP_USER and SMTP_PASS and school.principal_email):
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText

        subject = f"⚠️ {student.name} — {absent_count} din absent is mahine ({school.name})"
        body = (
            f"Principal ji,\n\n"
            f"Student *{student.name}* (Roll No: {student.roll_no}) is mahine "
            f"{absent_count} school-days absent rahe hain.\n\n"
            f"Student ki details:\n"
            f"  • Naam: {student.name}\n"
            f"  • Roll No: {student.roll_no}\n"
            f"  • Parent ka naam: {student.parent_name}\n"
            f"  • Parent ka phone: {student.parent_phone}\n\n"
            f"Kripya seedha parent se contact karein.\n\n"
            f"— SchoolTrack (Automated Alert)\n{school.name}"
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = school.principal_email

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [school.principal_email], msg.as_string())
        return True
    except Exception:
        return False


def send_to_make(event_type, data, school=None, recipient_count=1):
    """Fire a webhook to Make.com only for absent-marking and homework events,
    and record the result (with recipient count, for cost tracking) in WaLog."""
    if not MAKE_WEBHOOK_URL:
        return
    status = "sent"
    try:
        payload = {"event": event_type, **data}
        r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=8)
        if r.status_code >= 300:
            status = f"failed_{r.status_code}"
    except Exception:
        status = "error"
    if school is not None:
        phone_label = "(broadcast)" if recipient_count <= 1 else f"(broadcast) x{recipient_count}"
        log = WaLog(school_id=school.id, phone=phone_label, msg_type=f"make_{event_type}",
                    message=str(data.get("title") or data.get("student_name") or event_type),
                    status=status)
        db.session.add(log)
        db.session.commit()

# ════════════════════════════════════════════════════════════
# MODELS
# ════════════════════════════════════════════════════════════

class School(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    city        = db.Column(db.String(80))
    username    = db.Column(db.String(50), unique=True, nullable=False)
    password    = db.Column(db.String(200), nullable=False)
    wa_token    = db.Column(db.String(300), default="")
    wa_phone_id = db.Column(db.String(100), default="")
    principal_email = db.Column(db.String(150), default="")

    # ── Admin / billing fields ──
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


class Homework(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    school_id   = db.Column(db.Integer, db.ForeignKey("school.id"), nullable=False)
    class_id    = db.Column(db.Integer, db.ForeignKey("class.id"))
    subject     = db.Column(db.String(100))
    title       = db.Column(db.String(200))
    description = db.Column(db.Text)
    due_date    = db.Column(db.Date)
    wa_sent     = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class WaLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    school_id  = db.Column(db.Integer, nullable=False)
    phone      = db.Column(db.String(20))
    msg_type   = db.Column(db.String(30))
    message    = db.Column(db.Text)
    status     = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════════
# HELPERS — AUTH
# ════════════════════════════════════════════════════════════

def get_school():
    sid = session.get("school_id")
    return School.query.get(sid) if sid else None


def login_required(f):
    @wraps(f)
    def decorated(*a, **kw):
        s = get_school()
        if not s:
            return jsonify({"error": "Login required"}), 401
        if s.status == "suspended":
            return jsonify({"error": "Account suspended. Contact your service provider."}), 403
        return f(*a, **kw)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*a, **kw):
        if not session.get("is_admin"):
            return jsonify({"error": "Admin login required"}), 401
        return f(*a, **kw)
    return decorated


def fmt_phone(phone):
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("0"):
        phone = "91" + phone[1:]
    elif not phone.startswith("91"):
        phone = "91" + phone
    return phone


def send_wa(school, phone, message, msg_type="alert"):
    if not school.wa_token or not school.wa_phone_id:
        log = WaLog(school_id=school.id, phone=phone, msg_type=msg_type,
                    message=message, status="no_token")
        db.session.add(log)
        db.session.commit()
        return "no_token"

    phone = fmt_phone(phone)
    url = f"https://graph.facebook.com/v19.0/{school.wa_phone_id}/messages"
    headers = {"Authorization": f"Bearer {school.wa_token}",
               "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": phone,
               "type": "text", "text": {"body": message}}
    status = "sent"
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code != 200:
            status = f"failed_{r.status_code}"
    except Exception:
        status = "error"

    log = WaLog(school_id=school.id, phone=phone, msg_type=msg_type,
                message=message, status=status)
    db.session.add(log)
    db.session.commit()
    return status


# ════════════════════════════════════════════════════════════
# SCHOOL AUTH ROUTES
# ════════════════════════════════════════════════════════════

@app.route("/api/register", methods=["POST"])
def register():
    d = request.json
    if not d.get("username") or not d.get("password") or not d.get("name"):
        return jsonify({"error": "Name, username, password required"}), 400
    if School.query.filter_by(username=d["username"]).first():
        return jsonify({"error": "Username already exists"}), 400

    hashed = bcrypt.generate_password_hash(d["password"]).decode("utf-8")
    school = School(
        name=d["name"], city=d.get("city", ""),
        username=d["username"], password=hashed,
        plan="basic", status="trial",
        monthly_fee=999,
        expiry_date=date.today() + timedelta(days=14)
    )
    db.session.add(school)
    db.session.commit()
    return jsonify({"msg": "School registered. 14-day free trial started.",
                    "school_id": school.id}), 201


@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    school = School.query.filter_by(username=d.get("username", "")).first()
    if not school or not bcrypt.check_password_hash(school.password, d.get("password", "")):
        return jsonify({"error": "Invalid username or password"}), 401
    if school.status == "suspended":
        return jsonify({"error": "Account suspended. Contact support."}), 403

    session["school_id"]   = school.id
    session["school_name"] = school.name
    return jsonify({"msg": "Login successful", "school_id": school.id,
                    "school_name": school.name, "plan": school.plan,
                    "status": school.status,
                    "days_left": (school.expiry_date - date.today()).days})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"msg": "Logged out"})


@app.route("/api/me", methods=["GET"])
@login_required
def me():
    s = get_school()
    return jsonify({
        "school_id": s.id, "name": s.name, "city": s.city,
        "username": s.username, "plan": s.plan, "status": s.status,
        "days_left": (s.expiry_date - date.today()).days
    })


@app.route("/api/settings", methods=["PUT"])
@login_required
def update_settings():
    s, d = get_school(), request.json
    if d.get("wa_token"):    s.wa_token = d["wa_token"]
    if d.get("wa_phone_id"): s.wa_phone_id = d["wa_phone_id"]
    if d.get("city"):        s.city = d["city"]
    db.session.commit()
    return jsonify({"msg": "Settings updated"})


# ════════════════════════════════════════════════════════════
# CLASSES — school_id isolated
# ════════════════════════════════════════════════════════════

@app.route("/api/classes", methods=["GET"])
@login_required
def list_classes():
    s = get_school()
    cs = Class.query.filter_by(school_id=s.id).all()
    return jsonify([{
        "id": c.id, "name": c.name, "teacher": c.teacher,
        "students": Student.query.filter_by(class_id=c.id, school_id=s.id).count()
    } for c in cs])


@app.route("/api/classes", methods=["POST"])
@login_required
def add_class():
    s, d = get_school(), request.json
    c = Class(school_id=s.id, name=d["name"], teacher=d.get("teacher", ""))
    db.session.add(c)
    db.session.commit()
    return jsonify({"msg": "Class added", "class_id": c.id}), 201


@app.route("/api/classes/<int:cid>", methods=["DELETE"])
@login_required
def delete_class(cid):
    s = get_school()
    c = Class.query.filter_by(id=cid, school_id=s.id).first_or_404()
    db.session.delete(c)
    db.session.commit()
    return jsonify({"msg": "Class deleted"})


# ════════════════════════════════════════════════════════════
# STUDENTS — school_id isolated
# ════════════════════════════════════════════════════════════

@app.route("/api/students", methods=["GET"])
@login_required
def list_students():
    s, class_id = get_school(), request.args.get("class_id")
    q = Student.query.filter_by(school_id=s.id)
    if class_id: q = q.filter_by(class_id=int(class_id))
    students = q.order_by(Student.name).all()
    return jsonify([{
        "id": st.id, "name": st.name, "roll_no": st.roll_no,
        "class_id": st.class_id, "parent_name": st.parent_name,
        "parent_phone": st.parent_phone, "parent_email": st.parent_email
    } for st in students])


@app.route("/api/students", methods=["POST"])
@login_required
def add_student():
    s, d = get_school(), request.json
    if not d.get("name") or not d.get("parent_phone"):
        return jsonify({"error": "Name and parent phone required"}), 400
    st = Student(school_id=s.id, class_id=d["class_id"], name=d["name"],
                roll_no=d.get("roll_no", ""), parent_name=d.get("parent_name", ""),
                parent_phone=d.get("parent_phone", ""), parent_email=d.get("parent_email", ""))
    db.session.add(st)
    db.session.commit()
    return jsonify({"msg": "Student added", "student_id": st.id}), 201


@app.route("/api/students/<int:sid>", methods=["DELETE"])
@login_required
def delete_student(sid):
    s = get_school()
    st = Student.query.filter_by(id=sid, school_id=s.id).first_or_404()
    db.session.delete(st)
    db.session.commit()
    return jsonify({"msg": "Student removed"})


@app.route("/api/students/<int:sid>", methods=["PUT"])
@login_required
def update_student(sid):
    s  = get_school()
    st = Student.query.filter_by(id=sid, school_id=s.id).first_or_404()
    d  = request.json
    for f in ["name","roll_no","parent_name","parent_phone","parent_email","class_id"]:
        if f in d: setattr(st, f, d[f])
    db.session.commit()
    return jsonify({"msg": "Student updated"})


# ════════════════════════════════════════════════════════════
# ATTENDANCE — with lock + monthly % feature
# ════════════════════════════════════════════════════════════

@app.route("/api/attendance", methods=["POST"])
@login_required
def mark_attendance():
    school   = get_school()
    d        = request.json
    att_date = date.fromisoformat(d["date"]) if d.get("date") else date.today()
    class_id = d["class_id"]

    existing_locked = Attendance.query.filter_by(
        school_id=school.id, class_id=class_id, att_date=att_date, locked=True
    ).first()
    if existing_locked:
        return jsonify({"error": "This date is locked and cannot be edited"}), 403

    wa_sent, saved = 0, 0
    for rec in d.get("records", []):
        st = Student.query.filter_by(id=rec["student_id"], school_id=school.id).first()
        if not st: continue

        existing = Attendance.query.filter_by(student_id=st.id, att_date=att_date).first()
        if existing:
            existing.status = rec["status"]
        else:
            db.session.add(Attendance(school_id=school.id, class_id=class_id,
                                      student_id=st.id, status=rec["status"], att_date=att_date))

        if rec["status"] in ("A", "Absent"):
            msg = (f"Dear {st.parent_name},\n\nAapka bachha *{st.name}* (Roll {st.roll_no}) "
                  f"aaj *ABSENT* mark hua hai.\n📅 {att_date.strftime('%d %b %Y')}\n"
                  f"🏫 {school.name}, {school.city}\n\nKoi karan ho to school inform karein. 🙏")
            send_wa(school, st.parent_phone, msg, "absent"); wa_sent += 1
            send_to_make("student_absent", {
                "school": school.name, "student_name": st.name,
                "roll_no": st.roll_no, "parent_name": st.parent_name,
                "parent_phone": st.parent_phone, "date": str(att_date)
            }, school=school, recipient_count=1)

            # Monthly absent threshold check → email principal ONCE per month,
            # using a marker in WaLog so it can never be sent twice even if
            # attendance gets edited/re-marked later in the month.
            db.session.flush()
            month_start = att_date.replace(day=1)
            absent_count = Attendance.query.filter(
                Attendance.student_id == st.id,
                Attendance.status.in_(("A", "Absent")),
                Attendance.att_date >= month_start,
                Attendance.att_date <= att_date
            ).count()
            if absent_count >= ABSENT_ALERT_THRESHOLD:
                marker = f"{st.roll_no}:{month_start.isoformat()}"
                already_sent = WaLog.query.filter_by(
                    school_id=school.id, msg_type="principal_alert_marker", phone=marker
                ).first()
                if not already_sent:
                    send_principal_email(school, st, absent_count)
                    db.session.add(WaLog(
                        school_id=school.id, phone=marker, msg_type="principal_alert_marker",
                        message=f"{st.name} crossed {absent_count} absences this month",
                        status="sent"
                    ))
                    db.session.commit()
        elif rec["status"] in ("L", "Late"):
            msg = (f"Dear {st.parent_name},\n\nAapka bachha *{st.name}* (Roll {st.roll_no}) "
                  f"aaj *LATE* aaya hai.\n📅 {att_date.strftime('%d %b %Y')}\n🏫 {school.name} 🙏")
            send_wa(school, st.parent_phone, msg, "late"); wa_sent += 1
        saved += 1

    db.session.commit()
    return jsonify({"msg": "Attendance saved", "saved": saved,
                    "wa_sent": wa_sent, "date": str(att_date)}), 201


@app.route("/api/attendance/lock", methods=["POST"])
@login_required
def lock_attendance():
    school = get_school()
    d      = request.json
    att_date = date.fromisoformat(d["date"])
    class_id = d["class_id"]
    rows = Attendance.query.filter_by(school_id=school.id, class_id=class_id, att_date=att_date).all()
    for r in rows: r.locked = True
    db.session.commit()
    return jsonify({"msg": "Attendance locked for " + str(att_date), "rows": len(rows)})


@app.route("/api/attendance", methods=["GET"])
@login_required
def get_attendance():
    school   = get_school()
    att_date = request.args.get("date", str(date.today()))
    class_id = request.args.get("class_id")
    d = date.fromisoformat(att_date)

    q = db.session.query(Attendance, Student).join(
        Student, Attendance.student_id == Student.id
    ).filter(Attendance.school_id == school.id, Attendance.att_date == d)
    if class_id: q = q.filter(Attendance.class_id == int(class_id))
    rows = q.all()

    locked = any(a.locked for a, s in rows) if rows else False

    return jsonify({
        "date": str(d), "locked": locked,
        "present": sum(1 for a,s in rows if a.status in ("P","Present")),
        "absent":  sum(1 for a,s in rows if a.status in ("A","Absent")),
        "late":    sum(1 for a,s in rows if a.status in ("L","Late")),
        "records": [{"student_id": s.id, "name": s.name, "roll_no": s.roll_no,
                    "status": a.status, "wa_sent": a.wa_sent} for a,s in rows]
    })


@app.route("/api/attendance/monthly/<int:student_id>", methods=["GET"])
@login_required
def monthly_attendance(student_id):
    school = get_school()
    month  = int(request.args.get("month", date.today().month))
    year   = int(request.args.get("year", date.today().year))

    st = Student.query.filter_by(id=student_id, school_id=school.id).first_or_404()
    first_day = date(year, month, 1)
    last_day  = date(year, month, calendar.monthrange(year, month)[1])

    rows = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.att_date >= first_day,
        Attendance.att_date <= last_day
    ).all()

    total   = len(rows)
    present = sum(1 for r in rows if r.status in ("P","Present"))
    absent  = sum(1 for r in rows if r.status in ("A","Absent"))
    late    = sum(1 for r in rows if r.status in ("L","Late"))
    pct     = round(present/total*100, 1) if total else 0

    return jsonify({
        "student": st.name, "month": month, "year": year,
        "total_days": total, "present": present, "absent": absent,
        "late": late, "attendance_pct": pct
    })


@app.route("/api/class/monthly/<int:class_id>", methods=["GET"])
@login_required
def class_monthly_attendance(class_id):
    school = get_school()
    month  = int(request.args.get("month", date.today().month))
    year   = int(request.args.get("year", date.today().year))
    days_in_month = calendar.monthrange(year, month)[1]

    daily_data = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d > date.today(): break
        rows = Attendance.query.filter_by(school_id=school.id, class_id=class_id, att_date=d).all()
        total = len(rows)
        present = sum(1 for r in rows if r.status in ("P","Present"))
        daily_data.append({
            "date": str(d), "day": day,
            "present": present, "total": total,
            "pct": round(present/total*100,1) if total else None
        })

    return jsonify({"class_id": class_id, "month": month, "year": year, "daily": daily_data})


# ════════════════════════════════════════════════════════════
# HOMEWORK
# ════════════════════════════════════════════════════════════

@app.route("/api/homework", methods=["POST"])
@login_required
def add_homework():
    school, d = get_school(), request.json
    if not d.get("title") or not d.get("class_id"):
        return jsonify({"error": "Class and title required"}), 400

    hw = Homework(school_id=school.id, class_id=d["class_id"], subject=d.get("subject",""),
                 title=d["title"], description=d.get("description",""),
                 due_date=date.fromisoformat(d["due_date"]) if d.get("due_date") else None)
    db.session.add(hw)
    db.session.commit()

    students = Student.query.filter_by(class_id=d["class_id"], school_id=school.id).all()
    hw.wa_sent = True
    db.session.commit()

    return jsonify({"msg": "Homework saved — visible in Parent Portal", "hw_id": hw.id,
                     "students_count": len(students)}), 201


@app.route("/api/homework", methods=["GET"])
@login_required
def list_homework():
    school, class_id = get_school(), request.args.get("class_id")
    q = Homework.query.filter_by(school_id=school.id)
    if class_id: q = q.filter_by(class_id=int(class_id))
    hws = q.order_by(Homework.created_at.desc()).limit(50).all()
    return jsonify([{
        "id": h.id, "class_id": h.class_id, "subject": h.subject, "title": h.title,
        "description": h.description, "due_date": str(h.due_date) if h.due_date else None,
        "wa_sent": h.wa_sent, "created_at": str(h.created_at)
    } for h in hws])


@app.route("/api/homework/<int:hid>", methods=["DELETE"])
@login_required
def delete_homework(hid):
    school = get_school()
    hw = Homework.query.filter_by(id=hid, school_id=school.id).first_or_404()
    db.session.delete(hw)
    db.session.commit()
    return jsonify({"msg": "Homework deleted"})


# ════════════════════════════════════════════════════════════
# DASHBOARD + REPORTS (School-level)
# ════════════════════════════════════════════════════════════

@app.route("/api/dashboard", methods=["GET"])
@login_required
def dashboard():
    school, today = get_school(), date.today()
    total_students = Student.query.filter_by(school_id=school.id).count()
    total_classes  = Class.query.filter_by(school_id=school.id).count()
    today_att = Attendance.query.filter_by(school_id=school.id, att_date=today).all()

    present = sum(1 for a in today_att if a.status in ("P","Present"))
    absent  = sum(1 for a in today_att if a.status in ("A","Absent"))
    late    = sum(1 for a in today_att if a.status in ("L","Late"))
    att_pct = round(present/total_students*100,1) if total_students else 0

    wa_today = WaLog.query.filter(
        WaLog.school_id == school.id,
        db.func.date(WaLog.created_at) == today
    ).count()

    recent_hw = Homework.query.filter_by(school_id=school.id)\
        .order_by(Homework.created_at.desc()).limit(5).all()

    classes = Class.query.filter_by(school_id=school.id).all()
    class_summary = []
    for c in classes:
        total_c = Student.query.filter_by(class_id=c.id, school_id=school.id).count()
        pres_c  = sum(1 for a in today_att if a.class_id==c.id and a.status in ("P","Present"))
        class_summary.append({
            "class_id": c.id, "class_name": c.name, "teacher": c.teacher,
            "total": total_c, "present": pres_c, "absent": total_c - pres_c,
            "pct": round(pres_c/total_c*100,1) if total_c else 0
        })

    best_class  = max(class_summary, key=lambda x: x["pct"]) if class_summary else None
    worst_class = min(class_summary, key=lambda x: x["pct"]) if class_summary else None

    return jsonify({
        "school_name": school.name, "date": str(today),
        "total_students": total_students, "total_classes": total_classes,
        "plan": school.plan, "status": school.status,
        "days_left": (school.expiry_date - date.today()).days,
        "today": {"present": present, "absent": absent, "late": late,
                  "att_pct": att_pct, "wa_sent": wa_today},
        "class_summary": class_summary,
        "best_class": best_class, "worst_class": worst_class,
        "recent_homework": [{"subject": h.subject, "title": h.title,
                             "due": str(h.due_date) if h.due_date else None} for h in recent_hw]
    })


@app.route("/api/report", methods=["GET"])
@login_required
def report():
    school   = get_school()
    rep_date = request.args.get("date", str(date.today()))
    d        = date.fromisoformat(rep_date)

    rows = db.session.query(Attendance, Student, Class).join(
        Student, Attendance.student_id == Student.id
    ).join(Class, Attendance.class_id == Class.id).filter(
        Attendance.school_id == school.id, Attendance.att_date == d
    ).all()

    absent_list = [{"name": s.name, "roll": s.roll_no, "class": c.name, "phone": s.parent_phone}
                   for a,s,c in rows if a.status in ("A","Absent")]
    late_list   = [{"name": s.name, "roll": s.roll_no, "class": c.name}
                   for a,s,c in rows if a.status in ("L","Late")]

    return jsonify({
        "school": school.name, "date": str(d), "total_records": len(rows),
        "present": sum(1 for a,s,c in rows if a.status in ("P","Present")),
        "absent": len(absent_list), "late": len(late_list),
        "absent_list": absent_list, "late_list": late_list
    })


@app.route("/api/wa-log", methods=["GET"])
@login_required
def wa_log():
    school = get_school()
    logs = WaLog.query.filter_by(school_id=school.id)\
                      .filter(WaLog.msg_type != "principal_alert_marker")\
                      .order_by(WaLog.created_at.desc()).limit(100).all()
    return jsonify([{"phone": l.phone, "type": l.msg_type, "status": l.status,
                     "created_at": str(l.created_at), "preview": l.message[:60]+"..."} for l in logs])


# ════════════════════════════════════════════════════════════
# MAKE.COM WEBHOOK
# ════════════════════════════════════════════════════════════

@app.route("/webhook/daily-report", methods=["POST"])
def webhook_daily_report():
    data = request.json or {}
    if data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    school = School.query.get(data.get("school_id"))
    if not school:
        return jsonify({"error": "School not found"}), 404

    today = date.today()
    rows  = db.session.query(Attendance, Student).join(
        Student, Attendance.student_id == Student.id
    ).filter(Attendance.school_id == school.id, Attendance.att_date == today).all()

    absent  = [s.name for a,s in rows if a.status in ("A","Absent")]
    present = sum(1 for a,s in rows if a.status in ("P","Present"))
    total   = Student.query.filter_by(school_id=school.id).count()

    return jsonify({"school": school.name, "date": str(today), "total": total,
                    "present": present, "absent": len(absent), "absent_names": absent,
                    "att_pct": round(present/total*100,1) if total else 0})


# ════════════════════════════════════════════════════════════
# 👨‍👩‍👧 PARENT PORTAL — no WhatsApp, no Make.com; parents log in
# directly and view their own child's homework & attendance.
# Password = last 4 digits of the parent phone number already
# registered by the teacher (no extra field, no DB migration).
# ════════════════════════════════════════════════════════════

def _last4(phone):
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits


def parent_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "parent_student_id" not in session:
            return jsonify({"error": "Not logged in"}), 401
        return f(*a, **kw)
    return wrapper


@app.route("/parent")
def parent_portal_page():
    return render_template("parent.html")


@app.route("/api/parent/login", methods=["POST"])
def parent_login():
    d = request.get_json(force=True) or {}
    school_username = (d.get("school_username") or "").strip()
    roll_no = (d.get("roll_no") or "").strip()
    password = (d.get("password") or "").strip()

    if not school_username or not roll_no or not password:
        return jsonify({"error": "School ID, Roll No and Password are required"}), 400

    school = School.query.filter_by(username=school_username).first()
    if not school:
        return jsonify({"error": "School not found — check School ID"}), 404

    student = Student.query.filter_by(school_id=school.id, roll_no=roll_no).first()
    if not student or _last4(student.parent_phone) != password or not password:
        return jsonify({"error": "Invalid Roll No or Password"}), 401

    session["parent_student_id"] = student.id
    session["parent_school_id"] = school.id
    cls = Class.query.get(student.class_id)
    return jsonify({
        "msg": "Login successful",
        "student": {
            "name": student.name, "roll_no": student.roll_no,
            "class_name": cls.name if cls else "", "school_name": school.name
        }
    })


@app.route("/api/parent/logout", methods=["POST"])
def parent_logout():
    session.pop("parent_student_id", None)
    session.pop("parent_school_id", None)
    return jsonify({"msg": "Logged out"})


@app.route("/api/parent/me", methods=["GET"])
@parent_required
def parent_me():
    student = Student.query.get(session["parent_student_id"])
    if not student:
        return jsonify({"error": "Student not found"}), 404
    cls = Class.query.get(student.class_id)
    school = School.query.get(student.school_id)
    return jsonify({
        "name": student.name, "roll_no": student.roll_no,
        "class_name": cls.name if cls else "", "school_name": school.name if school else ""
    })


@app.route("/api/parent/homework", methods=["GET"])
@parent_required
def parent_homework():
    student = Student.query.get(session["parent_student_id"])
    if not student:
        return jsonify({"error": "Student not found"}), 404
    rows = Homework.query.filter_by(
        school_id=student.school_id, class_id=student.class_id
    ).order_by(Homework.created_at.desc()).limit(30).all()
    return jsonify([{
        "subject": h.subject, "title": h.title, "description": h.description,
        "due_date": h.due_date.strftime("%d %b %Y") if h.due_date else None,
        "assigned_on": h.created_at.strftime("%d %b %Y")
    } for h in rows])


@app.route("/api/parent/attendance", methods=["GET"])
@parent_required
def parent_attendance():
    student = Student.query.get(session["parent_student_id"])
    if not student:
        return jsonify({"error": "Student not found"}), 404
    rows = Attendance.query.filter_by(student_id=student.id).order_by(
        Attendance.att_date.desc()).limit(180).all()
    present = sum(1 for a in rows if a.status in ("P", "Present"))
    total = len(rows)
    return jsonify({
        "records": [{"date": a.att_date.strftime("%d %b %Y"), "status": a.status} for a in rows],
        "present": present, "total": total,
        "pct": round(present/total*100, 1) if total else 0
    })


# ════════════════════════════════════════════════════════════
# 🏢 SUPER ADMIN — manage all schools
# ════════════════════════════════════════════════════════════

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.json
    if d.get("username") == SUPER_ADMIN_USER and d.get("password") == SUPER_ADMIN_PASS:
        session["is_admin"] = True
        return jsonify({"msg": "Admin login successful"})
    return jsonify({"error": "Invalid admin credentials"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return jsonify({"msg": "Admin logged out"})


@app.route("/api/admin/principal-alerts", methods=["GET"])
@admin_required
def admin_principal_alerts():
    rows = WaLog.query.filter_by(msg_type="principal_alert_marker")\
                       .order_by(WaLog.created_at.desc()).limit(100).all()
    result = []
    for r in rows:
        school = School.query.get(r.school_id)
        result.append({
            "school": school.name if school else "Unknown",
            "principal_email": school.principal_email if school else "",
            "detail": r.message,
            "sent_at": str(r.created_at)
        })
    return jsonify(result)


@app.route("/api/admin/costs", methods=["GET"])
@admin_required
def admin_costs():
    import re
    today = date.today()
    month_start = today.replace(day=1)

    rows = WaLog.query.filter(
        WaLog.msg_type.like("make_%"),
        db.func.date(WaLog.created_at) >= month_start
    ).all()

    total_events = 0        # number of triggers (absent marks / homework broadcasts)
    total_recipients = 0    # total individual WhatsApp messages this represents
    sent_events = 0
    failed_events = 0

    for r in rows:
        total_events += 1
        if r.status == "sent":
            sent_events += 1
        else:
            failed_events += 1
        m = re.search(r"x(\d+)$", r.phone or "")
        total_recipients += int(m.group(1)) if m else 1

    aisensy_message_cost = round(total_recipients * AISENSY_UTILITY_RATE, 2)
    aisensy_total = round(AISENSY_PLAN_FEE + aisensy_message_cost, 2)

    # Rough Make.com operation estimate: 1 webhook trigger + 2 ops per recipient (iterator + HTTP send)
    make_ops_used = total_events + (total_recipients * 2)
    make_overage_ops = max(0, make_ops_used - MAKE_FREE_OPS)
    make_total = MAKE_PLAN_FEE  # overage billing varies by Make plan; shown as ops count instead

    grand_total = round(aisensy_total + make_total, 2)

    return jsonify({
        "period": month_start.strftime("%b %Y"),
        "total_events": total_events,
        "sent_events": sent_events,
        "failed_events": failed_events,
        "total_recipients": total_recipients,
        "aisensy_plan_fee": AISENSY_PLAN_FEE,
        "aisensy_utility_rate": AISENSY_UTILITY_RATE,
        "aisensy_message_cost": aisensy_message_cost,
        "aisensy_total": aisensy_total,
        "make_ops_used": make_ops_used,
        "make_free_ops": MAKE_FREE_OPS,
        "make_overage_ops": make_overage_ops,
        "make_plan_fee": MAKE_PLAN_FEE,
        "grand_total": grand_total
    })


@app.route("/api/admin/overview", methods=["GET"])
@admin_required
def admin_overview():
    today = date.today()
    total_schools   = School.query.count()
    active_schools  = School.query.filter_by(status="active").count()
    trial_schools   = School.query.filter_by(status="trial").count()
    suspended       = School.query.filter_by(status="suspended").count()

    total_students  = Student.query.count()
    wa_today        = WaLog.query.filter(db.func.date(WaLog.created_at) == today).count()
    hw_today        = Homework.query.filter(db.func.date(Homework.created_at) == today).count()
    att_today_marked= db.session.query(Attendance.school_id).filter(
        Attendance.att_date == today).distinct().count()

    monthly_revenue = db.session.query(db.func.sum(School.monthly_fee)).filter(
        School.status == "active").scalar() or 0

    expiring_soon = School.query.filter(
        School.expiry_date <= today + timedelta(days=7),
        School.expiry_date >= today,
        School.status.in_(["active","trial"])
    ).count()

    return jsonify({
        "total_schools": total_schools, "active_schools": active_schools,
        "trial_schools": trial_schools, "suspended_schools": suspended,
        "total_students": total_students,
        "wa_sent_today": wa_today, "homework_today": hw_today,
        "schools_marked_attendance_today": att_today_marked,
        "monthly_revenue": monthly_revenue,
        "expiring_soon": expiring_soon
    })


@app.route("/api/admin/schools", methods=["GET"])
@admin_required
def admin_list_schools():
    schools = School.query.order_by(School.created_at.desc()).all()
    today = date.today()
    result = []
    for s in schools:
        students = Student.query.filter_by(school_id=s.id).count()
        last_att = Attendance.query.filter_by(school_id=s.id)\
            .order_by(Attendance.created_at.desc()).first()
        result.append({
            "id": s.id, "name": s.name, "city": s.city, "username": s.username,
            "plan": s.plan, "status": s.status, "monthly_fee": s.monthly_fee,
            "expiry_date": str(s.expiry_date),
            "days_left": (s.expiry_date - today).days,
            "students": students,
            "principal_email": s.principal_email or "",
            "last_active": str(last_att.created_at) if last_att else "Never",
            "created_at": str(s.created_at)
        })
    return jsonify(result)


@app.route("/api/admin/schools/<int:sid>", methods=["GET"])
@admin_required
def admin_school_detail(sid):
    s = School.query.get_or_404(sid)
    return jsonify({
        "id": s.id, "name": s.name, "city": s.city, "username": s.username,
        "plan": s.plan, "status": s.status, "monthly_fee": s.monthly_fee,
        "expiry_date": str(s.expiry_date), "notes": s.notes,
        "principal_email": s.principal_email or "",
        "wa_configured": bool(s.wa_token and s.wa_phone_id),
        "classes": Class.query.filter_by(school_id=s.id).count(),
        "students": Student.query.filter_by(school_id=s.id).count(),
        "wa_sent_total": WaLog.query.filter_by(school_id=s.id).count(),
    })


@app.route("/api/admin/schools/<int:sid>", methods=["PUT"])
@admin_required
def admin_update_school(sid):
    s, d = School.query.get_or_404(sid), request.json
    if "status" in d:       s.status = d["status"]
    if "plan" in d:         s.plan = d["plan"]
    if "monthly_fee" in d:  s.monthly_fee = d["monthly_fee"]
    if "expiry_date" in d:  s.expiry_date = date.fromisoformat(d["expiry_date"])
    if "notes" in d:        s.notes = d["notes"]
    if "principal_email" in d: s.principal_email = d["principal_email"]
    db.session.commit()
    return jsonify({"msg": "School updated"})


@app.route("/api/admin/schools/<int:sid>/extend", methods=["POST"])
@admin_required
def admin_extend_subscription(sid):
    s, d = School.query.get_or_404(sid), request.json
    days = d.get("days", 30)
    base = s.expiry_date if s.expiry_date >= date.today() else date.today()
    s.expiry_date = base + timedelta(days=days)
    s.status = "active"
    db.session.commit()
    return jsonify({"msg": f"Extended by {days} days", "new_expiry": str(s.expiry_date)})


@app.route("/api/admin/schools/<int:sid>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(sid):
    s, d = School.query.get_or_404(sid), request.json
    new_pass = d.get("new_password", "school@123")
    s.password = bcrypt.generate_password_hash(new_pass).decode("utf-8")
    db.session.commit()
    return jsonify({"msg": "Password reset", "new_password": new_pass})


@app.route("/api/admin/schools/<int:sid>", methods=["DELETE"])
@admin_required
def admin_delete_school(sid):
    s = School.query.get_or_404(sid)
    Attendance.query.filter_by(school_id=sid).delete()
    Homework.query.filter_by(school_id=sid).delete()
    WaLog.query.filter_by(school_id=sid).delete()
    Student.query.filter_by(school_id=sid).delete()
    Class.query.filter_by(school_id=sid).delete()
    db.session.delete(s)
    db.session.commit()
    return jsonify({"msg": "School and all data deleted permanently"})


@app.route("/api/admin/login-as/<int:sid>", methods=["POST"])
@admin_required
def admin_login_as(sid):
    s = School.query.get_or_404(sid)
    session["school_id"]   = s.id
    session["school_name"] = s.name
    return jsonify({"msg": f"Now logged in as {s.name}", "school_id": s.id})


@app.route("/api/admin/broadcast", methods=["POST"])
@admin_required
def admin_broadcast():
    d = request.json
    message = d.get("message", "")
    schools = School.query.filter(School.status.in_(["active","trial"])).all()
    sent = sum(1 for s in schools if s.wa_token and s.wa_phone_id)
    return jsonify({"msg": f"Broadcast queued for {sent} schools", "total_schools": len(schools)})


# ════════════════════════════════════════════════════════════
# SERVE FRONTEND
# ════════════════════════════════════════════════════════════

@app.route("/admin")
def serve_admin():
    return render_template("admin.html")

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_pwa(path):
    return render_template("index.html")


# ════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Lightweight auto-migration: add new column to an already-existing
        # production table without needing a full migration tool.
        try:
            db.session.execute(db.text("ALTER TABLE school ADD COLUMN principal_email VARCHAR(150) DEFAULT ''"))
            db.session.commit()
            print("✅ Migrated: added principal_email column")
        except Exception:
            db.session.rollback()
        if School.query.count() == 0:
            demo = [
                School(name="Sunrise Public School", city="Nagpur", username="sunrise",
                      password=bcrypt.generate_password_hash("sunrise123").decode("utf-8"),
                      plan="pro", status="active", monthly_fee=1999,
                      expiry_date=date.today()+timedelta(days=300)),
                School(name="Bright Future Academy", city="Nagpur", username="brightfuture",
                      password=bcrypt.generate_password_hash("bright123").decode("utf-8"),
                      plan="basic", status="trial", monthly_fee=999,
                      expiry_date=date.today()+timedelta(days=10)),
            ]
            for s in demo: db.session.add(s)
            db.session.commit()
            print("✅ Demo schools created!")
        print("✅ Database ready!")
        print(f"🔑 Super Admin login → username: {SUPER_ADMIN_USER} / password: {SUPER_ADMIN_PASS}")
        print("🚀 Server starting on http://localhost:5000")
        print("🏢 Admin panel at http://localhost:5000/admin")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
