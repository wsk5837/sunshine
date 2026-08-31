import sqlite3

from app import db
from app.sqlite_postgres_migration import _dependency_order


def test_postgres_sql_translation_covers_application_sqlite_subset():
    ddl, _ = db._translate_postgres_sql("CREATE TABLE sample(id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL)")
    assert "SERIAL PRIMARY KEY" in ddl
    assert "DOUBLE PRECISION" in ddl

    insert, ignored = db._translate_postgres_sql("INSERT OR IGNORE INTO sample(name) VALUES (?)")
    assert ignored is True
    assert insert == "INSERT INTO sample(name) VALUES (%s) ON CONFLICT DO NOTHING"

    query, _ = db._translate_postgres_sql("SELECT * FROM sample WHERE name LIKE '%审批%' OR id=?")
    assert "LIKE '%%审批%%'" in query
    assert query.endswith("id=%s")


def test_database_backend_selection(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRM_DATABASE_BACKEND", raising=False)
    assert db.database_backend() == "sqlite"

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    assert db.database_backend() == "postgresql"

    monkeypatch.setenv("TRM_DATABASE_BACKEND", "sqlite")
    assert db.database_backend() == "sqlite"


def test_migration_dependency_order_places_parent_first():
    source = sqlite3.connect(":memory:")
    source.execute("PRAGMA foreign_keys=ON")
    source.executescript(
        """
        CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id));
        """
    )
    assert _dependency_order(source, ["child", "parent"]) == ["parent", "child"]
