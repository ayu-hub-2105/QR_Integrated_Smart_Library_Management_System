from __future__ import annotations

import html
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, redirect, render_template_string, request, session, url_for

try:
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
DATABASE_DIR = PROJECT_DIR / "database"
DB_PATH = DATABASE_DIR / "smart_library.db"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

app = Flask(__name__, static_folder=str(FRONTEND_DIR / "static"), static_url_path="/static")
app.secret_key = os.environ.get("LIBRARY_SECRET_KEY", secrets.token_hex(32))


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today() -> date:
    return date.today()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(amount: Any) -> str:
    try:
        return f"Rs. {float(amount):.2f}"
    except (TypeError, ValueError):
        return "Rs. 0.00"


def hash_password(password: str, salt: str | None = None) -> str:
    import hashlib

    salt = salt or secrets.token_hex(16)
    rounds = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    import hashlib
    import hmac

    try:
        _, rounds, salt, digest = stored_hash.split("$", 3)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(actual, digest)


def get_setting(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def loan_days(conn: sqlite3.Connection) -> int:
    try:
        return max(1, int(get_setting(conn, "loan_days", "14")))
    except ValueError:
        return 14


def fine_per_day(conn: sqlite3.Connection) -> float:
    try:
        return max(0.0, float(get_setting(conn, "fine_per_day", "5")))
    except ValueError:
        return 5.0


def calculate_fine(conn: sqlite3.Connection, due_date: str | None, checked_on: date | None = None) -> float:
    due = parse_date(due_date)
    if due is None:
        return 0.0
    return max(((checked_on or today()) - due).days, 0) * fine_per_day(conn)


def build_user_token(user_id: int, roll_no: str) -> str:
    return f"USER:{user_id}:{(roll_no or 'NO-ROLL').replace(' ', '-')}"


def build_book_token(book_id: int, isbn: str) -> str:
    return f"BOOK:{book_id}:{(isbn or 'NO-ISBN').replace(' ', '-')}"


def redirect_url(path: str, message: str | None = None, kind: str = "success") -> str:
    if not message:
        return path
    return f"{path}?message={quote(message)}&kind={quote(kind)}"


def pill(label: str, kind: str = "muted") -> str:
    return f'<span class="pill pill-{esc(kind)}">{esc(label)}</span>'


def init_db() -> None:
    DATABASE_DIR.mkdir(exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        set_setting(conn, "loan_days", get_setting(conn, "loan_days", "14"))
        set_setting(conn, "fine_per_day", get_setting(conn, "fine_per_day", "5"))

        if conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"] == 0:
            for name, email, roll_no, role, password in (
                ("Admin Librarian", "admin@example.com", "", "admin", "admin123"),
                ("Ayush Raj", "student@example.com", "23303435003", "student", "student123"),
            ):
                cur = conn.execute(
                    "INSERT INTO users(name, email, roll_no, role, password_hash, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (name, email, roll_no, role, hash_password(password), now_iso()),
                )
                conn.execute(
                    "UPDATE users SET qr_token = ? WHERE id = ?",
                    (build_user_token(cur.lastrowid, roll_no or email), cur.lastrowid),
                )

        if conn.execute("SELECT COUNT(*) AS total FROM books").fetchone()["total"] == 0:
            for title, author, isbn, category, shelf, copies in (
                ("Python Programming", "Guido van Rossum", "9781593279288", "Programming", "A1-01", 4),
                ("Database System Concepts", "Abraham Silberschatz", "9780073523323", "Database", "B2-04", 3),
                ("Clean Code", "Robert C. Martin", "9780132350884", "Software Engineering", "A2-03", 2),
                ("Computer Networks", "Andrew S. Tanenbaum", "9780132126953", "Networking", "C1-02", 2),
            ):
                cur = conn.execute(
                    "INSERT INTO books(title, author, isbn, category, shelf_location, total_copies, available_copies, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (title, author, isbn, category, shelf, copies, copies, now_iso()),
                )
                conn.execute(
                    "UPDATE books SET qr_token = ? WHERE id = ?",
                    (build_book_token(cur.lastrowid, isbn), cur.lastrowid),
                )


def current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def require_login(*roles: str) -> sqlite3.Row | Response:
    user = current_user()
    if not user:
        return redirect(redirect_url("/login", "Please sign in to continue.", "warning"))
    if roles and user["role"] not in roles:
        return Response("Forbidden", 403)
    return user


def page(title: str, body: str, active: str = "") -> str:
    user = current_user()
    notice = ""
    if request.args.get("message"):
        kind = request.args.get("kind", "success")
        notice = f'<div class="notice notice-{esc(kind)}">{esc(request.args["message"])}</div>'

    nav = ""
    shell_class = "auth-shell"
    if user:
        shell_class = "shell"
        if user["role"] == "admin":
            items = (
                ("Dashboard", "/admin/dashboard", "dashboard"),
                ("Books", "/admin/books", "books"),
                ("Users", "/admin/users", "users"),
                ("Issue/Return", "/admin/issue-return", "issue"),
                ("Transactions", "/admin/transactions", "transactions"),
                ("Settings", "/admin/settings", "settings"),
            )
        else:
            items = (
                ("Dashboard", "/student/dashboard", "dashboard"),
                ("Catalog", "/student/books", "books"),
                ("History", "/student/history", "history"),
                ("My QR", "/student/qr", "qr"),
            )
        links = "".join(
            f'<a class="{"active" if active == key else ""}" href="{href}">{label}</a>'
            for label, href, key in items
        )
        nav = f"""
        <aside class="sidebar">
            <a class="brand" href="/dashboard">
                <span class="brand-mark">QR</span>
                <span><strong>Smart Library</strong><small>{esc(user["role"].title())}</small></span>
            </a>
            <nav>{links}</nav>
            <form method="post" action="/logout"><button class="ghost full" type="submit">Sign out</button></form>
        </aside>
        """

    return render_template_string(
        """<!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>{{ title }} | Smart Library</title>
            <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
        </head>
        <body>
            <div class="{{ shell_class }}">
                {{ nav|safe }}
                <main class="content">
                    {{ notice|safe }}{{ body|safe }}
                    <footer class="site-footer">
                        &copy; 2026 MPI Patna. All Rights Reserved. | Web Development &amp; Design by Ayush Raj, BCA Student (2023-2026)
                    </footer>
                </main>
            </div>
            <script src="{{ url_for('static', filename='app.js') }}"></script>
        </body>
        </html>""",
        title=title,
        shell_class=shell_class,
        nav=nav,
        notice=notice,
        body=body,
    )


@app.get("/")
@app.get("/dashboard")
def dashboard_redirect() -> Response:
    user = current_user()
    if not user:
        return redirect("/login")
    return redirect("/admin/dashboard" if user["role"] == "admin" else "/student/dashboard")


@app.get("/login")
def login_page() -> str:
    body = """
    <section class="login-panel">
        <div class="login-visual">
            <div class="qr-stack"><div></div><div></div><div></div></div>
            <h1>QR-Integrated Smart Library Management System</h1>
            <p>Magadh Professional Institute, Patna</p>
        </div>
        <form class="auth-card" method="post" action="/login">
            <p class="eyebrow">Secure access</p>
            <h2>Sign in</h2>
            <label>Email <input name="email" type="email" value="admin@example.com" required></label>
            <label>Password <input name="password" type="password" value="admin123" required></label>
            <button type="submit" class="button full">Sign in</button>
            <div class="demo-logins">
                <span>Admin: admin@example.com / admin123</span>
                <span>Student: student@example.com / student123</span>
            </div>
        </form>
    </section>
    """
    return page("Sign in", body)


@app.post("/login")
def login() -> Response:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
    if not user or not verify_password(password, user["password_hash"]):
        return redirect(redirect_url("/login", "Invalid email or password.", "error"))
    session["user_id"] = user["id"]
    return redirect("/admin/dashboard" if user["role"] == "admin" else "/student/dashboard")


@app.post("/logout")
def logout() -> Response:
    session.clear()
    return redirect(redirect_url("/login", "You have been signed out."))


@app.get("/admin/dashboard")
def admin_dashboard() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        metrics = {
            "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "copies": conn.execute("SELECT COALESCE(SUM(available_copies), 0) FROM books").fetchone()[0],
            "students": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone()[0],
            "issued": conn.execute("SELECT COUNT(*) FROM transactions WHERE status = 'issued'").fetchone()[0],
        }
        recent = conn.execute(
            """
            SELECT t.*, b.title, u.name AS student_name
            FROM transactions t
            JOIN books b ON b.id = t.book_id
            JOIN users u ON u.id = t.student_id
            ORDER BY t.id DESC LIMIT 6
            """
        ).fetchall()
    rows = "".join(
        f"<tr><td><strong>{esc(r['title'])}</strong></td><td>{esc(r['student_name'])}</td>"
        f"<td>{esc(r['issue_date'])}</td><td>{pill(r['status'], 'ok' if r['status'] == 'returned' else 'info')}</td></tr>"
        for r in recent
    ) or '<tr><td colspan="4">No transactions yet.</td></tr>'
    body = f"""
    <section class="page-head"><div><p class="eyebrow">Admin dashboard</p><h1>Library overview</h1></div>
        <a class="button" href="/admin/issue-return">Issue or return</a></section>
    <section class="metric-grid">
        <article class="metric"><span>Book titles</span><strong>{metrics["books"]}</strong></article>
        <article class="metric"><span>Available copies</span><strong>{metrics["copies"]}</strong></article>
        <article class="metric"><span>Students</span><strong>{metrics["students"]}</strong></article>
        <article class="metric"><span>Active issues</span><strong>{metrics["issued"]}</strong></article>
    </section>
    <section class="panel"><div class="panel-head"><h2>Recent transactions</h2><a href="/admin/transactions">View all</a></div>
        <div class="table-wrap"><table><thead><tr><th>Book</th><th>Student</th><th>Issued</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    """
    return page("Admin dashboard", body, "dashboard")


@app.get("/admin/books")
def admin_books() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    q = request.args.get("q", "").strip()
    with db() as conn:
        if q:
            like = f"%{q}%"
            books = conn.execute(
                "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ? OR category LIKE ? ORDER BY title",
                (like, like, like, like),
            ).fetchall()
        else:
            books = conn.execute("SELECT * FROM books ORDER BY title").fetchall()
    rows = "".join(
        f"""
        <tr>
            <td><strong>{esc(b["title"])}</strong><small>{esc(b["author"])}</small></td>
            <td>{esc(b["isbn"] or "-")}</td><td>{esc(b["category"] or "-")}</td>
            <td>{esc(b["shelf_location"] or "-")}</td><td>{esc(b["available_copies"])} / {esc(b["total_copies"])}</td>
            <td><img class="qr-thumb" src="/qr/book/{b["id"]}.svg" alt="Book QR"><small>{esc(b["qr_token"])}</small></td>
        </tr>"""
        for b in books
    ) or '<tr><td colspan="6">No books found.</td></tr>'
    body = f"""
    <section class="page-head"><div><p class="eyebrow">Book management</p><h1>Inventory and QR codes</h1></div>
        <form class="search" method="get"><input name="q" value="{esc(q)}" placeholder="Search books"><button class="ghost">Search</button></form></section>
    <form class="panel form-grid" method="post" action="/admin/books/create">
        <label>Title<input name="title" required></label><label>Author<input name="author" required></label>
        <label>ISBN<input name="isbn"></label><label>Category<input name="category"></label>
        <label>Shelf<input name="shelf_location"></label><label>Total copies<input name="total_copies" type="number" min="1" value="1"></label>
        <button class="button" type="submit">Add book</button>
    </form>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>Book</th><th>ISBN</th><th>Category</th><th>Shelf</th><th>Copies</th><th>QR</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    """
    return page("Books", body, "books")


@app.post("/admin/books/create")
def create_book() -> Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    copies = max(1, int(request.form.get("total_copies", "1") or 1))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO books(title, author, isbn, category, shelf_location, total_copies, available_copies, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.form.get("title", "").strip(),
                request.form.get("author", "").strip(),
                request.form.get("isbn", "").strip(),
                request.form.get("category", "").strip(),
                request.form.get("shelf_location", "").strip(),
                copies,
                copies,
                now_iso(),
            ),
        )
        conn.execute("UPDATE books SET qr_token = ? WHERE id = ?", (build_book_token(cur.lastrowid, request.form.get("isbn", "")), cur.lastrowid))
    return redirect(redirect_url("/admin/books", "Book added."))


@app.get("/admin/users")
def admin_users() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    rows = "".join(
        f"""
        <tr><td><strong>{esc(u["name"])}</strong><small>{esc(u["email"])}</small></td>
        <td>{esc(u["roll_no"] or "-")}</td><td>{pill(u["role"], "info" if u["role"] == "admin" else "ok")}</td>
        <td><img class="qr-thumb" src="/qr/user/{u["id"]}.svg" alt="User QR"><small>{esc(u["qr_token"])}</small></td></tr>
        """
        for u in users
    )
    body = f"""
    <section class="page-head compact"><div><p class="eyebrow">User management</p><h1>Students and librarians</h1></div></section>
    <form class="panel form-grid" method="post" action="/admin/users/create">
        <label>Name<input name="name" required></label><label>Email<input name="email" type="email" required></label>
        <label>Roll number<input name="roll_no"></label><label>Role<select name="role"><option value="student">Student</option><option value="admin">Admin</option></select></label>
        <label>Password<input name="password" type="password" value="student123" required></label><button class="button">Add user</button>
    </form>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>User</th><th>Roll</th><th>Role</th><th>QR</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    """
    return page("Users", body, "users")


@app.post("/admin/users/create")
def create_user() -> Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    role = request.form.get("role", "student")
    if role not in {"admin", "student"}:
        role = "student"
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(name, email, roll_no, role, password_hash, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    request.form.get("name", "").strip(),
                    request.form.get("email", "").strip().lower(),
                    request.form.get("roll_no", "").strip(),
                    role,
                    hash_password(request.form.get("password", "student123")),
                    now_iso(),
                ),
            )
            conn.execute("UPDATE users SET qr_token = ? WHERE id = ?", (build_user_token(cur.lastrowid, request.form.get("roll_no", "")), cur.lastrowid))
        except sqlite3.IntegrityError:
            return redirect(redirect_url("/admin/users", "That email is already registered.", "error"))
    return redirect(redirect_url("/admin/users", "User added."))


def find_student_and_book(conn: sqlite3.Connection, student_qr: str, book_qr: str) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    student = conn.execute("SELECT * FROM users WHERE qr_token = ? AND role = 'student'", (student_qr.strip(),)).fetchone()
    book = conn.execute("SELECT * FROM books WHERE qr_token = ?", (book_qr.strip(),)).fetchone()
    return student, book


@app.get("/admin/issue-return")
def issue_return_page() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    body = """
    <section class="page-head compact"><div><p class="eyebrow">QR transaction module</p><h1>Issue and return books</h1></div></section>
    <section class="split-grid">
        <form class="panel form-grid narrow" method="post" action="/admin/issue">
            <h2>Issue book</h2>
            <label>Student QR token<input id="issue-student" name="student_qr" placeholder="USER:..." required></label>
            <button class="ghost" type="button" data-scan-target="issue-student">Scan student QR</button>
            <label>Book QR token<input id="issue-book" name="book_qr" placeholder="BOOK:..." required></label>
            <button class="ghost" type="button" data-scan-target="issue-book">Scan book QR</button>
            <button class="button" type="submit">Issue book</button>
        </form>
        <form class="panel form-grid narrow" method="post" action="/admin/return">
            <h2>Return book</h2>
            <label>Student QR token<input id="return-student" name="student_qr" placeholder="USER:..." required></label>
            <button class="ghost" type="button" data-scan-target="return-student">Scan student QR</button>
            <label>Book QR token<input id="return-book" name="book_qr" placeholder="BOOK:..." required></label>
            <button class="ghost" type="button" data-scan-target="return-book">Scan book QR</button>
            <button class="button" type="submit">Return book</button>
        </form>
    </section>
    <section id="scanner" class="scanner" hidden>
        <video id="scanner-video" autoplay muted playsinline></video>
        <p id="scanner-status">Looking for QR code...</p>
        <button class="ghost" type="button" data-scan-stop>Stop scanner</button>
    </section>
    """
    return page("Issue and return", body, "issue")


@app.post("/admin/issue")
def issue_book() -> Response:
    admin = require_login("admin")
    if not isinstance(admin, sqlite3.Row):
        return admin
    with db() as conn:
        student, book = find_student_and_book(conn, request.form.get("student_qr", ""), request.form.get("book_qr", ""))
        if not student or not book:
            return redirect(redirect_url("/admin/issue-return", "Student or book QR code was not found.", "error"))
        if book["available_copies"] <= 0:
            return redirect(redirect_url("/admin/issue-return", "No copies are available.", "warning"))
        active = conn.execute(
            "SELECT id FROM transactions WHERE book_id = ? AND student_id = ? AND status = 'issued'",
            (book["id"], student["id"]),
        ).fetchone()
        if active:
            return redirect(redirect_url("/admin/issue-return", "This student already has this book.", "warning"))
        issued = today()
        due = issued + timedelta(days=loan_days(conn))
        conn.execute(
            "INSERT INTO transactions(book_id, student_id, issue_date, due_date, status, created_by) VALUES(?, ?, ?, ?, 'issued', ?)",
            (book["id"], student["id"], issued.isoformat(), due.isoformat(), admin["id"]),
        )
        conn.execute("UPDATE books SET available_copies = available_copies - 1 WHERE id = ?", (book["id"],))
    return redirect(redirect_url("/admin/issue-return", f"Issued {book['title']} to {student['name']} until {due.isoformat()}."))


@app.post("/admin/return")
def return_book() -> Response:
    admin = require_login("admin")
    if not isinstance(admin, sqlite3.Row):
        return admin
    with db() as conn:
        student, book = find_student_and_book(conn, request.form.get("student_qr", ""), request.form.get("book_qr", ""))
        if not student or not book:
            return redirect(redirect_url("/admin/issue-return", "Student or book QR code was not found.", "error"))
        tx = conn.execute(
            "SELECT * FROM transactions WHERE book_id = ? AND student_id = ? AND status = 'issued' ORDER BY id DESC LIMIT 1",
            (book["id"], student["id"]),
        ).fetchone()
        if not tx:
            return redirect(redirect_url("/admin/issue-return", "No active issue was found.", "warning"))
        fine = calculate_fine(conn, tx["due_date"], today())
        conn.execute(
            "UPDATE transactions SET return_date = ?, fine_amount = ?, status = 'returned', returned_by = ? WHERE id = ?",
            (today().isoformat(), fine, admin["id"], tx["id"]),
        )
        conn.execute("UPDATE books SET available_copies = available_copies + 1 WHERE id = ?", (book["id"],))
    return redirect(redirect_url("/admin/issue-return", f"Returned {book['title']}. Fine: {money(fine)}."))


@app.get("/admin/transactions")
def admin_transactions() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    status = request.args.get("status", "all")
    where, args = ("", ())
    if status in {"issued", "returned"}:
        where, args = ("WHERE t.status = ?", (status,))
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, b.title, b.isbn, u.name AS student_name, u.roll_no
            FROM transactions t
            JOIN books b ON b.id = t.book_id
            JOIN users u ON u.id = t.student_id
            {where}
            ORDER BY t.id DESC
            """,
            args,
        ).fetchall()
        rendered = []
        for r in rows:
            current_fine = calculate_fine(conn, r["due_date"]) if r["status"] == "issued" else r["fine_amount"]
            action = ""
            if r["status"] == "returned" and r["fine_amount"] > 0 and not r["fine_paid"]:
                action = f'<form method="post" action="/admin/fines/pay"><input type="hidden" name="transaction_id" value="{r["id"]}"><button class="ghost small">Mark paid</button></form>'
            rendered.append(
                f"<tr><td><strong>{esc(r['title'])}</strong><small>{esc(r['isbn'])}</small></td>"
                f"<td><strong>{esc(r['student_name'])}</strong><small>{esc(r['roll_no'])}</small></td>"
                f"<td>{esc(r['issue_date'])}</td><td>{esc(r['due_date'])}</td><td>{esc(r['return_date'] or '-')}</td>"
                f"<td>{pill(r['status'], 'ok' if r['status'] == 'returned' else 'info')}</td><td>{money(current_fine)}</td><td>{action}</td></tr>"
            )
    rows_html = "".join(rendered) or '<tr><td colspan="8">No transactions found.</td></tr>'
    selected = {key: "selected" if status == key else "" for key in ("all", "issued", "returned")}
    body = f"""
    <section class="page-head"><div><p class="eyebrow">Transaction module</p><h1>Issue history and fines</h1></div>
        <div class="actions"><form method="get" class="filter"><select name="status">
        <option value="all" {selected["all"]}>All</option><option value="issued" {selected["issued"]}>Issued</option><option value="returned" {selected["returned"]}>Returned</option>
        </select><button class="ghost">Filter</button></form><a class="button" href="/admin/reports/transactions.pdf">PDF report</a></div></section>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>Book</th><th>Student</th><th>Issued</th><th>Due</th><th>Returned</th><th>Status</th><th>Fine</th><th></th></tr></thead><tbody>{rows_html}</tbody></table></div></section>
    """
    return page("Transactions", body, "transactions")


@app.post("/admin/fines/pay")
def pay_fine() -> Response:
    admin = require_login("admin")
    if not isinstance(admin, sqlite3.Row):
        return admin
    tx_id = int(request.form.get("transaction_id", "0") or 0)
    with db() as conn:
        tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not tx or tx["fine_amount"] <= 0:
            return redirect(redirect_url("/admin/transactions", "No payable fine found.", "warning"))
        conn.execute(
            "INSERT INTO fine_payments(transaction_id, amount, paid_at, recorded_by) VALUES(?, ?, ?, ?)",
            (tx_id, tx["fine_amount"], now_iso(), admin["id"]),
        )
        conn.execute("UPDATE transactions SET fine_paid = 1 WHERE id = ?", (tx_id,))
    return redirect(redirect_url("/admin/transactions", "Fine payment recorded."))


@app.get("/admin/settings")
def settings_page() -> str | Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        days, rate = loan_days(conn), fine_per_day(conn)
    body = f"""
    <section class="page-head compact"><div><p class="eyebrow">System settings</p><h1>Loan and fine rules</h1></div></section>
    <form class="panel form-grid narrow" method="post" action="/admin/settings">
        <label>Loan period in days<input name="loan_days" type="number" min="1" value="{days}" required></label>
        <label>Fine per overdue day<input name="fine_per_day" type="number" min="0" step="0.5" value="{rate:g}" required></label>
        <button class="button">Save settings</button>
    </form>
    """
    return page("Settings", body, "settings")


@app.post("/admin/settings")
def update_settings() -> Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        set_setting(conn, "loan_days", str(max(1, int(request.form.get("loan_days", "14") or 14))))
        set_setting(conn, "fine_per_day", str(max(0.0, float(request.form.get("fine_per_day", "5") or 5))))
    return redirect(redirect_url("/admin/settings", "Settings saved."))


@app.get("/student/dashboard")
def student_dashboard() -> str | Response:
    user = require_login("student")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        active = conn.execute(
            "SELECT t.*, b.title, b.author FROM transactions t JOIN books b ON b.id = t.book_id WHERE t.student_id = ? AND t.status = 'issued' ORDER BY t.due_date",
            (user["id"],),
        ).fetchall()
        returned_due = conn.execute(
            "SELECT COALESCE(SUM(fine_amount), 0) AS total FROM transactions WHERE student_id = ? AND fine_paid = 0",
            (user["id"],),
        ).fetchone()["total"]
        active_items = [(row, calculate_fine(conn, row["due_date"])) for row in active]
        active_fine = sum(fine for _, fine in active_items)
    rows = "".join(
        f"<tr><td><strong>{esc(r['title'])}</strong><small>{esc(r['author'])}</small></td><td>{esc(r['issue_date'])}</td><td>{esc(r['due_date'])}</td><td>{money(fine)}</td></tr>"
        for r, fine in active_items
    ) or '<tr><td colspan="4">No active issued books.</td></tr>'
    body = f"""
    <section class="page-head"><div><p class="eyebrow">Student dashboard</p><h1>Welcome, {esc(user["name"])}</h1></div><a class="button" href="/student/qr">View QR ID</a></section>
    <section class="metric-grid"><article class="metric"><span>Issued books</span><strong>{len(active)}</strong></article><article class="metric"><span>Current fines</span><strong>{money(active_fine + returned_due)}</strong></article><article class="metric"><span>Roll number</span><strong>{esc(user["roll_no"] or "-")}</strong></article></section>
    <section class="panel"><div class="panel-head"><h2>Active issues</h2><a href="/student/history">History</a></div><div class="table-wrap"><table><thead><tr><th>Book</th><th>Issued</th><th>Due</th><th>Fine</th></tr></thead><tbody>{rows}</tbody></table></div></section>
    """
    return page("Student dashboard", body, "dashboard")


@app.get("/student/books")
def student_books() -> str | Response:
    user = require_login("student")
    if not isinstance(user, sqlite3.Row):
        return user
    q = request.args.get("q", "").strip()
    with db() as conn:
        if q:
            like = f"%{q}%"
            books = conn.execute(
                "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ? OR category LIKE ? ORDER BY title",
                (like, like, like, like),
            ).fetchall()
        else:
            books = conn.execute("SELECT * FROM books ORDER BY title").fetchall()
    cards = "".join(
        f"""
        <article class="book-card"><div><h3>{esc(b["title"])}</h3><p>{esc(b["author"])}</p></div>
        <dl><div><dt>Category</dt><dd>{esc(b["category"] or "-")}</dd></div><div><dt>Shelf</dt><dd>{esc(b["shelf_location"] or "-")}</dd></div><div><dt>Available</dt><dd>{esc(b["available_copies"])} / {esc(b["total_copies"])}</dd></div></dl></article>
        """
        for b in books
    ) or '<div class="empty"><strong>No books found</strong><span>Try another title, author, ISBN, or category.</span></div>'
    body = f"""
    <section class="page-head"><div><p class="eyebrow">Library catalog</p><h1>Search books</h1></div><form class="search" method="get"><input name="q" value="{esc(q)}" placeholder="Search catalog"><button class="ghost">Search</button></form></section>
    <section class="book-grid">{cards}</section>
    """
    return page("Catalog", body, "books")


@app.get("/student/history")
def student_history() -> str | Response:
    user = require_login("student")
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        rows = conn.execute(
            "SELECT t.*, b.title, b.author FROM transactions t JOIN books b ON b.id = t.book_id WHERE t.student_id = ? ORDER BY t.id DESC",
            (user["id"],),
        ).fetchall()
        rendered = []
        for r in rows:
            current_fine = r["fine_amount"] if r["status"] == "returned" else calculate_fine(conn, r["due_date"])
            rendered.append(
                f"<tr><td><strong>{esc(r['title'])}</strong><small>{esc(r['author'])}</small></td><td>{esc(r['issue_date'])}</td><td>{esc(r['due_date'])}</td><td>{esc(r['return_date'] or '-')}</td><td>{pill(r['status'], 'ok' if r['status'] == 'returned' else 'info')}</td><td>{money(current_fine)}</td></tr>"
            )
    rows_html = "".join(rendered) or '<tr><td colspan="6">No issue history yet.</td></tr>'
    body = f"""
    <section class="page-head compact"><div><p class="eyebrow">Student module</p><h1>Issued history and fines</h1></div></section>
    <section class="panel"><div class="table-wrap"><table><thead><tr><th>Book</th><th>Issued</th><th>Due</th><th>Returned</th><th>Status</th><th>Fine</th></tr></thead><tbody>{rows_html}</tbody></table></div></section>
    """
    return page("History", body, "history")


@app.get("/student/qr")
def student_qr() -> str | Response:
    user = require_login("student")
    if not isinstance(user, sqlite3.Row):
        return user
    body = f"""
    <section class="page-head compact"><div><p class="eyebrow">Digital QR ID</p><h1>{esc(user["name"])}</h1></div></section>
    <section class="qr-card"><img src="/qr/user/{user["id"]}.svg" alt="Student QR ID"><div><h2>{esc(user["qr_token"])}</h2><p>{esc(user["email"])}</p><p>Roll: {esc(user["roll_no"] or "-")}</p></div></section>
    """
    return page("My QR", body, "qr")


def svg_qr(payload: str) -> bytes:
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab is required for QR generation.")
    size = 180
    widget = qr.QrCodeWidget(payload)
    bounds = widget.getBounds()
    scale = min(size / (bounds[2] - bounds[0]), size / (bounds[3] - bounds[1]))
    drawing = Drawing(size, size, transform=[scale, 0, 0, scale, 0, 0])
    drawing.add(widget)
    svg = renderSVG.drawToString(drawing)
    return svg.encode() if isinstance(svg, str) else svg


@app.get("/qr/book/<int:book_id>.svg")
def book_qr(book_id: int) -> Response:
    user = require_login()
    if not isinstance(user, sqlite3.Row):
        return user
    with db() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        return Response("QR not found", 404)
    return Response(svg_qr(book["qr_token"]), mimetype="image/svg+xml")


@app.get("/qr/user/<int:user_id>.svg")
def user_qr(user_id: int) -> Response:
    user = require_login()
    if not isinstance(user, sqlite3.Row):
        return user
    if user["role"] != "admin" and user["id"] != user_id:
        return Response("Forbidden", 403)
    with db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return Response("QR not found", 404)
    return Response(svg_qr(target["qr_token"]), mimetype="image/svg+xml")


@app.get("/admin/reports/transactions.pdf")
def transactions_pdf() -> Response:
    user = require_login("admin")
    if not isinstance(user, sqlite3.Row):
        return user
    if not REPORTLAB_AVAILABLE:
        return Response("ReportLab is required for PDF reports.", 500)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT t.*, b.title, u.name AS student_name
            FROM transactions t
            JOIN books b ON b.id = t.book_id
            JOIN users u ON u.id = t.student_id
            ORDER BY t.id DESC
            """
        ).fetchall()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=32, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("QR-Integrated Smart Library Management System", styles["Title"]),
        Paragraph("Transaction and fine report", styles["Heading2"]),
        Spacer(1, 12),
    ]
    data = [["ID", "Book", "Student", "Issued", "Due", "Returned", "Status", "Fine"]]
    for r in rows:
        data.append([str(r["id"]), r["title"], r["student_name"], r["issue_date"], r["due_date"], r["return_date"] or "-", r["status"], money(r["fine_amount"])])
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4d4a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8f9")]),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return Response(buffer.getvalue(), mimetype="application/pdf")


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("LIBRARY_PORT", "8000")), debug=True)
