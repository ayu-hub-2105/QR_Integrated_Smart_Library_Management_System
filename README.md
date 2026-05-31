# QR-Integrated Smart Library Management System (Flask)

Flask implementation of the QR-based library project from `Ayush.zip`.

## Features

- Admin and student login
- SQLite-backed books, users, settings, transactions, and fine payments
- QR generation for books and student IDs
- Camera-assisted QR issue/return forms where the browser supports `BarcodeDetector`
- Overdue fine calculation with configurable loan period and daily fine
- Admin dashboard, inventory, user management, transactions, fines, settings, and PDF report
- Student dashboard, catalog search, history, fines, and QR ID

## Run

```bash
pip install -r requirements.txt
python backend/app.py
```

Open `http://127.0.0.1:8000`.

## Folder Structure

```text
backend/
  app.py
frontend/
  static/
    app.js
    styles.css
database/
  schema.sql
  smart_library.db
```

## Demo Logins

```text
Admin:   admin@example.com / admin123
Student: student@example.com / student123
```
