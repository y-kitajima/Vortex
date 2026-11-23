# =============================================================================
# store.py ― 既読データベース（seen.db）を扱う担当
# =============================================================================
#
# 【このファイルの責務】
#   「どの記事を出したか」の記憶だけを担当する。
#   SQLiteのテーブル構造やSQL文を知る唯一のファイルなので、
#   「記憶の作り方を大きく変えたい」時も、ここだけ直せば済みます。
#
# 【他のファイルから使うには】
#   reader.py などから:
#       import store
#       conn = store.open_db("seen.db")
#       store.is_seen(conn, url)
#   のように「store.（ドット）関数名」で呼びます。
#
# 関数の引数に毎回 conn を渡しているのが目につくと思います。
# これは「データベースとの回線（conn）を誰が握るか」を明確にする方法で、
# store.py 自身が回線を持たず、使う側が回線を預ける形にしています。
# こうすると「どのファイルがDBを開いているか」がreader.pyだけ見れば分かります。
#
# =============================================================================

# sqlite3: SQLiteデータベースを扱うモジュール。Pythonに標準で付属しています。
import sqlite3

# time: 現在時刻を取得する標準モジュール。記録した瞬間をUNIX秒で保存します。
import time


# --- 定数の定義 ---
# 1日あたりの秒数。「日」を「秒」に換算するのに使います。
SECONDS_PER_DAY = 24 * 60 * 60


def open_db(db_path: str) -> sqlite3.Connection:
    """
    データベースを開き、必要なテーブルが無ければ作る。

    引数:
        db_path: データベースファイルのパス（例: "seen.db"）
    戻り値:
        データベースとの「コネクション」という接続オブジェクト。
        この回線を通してSQL文という命令を送り、結果を受け取ります。
    """
    conn = sqlite3.connect(db_path)

    # 【マイグレーション（古い表の引っ越し）】
    # 以前のバージョンの seen テーブルは url 列しか持っていませんでした。
    # 「seen_at列が無い古い表」が残っているなら、作り直します。
    # PRAGMA table_info(seen) ... SQLiteに「seenテーブルの列一覧」を聞く命令
    columns = [row[1] for row in conn.execute("PRAGMA table_info(seen)").fetchall()]
    if columns and "seen_at" not in columns:
        conn.execute("DROP TABLE seen")

    # 【テーブルの意味】
    #   seen: 既読記事の表
    #     url      ... 記事URL。PRIMARY KEY = 重複不可（同じURLを2回記録しない）
    #     seen_at  ... 記録した時刻（UNIX秒）。期間切れ削除の判断に使う
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            url TEXT PRIMARY KEY,
            seen_at REAL NOT NULL
        )
    """)

    return conn


def remove_old_entries(conn: sqlite3.Connection, retention_days: int) -> int:
    """
    保持期間より古い既読記録を削除する（データベースの肥大化防止）。

    戻り値: 削除した行数

    【考え方】
        cutoff（境界線）= 今の時刻 − 保持日数分の秒数
        これより古い（seen_atが境界線より小さい）行を消します。
    """
    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    cur = conn.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
    return cur.rowcount


def is_seen(conn: sqlite3.Connection, url: str) -> bool:
    """
    このURLがすでに「既読」として記録されているか調べる。

    【SELECTとは】
        テーブルからデータを取り出す命令です。
        fetchone()（先頭1行）が None でなければ「見つかった＝既読」です。
    """
    row = conn.execute("SELECT 1 FROM seen WHERE url = ?", (url,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, url: str) -> None:
    """
    このURLを「いつ出したか」の日時付きで既読記録に追加する。

    【INSERTとは】
        テーブルに新しい行を追加する命令です。
        OR IGNORE は「重複したら黙って無視」（PRIMARY KEYの約束との合わせ技）。
    """
    conn.execute(
        "INSERT OR IGNORE INTO seen (url, seen_at) VALUES (?, ?)",
        (url, time.time()),
    )
