# upgrade_users_add_columns.py
import os, sqlite3, sys
DB_PATH = os.environ.get("SQLITE_PATH", os.path.join("instance","app.sqlite"))

def has_column(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())

def add_column(cur, table, col, coltype, default=None):
    if has_column(cur, table, col):
        print(f"[SKIP] {table}.{col} は既に存在します。"); return
    sql = f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"
    if default is not None:
        sql += f" DEFAULT {default}"
    print(f"[ALTER] {sql}")
    cur.execute(sql)

def ensure_unique_index(cur, table, col, idx_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",(idx_name,))
    if cur.fetchone():
        print(f"[SKIP] index {idx_name} は既に存在します。"); return
    sql = f"CREATE UNIQUE INDEX {idx_name} ON {table}({col})"
    print(f"[CREATE] {sql}")
    cur.execute(sql)

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] DBが見つかりません: {DB_PATH}")
        sys.exit(1)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        add_column(cur, "users", "role", "TEXT", "'staff'")
        add_column(cur, "users", "is_active", "INTEGER", 1)
        add_column(cur, "users", "email_verified", "INTEGER", 0)
        add_column(cur, "users", "created_at", "TEXT", None)
        ensure_unique_index(cur, "users", "email", "idx_users_email_unique")
        conn.commit()

    print("[DONE] users テーブルの列追加を完了しました。")
