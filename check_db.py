import sqlite3, pathlib

# DBファイルの場所を確認
db = pathlib.Path("instance/app.sqlite").resolve()
print("DB path ->", db)
print("ファイルの有無:", db.exists())

con = sqlite3.connect(db)
cur = con.cursor()

# テーブル一覧を表示
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)

# usersテーブルの件数
if "users" in tables:
    cnt = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print("users count =", cnt)

    # 登録されているユーザー情報を表示
    print("\n--- users table rows ---")
    for row in cur.execute("SELECT id, email, name, role FROM users"):
        print(row)
else:
    print("users テーブルが存在しません")

con.close()
