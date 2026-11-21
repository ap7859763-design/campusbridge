import os
import uuid

import mysql.connector
from mysql.connector import IntegrityError

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename
from config import Config


# =====================================
# FLASK CONFIGURATION
# =====================================

app = Flask(__name__)
app.config.from_object(Config)

app.secret_key = (
    app.config.get("SECRET_KEY")
    or "campusbridge-dev-key-change-this"
)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(
    app.root_path, "uploads", "resumes"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"pdf"}


# =====================================
# FILE VALIDATION
# =====================================

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# =====================================
# DATABASE CONNECTION
# =====================================

def get_db_connection():
    return mysql.connector.connect(
        host=app.config["MYSQL_HOST"],
        user=app.config["MYSQL_USER"],
        password=app.config["MYSQL_PASSWORD"],
        database=app.config["MYSQL_DATABASE"]
    )


# =====================================
# HOME
# =====================================

@app.route("/")
def home():
    return render_template("index.html")


# =====================================
# REGISTER
# =====================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return "Please fill all fields.", 400

        if len(password) < 8:
            return "Password must be at least 8 characters.", 400

        conn = None
        cursor = None

        try:

            conn = get_db_connection()
            cursor = conn.cursor()

            password_hash = generate_password_hash(password)

            cursor.execute("""
                INSERT INTO users
                (full_name, email, password_hash, role)
                VALUES (%s, %s, %s, 'student')
            """, (name, email, password_hash))

            user_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO students (user_id)
                VALUES (%s)
            """, (user_id,))

            conn.commit()

            flash(
                "Registration successful. Please login.",
                "success"
            )

            return redirect(url_for("login"))

        except IntegrityError:

            if conn:
                conn.rollback()

            return "This email is already registered.", 409

        except mysql.connector.Error:

            if conn:
                conn.rollback()

            app.logger.exception("Registration error")

            return "Database registration error.", 500

        finally:

            if cursor:
                cursor.close()

            if conn and conn.is_connected():
                conn.close()

    return render_template("register.html")


# =====================================
# LOGIN
# =====================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return "Please enter email and password.", 400

        conn = None
        cursor = None

        try:

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    id,
                    full_name AS name,
                    email,
                    password_hash,
                    role
                FROM users
                WHERE email = %s
            """, (email,))

            user = cursor.fetchone()

            if user and check_password_hash(
                user["password_hash"], password
            ):

                session.clear()

                session["user_id"] = user["id"]
                session["name"] = user["name"]
                session["role"] = user["role"]

                if user["role"] == "admin":
                    return redirect(url_for("admin_dashboard"))

                elif user["role"] == "company":
                    return redirect(url_for("company_dashboard"))

                return redirect(url_for("student_dashboard"))

            return "Invalid email or password.", 401

        except mysql.connector.Error:

            app.logger.exception("Login error")

            return "Database connection error.", 500

        finally:

            if cursor:
                cursor.close()

            if conn and conn.is_connected():
                conn.close()

    return render_template("login.html")


# =====================================
# STUDENT DASHBOARD
# =====================================

@app.route("/student_dashboard")
def student_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return "Access denied.", 403

    return render_template("student_dashboard.html")


# =====================================
# INTERNSHIPS SEARCH AND FILTER
# =====================================

@app.route("/internships")
def internships():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return "Access denied.", 403

    search = request.args.get("search", "").strip()
    location = request.args.get("location", "").strip()
    job_type = request.args.get("job_type", "").strip()

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT
                j.id,
                j.title,
                j.description,
                j.job_type,
                j.location,
                j.stipend,
                j.last_date,
                c.company_name

            FROM jobs j

            INNER JOIN companies c
                ON j.company_id = c.id

            WHERE j.status = 'active'
            AND (
                j.last_date IS NULL
                OR j.last_date >= CURDATE()
            )
        """

        params = []

        if search:

            query += """
                AND (
                    j.title LIKE %s
                    OR j.description LIKE %s
                    OR c.company_name LIKE %s
                )
            """

            value = "%" + search + "%"

            params.extend([value, value, value])

        if location:

            query += " AND j.location LIKE %s"

            params.append("%" + location + "%")

        if job_type:

            query += " AND j.job_type = %s"

            params.append(job_type)

        query += " ORDER BY j.created_at DESC"

        cursor.execute(query, tuple(params))

        internship_list = cursor.fetchall()

        return render_template(
            "internships.html",
            internships=internship_list,
            search=search,
            location=location,
            job_type=job_type
        )

    except mysql.connector.Error:

        app.logger.exception("Internship loading error")

        return "Unable to load internships.", 500

    finally:

        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()


# =====================================
# APPLY INTERNSHIP + RESUME UPLOAD
# =====================================

@app.route("/apply/<int:job_id>", methods=["POST"])
def apply_internship(job_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return "Only students can apply.", 403

    resume = request.files.get("resume")

    if not resume or not resume.filename:
        return "Please upload your resume.", 400

    if not allowed_file(resume.filename):
        return "Only PDF resumes are allowed.", 400

    file_start = resume.stream.read(5)
    resume.stream.seek(0)

    if file_start != b"%PDF-":
        return "Invalid PDF file.", 400

    conn = None
    cursor = None
    saved_file_path = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id
            FROM students
            WHERE user_id = %s
        """, (session["user_id"],))

        student = cursor.fetchone()

        if not student:
            return "Student profile not found.", 404

        cursor.execute("""
            SELECT id
            FROM jobs
            WHERE id = %s
            AND status = 'active'
            AND (
                last_date IS NULL
                OR last_date >= CURDATE()
            )
        """, (job_id,))

        job = cursor.fetchone()

        if not job:
            return "Internship is no longer available.", 404

        unique_name = (
            str(uuid.uuid4())
            + "_"
            + secure_filename(resume.filename)
        )

        saved_file_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            unique_name
        )

        resume.save(saved_file_path)

        cursor.execute("""
            INSERT INTO applications
            (student_id, job_id, resume_path, status)
            VALUES (%s, %s, %s, 'applied')
        """, (
            student["id"],
            job_id,
            unique_name
        ))

        conn.commit()

        flash(
            "Application submitted successfully!",
            "success"
        )

        return redirect(url_for("my_applications"))

    except IntegrityError:

        if conn:
            conn.rollback()

        if saved_file_path and os.path.exists(saved_file_path):
            os.remove(saved_file_path)

        return "You have already applied for this internship.", 409

    except mysql.connector.Error:

        if conn:
            conn.rollback()

        if saved_file_path and os.path.exists(saved_file_path):
            os.remove(saved_file_path)

        app.logger.exception("Application submission error")

        return "Unable to submit application.", 500

    except Exception:

        if conn:
            conn.rollback()

        if saved_file_path and os.path.exists(saved_file_path):
            os.remove(saved_file_path)

        app.logger.exception("Unexpected application error")

        return "Something went wrong.", 500

    finally:

        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()


# =====================================
# MY APPLICATIONS - STATUS TRACKING
# =====================================

@app.route("/my_applications")
def my_applications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return "Access denied.", 403

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                a.id,
                a.status,
                a.applied_at,
                j.title,
                j.location,
                j.stipend,
                c.company_name

            FROM applications a

            INNER JOIN students s
                ON a.student_id = s.id

            INNER JOIN jobs j
                ON a.job_id = j.id

            INNER JOIN companies c
                ON j.company_id = c.id

            WHERE s.user_id = %s

            ORDER BY a.applied_at DESC
        """, (session["user_id"],))

        applications = cursor.fetchall()

        return render_template(
            "my_applications.html",
            applications=applications
        )

    except mysql.connector.Error:

        app.logger.exception("My applications error")

        return "Unable to load applications.", 500

    finally:

        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()


# =====================================
# MY PROFILE - VIEW AND UPDATE
# =====================================

@app.route("/my_profile", methods=["GET", "POST"])
def my_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return "Access denied.", 403

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # UPDATE PROFILE

        if request.method == "POST":

            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            college = request.form.get("college", "").strip()
            course = request.form.get("course", "").strip()
            graduation_year = request.form.get(
                "graduation_year", ""
            ).strip()
            skills = request.form.get("skills", "").strip()

            if not name:
                return "Name is required.", 400

            if len(name) > 255:
                return "Name is too long.", 400

            if len(phone) > 15:
                return "Phone number must be 15 characters or less.", 400

            if graduation_year:

                if not graduation_year.isdigit():
                    return "Graduation year must be a number.", 400

                graduation_year = int(graduation_year)

                if not 1900 <= graduation_year <= 2100:
                    return "Please enter a valid graduation year.", 400

            else:
                graduation_year = None

            # Update users table

            cursor.execute("""
                UPDATE users
                SET full_name = %s
                WHERE id = %s
            """, (
                name,
                session["user_id"]
            ))

            # Update students table

            cursor.execute("""
                UPDATE students
                SET
                    phone = %s,
                    college = %s,
                    course = %s,
                    graduation_year = %s,
                    skills = %s
                WHERE user_id = %s
            """, (
                phone or None,
                college or None,
                course or None,
                graduation_year,
                skills or None,
                session["user_id"]
            ))

            if cursor.rowcount == 0:

                cursor.execute("""
                    SELECT id
                    FROM students
                    WHERE user_id = %s
                """, (session["user_id"],))

                student = cursor.fetchone()

                if not student:
                    return "Student profile not found.", 404

            conn.commit()

            session["name"] = name

            flash(
                "Profile updated successfully!",
                "success"
            )

            return redirect(url_for("my_profile"))

        # FETCH PROFILE

        cursor.execute("""
            SELECT
                u.full_name AS name,
                u.email,
                s.phone,
                s.college,
                s.course,
                s.graduation_year,
                s.skills

            FROM users u

            INNER JOIN students s
                ON u.id = s.user_id

            WHERE u.id = %s
        """, (session["user_id"],))

        profile = cursor.fetchone()

        if not profile:
            return "Student profile not found.", 404

        return render_template(
            "my_profile.html",
            profile=profile
        )

    except mysql.connector.Error:

        if conn:
            conn.rollback()

        app.logger.exception("Profile loading/updating error")

        return "Unable to load or update profile.", 500

    finally:

        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()


# =====================================
# COMPANY DASHBOARD
# =====================================

@app.route("/company_dashboard")
def company_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "company":
        return "Access denied.", 403

    return render_template("company_dashboard.html")


# =====================================
# ADMIN DASHBOARD
# =====================================

@app.route("/admin_dashboard")
def admin_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        return "Access denied.", 403

    return render_template("admin_dashboard.html")


# =====================================
# LOGOUT
# =====================================

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out.", "success")

    return redirect(url_for("home"))


# =====================================
# DATABASE HEALTH CHECK
# =====================================

@app.route("/health")
def health():

    conn = None

    try:

        conn = get_db_connection()

        return {"status": "Database connected"}

    except mysql.connector.Error:

        app.logger.exception("Database health check failed")

        return {"status": "Database connection failed"}, 500

    finally:

        if conn and conn.is_connected():
            conn.close()


# =====================================
# FILE SIZE ERROR
# =====================================

@app.errorhandler(413)
def file_too_large(error):

    return "Resume is too large. Maximum size is 5 MB.", 413


# =====================================
# RUN APPLICATION
# =====================================

if __name__ == "__main__":

    app.run(debug=True)