import os
import sqlite3
import hmac
import secrets
from pathlib import Path
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort,
)

# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "blood_safety.db"
SCHEMA = BASE_DIR / "schema.sql"

BLOOD_GROUPS = [
    "A+", "A-", "B+", "B-",
    "AB+", "AB-", "O+", "O-"
]

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "local-demo-key-change-before-deployment"
)

app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    if not SCHEMA.exists():
        raise FileNotFoundError(
            f"Could not find schema.sql at: {SCHEMA}"
        )

    with get_db() as conn:
        conn.executescript(
            SCHEMA.read_text(encoding="utf-8")
        )


# --------------------------------------------------
# HELPER FUNCTIONS
# --------------------------------------------------

def valid_code(value):
    value = value.strip()

    return (
        2 <= len(value) <= 30
        and all(c.isalnum() or c in "-_" for c in value)
    )


def get_form_value(name):
    return request.form.get(name, "").strip().upper()


# --------------------------------------------------
# CSRF PROTECTION
# --------------------------------------------------

def csrf_protect(view):
    @wraps(view)
    def wrapped(*args, **kwargs):

        # Check the token only when the form is submitted.
        if request.method == "POST":
            token = session.get("csrf_token")
            submitted = request.form.get("csrf_token", "")

            if (
                not token
                or not hmac.compare_digest(token, submitted)
            ):
                abort(
                    400,
                    description=(
                        "Invalid form token. "
                        "Refresh the page and try again."
                    )
                )

        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)

    return {"demo_mode": True}


# --------------------------------------------------
# EDUCATIONAL COMPATIBILITY CHECK
# NOT FOR CLINICAL USE
# --------------------------------------------------

def check_demo_compatibility(donor_group, patient_group):
    """
    Simplified educational ABO/Rh red-cell illustration only.

    This is NOT a clinical transfusion compatibility algorithm.
    It must never be used to authorize a real transfusion.
    """

    abo_donor = donor_group.rstrip("+-")
    abo_patient = patient_group.rstrip("+-")

    rh_donor = donor_group[-1]
    rh_patient = patient_group[-1]

    abo_compatible = (
        abo_donor == "O"
        or abo_patient == "AB"
        or abo_donor == abo_patient
    )

    rh_compatible = not (
        rh_donor == "+" and rh_patient == "-"
    )

    if abo_compatible and rh_compatible:
        return (
            "BASIC_CHECK_PASSED",
            "The simplified educational ABO/Rh rules did not "
            "flag a mismatch. This does not establish clinical "
            "transfusion compatibility or safety."
        )

    reasons = []

    if not abo_compatible:
        reasons.append(
            "ABO groups are incompatible under the demo rules"
        )

    if not rh_compatible:
        reasons.append(
            "Rh-positive donor red cells are flagged for "
            "an Rh-negative recipient"
        )

    return (
        "MISMATCH_FLAGGED",
        "; ".join(reasons)
        + ". This is not a clinical decision."
    )


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/")
def dashboard():

    with get_db() as conn:

        patients = conn.execute(
            "SELECT COUNT(*) FROM patients"
        ).fetchone()[0]

        units = conn.execute(
            "SELECT COUNT(*) FROM blood_units"
        ).fetchone()[0]

        checks = conn.execute(
            "SELECT COUNT(*) FROM verification_logs"
        ).fetchone()[0]

        alerts = conn.execute(
            """
            SELECT COUNT(*)
            FROM verification_logs
            WHERE result != 'BASIC_CHECK_PASSED'
            """
        ).fetchone()[0]

        recent = conn.execute(
            """
            SELECT *
            FROM verification_logs
            ORDER BY id DESC
            LIMIT 6
            """
        ).fetchall()

    return render_template(
        "dashboard.html",
        patients=patients,
        units=units,
        checks=checks,
        alerts=alerts,
        recent=recent,
    )


# --------------------------------------------------
# REGISTER PATIENTS AND BLOOD UNITS
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
@csrf_protect
def register():

    if request.method == "POST":

        kind = request.form.get("kind", "")

        # Register a synthetic patient
        if kind == "patient":

            code = get_form_value("patient_code")
            group = get_form_value("blood_group")

            if (
                not valid_code(code)
                or group not in BLOOD_GROUPS
            ):
                flash(
                    "Enter a valid patient ID and blood group.",
                    "error"
                )
                return redirect(url_for("register"))

            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO patients
                        (patient_code, blood_group)
                        VALUES (?, ?)
                        """,
                        (code, group)
                    )

                flash(
                    "Synthetic patient registered successfully.",
                    "success"
                )

            except sqlite3.IntegrityError:
                flash(
                    "That patient ID already exists.",
                    "error"
                )

        # Register a synthetic blood unit
        elif kind == "unit":

            code = get_form_value("unit_code")
            donor = get_form_value("donor_code")
            group = get_form_value("blood_group")

            if (
                not valid_code(code)
                or not valid_code(donor)
                or group not in BLOOD_GROUPS
            ):
                flash(
                    "Enter valid donor and unit IDs and a blood group.",
                    "error"
                )
                return redirect(url_for("register"))

            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO blood_units
                        (unit_code, donor_code, blood_group)
                        VALUES (?, ?, ?)
                        """,
                        (code, donor, group)
                    )

                flash(
                    "Synthetic blood unit registered successfully.",
                    "success"
                )

            except sqlite3.IntegrityError:
                flash(
                    "That blood-unit ID already exists.",
                    "error"
                )

        else:
            flash(
                "Unknown registration type.",
                "error"
            )

        return redirect(url_for("register"))

    return render_template(
        "register.html",
        groups=BLOOD_GROUPS
    )


# --------------------------------------------------
# EDUCATIONAL VERIFICATION
# --------------------------------------------------

@app.route("/verify", methods=["GET", "POST"])
@csrf_protect
def verify():

    outcome = None

    with get_db() as conn:

        patients = conn.execute(
            """
            SELECT patient_code, blood_group
            FROM patients
            ORDER BY patient_code
            """
        ).fetchall()

        units = conn.execute(
            """
            SELECT unit_code, donor_code, blood_group
            FROM blood_units
            ORDER BY unit_code
            """
        ).fetchall()

    if request.method == "POST":

        patient_code = get_form_value("patient_code")
        unit_code = get_form_value("unit_code")

        patient = next(
            (
                p for p in patients
                if p["patient_code"] == patient_code
            ),
            None
        )

        unit = next(
            (
                u for u in units
                if u["unit_code"] == unit_code
            ),
            None
        )

        if not patient or not unit:
            flash(
                "Select an existing patient and blood unit. "
                "Unknown IDs cannot be checked.",
                "error"
            )
            return redirect(url_for("verify"))

        result, reason = check_demo_compatibility(
            unit["blood_group"],
            patient["blood_group"]
        )

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO verification_logs
                (
                    patient_code,
                    unit_code,
                    patient_group,
                    donor_group,
                    result,
                    reason
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    patient["patient_code"],
                    unit["unit_code"],
                    patient["blood_group"],
                    unit["blood_group"],
                    result,
                    reason,
                )
            )

        outcome = {
            "patient_code": patient["patient_code"],
            "unit_code": unit["unit_code"],
            "patient_group": patient["blood_group"],
            "donor_group": unit["blood_group"],
            "result": result,
            "reason": reason,
        }

    return render_template(
        "verify.html",
        patients=patients,
        units=units,
        outcome=outcome
    )


# --------------------------------------------------
# VERIFICATION HISTORY
# --------------------------------------------------

@app.route("/history")
def history():

    search = request.args.get("q", "").strip()[:50]

    with get_db() as conn:

        if search:
            pattern = f"%{search}%"

            logs = conn.execute(
                """
                SELECT *
                FROM verification_logs
                WHERE patient_code LIKE ?
                   OR unit_code LIKE ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (pattern, pattern)
            ).fetchall()

        else:
            logs = conn.execute(
                """
                SELECT *
                FROM verification_logs
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()

    return render_template(
        "history.html",
        logs=logs,
        search=search
    )


# --------------------------------------------------
# ERROR PAGES
# --------------------------------------------------

@app.errorhandler(400)
def bad_request(error):

    return render_template(
        "error.html",
        code=400,
        message=getattr(
            error,
            "description",
            "Invalid request."
        )
    ), 400


@app.errorhandler(404)
def not_found(error):

    return render_template(
        "error.html",
        code=404,
        message="Page not found."
    ), 404


# --------------------------------------------------
# STARTUP
# --------------------------------------------------

init_db()


if __name__ == "__main__":
    app.run(debug=True)