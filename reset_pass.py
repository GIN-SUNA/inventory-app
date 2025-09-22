import sqlite3, pathlib
from werkzeug.security import generate_password_hash

# 書き換えたいユーザーのメールアドレス
EMAIL = "owner@example.com"   # ← 確認したメールに置き換える
NEW_PASS = "pass1234"   # ← 好きな新パスワード

db = pathlib.Path("instance/app.sqlite").resolve()
con = sqlite3.connect(db)
cur = con.cursor()

cur.execute("UPDATE users SET password_hash=? WHERE email=?",
            (generate_password_hash(NEW_PASS), EMAIL))
con.commit()
con.close()

print(f"パスワードを {EMAIL} のユーザーに再設定しました。新しいパスワードは {NEW_PASS} です。")
