PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    roll_no TEXT,
    role TEXT NOT NULL CHECK(role IN ('admin', 'student')),
    password_hash TEXT NOT NULL,
    qr_token TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    isbn TEXT,
    category TEXT,
    shelf_location TEXT,
    total_copies INTEGER NOT NULL DEFAULT 1,
    available_copies INTEGER NOT NULL DEFAULT 1,
    qr_token TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    student_id INTEGER NOT NULL REFERENCES users(id),
    issue_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    return_date TEXT,
    fine_amount REAL NOT NULL DEFAULT 0,
    fine_paid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('issued', 'returned')),
    created_by INTEGER REFERENCES users(id),
    returned_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS fine_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    amount REAL NOT NULL,
    paid_at TEXT NOT NULL,
    recorded_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
