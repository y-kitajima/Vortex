# =============================================================================
# RSSリーダー（既読管理 + Discord配信つき）
# =============================================================================
#
# このプログラムの目的:
#   1. config.yaml のRSSフィードから記事を集める
#   2. 既読（前回までに表示済み）の記事をスキップする
#   3. 新着記事を Discord に embed（リッチな枠）で送信する
#   4. 「送信に成功した記事だけ」を既読として記録する
#
# 使い方:
#   1. 必要なライブラリをインストールします:
#      py -m pip install -r requirements.txt
#
#   2. config.yaml の discord.webhook_url にURLを貼ります
#      （空のままなら Discord には送らず、コンソール表示だけで動きます）
#
#   3. このファイルを実行します:
#      py reader.py
#
# 【なぜ「送信してから既読記録」なのか】
#   表示した瞬間に既読登録してしまうと、その後のDiscord送信が失敗したときに
#   その記事は「二度とどこにも出ない」まま消えてしまいます。
#   そこで順番を「送信成功 → その後で既読記録」にします。
#   失敗した場合は既読にならないので、次回の実行で再挑戦されます。
#   （この考え方を「少なくとも1回届ける（at-least-once）」と呼ぶことがあります）
#
# =============================================================================

# --- 外部ライブラリ・標準モジュールの読み込み ---

# sqlite3: SQLiteデータベースを扱うモジュール。Pythonに標準で付属しています。
import sqlite3

# time: 現在時刻を取得する標準モジュール。「記録した瞬間」をUNIX秒で保存するために使います。
import time

# feedparser: RSSフィード（XML形式のデータ）を読み込むライブラリ
import feedparser

# yaml: YAML形式の設定ファイルを読み込むライブラリ
import yaml

# requests: WebにHTTPリクエストを送るライブラリ。
# ここでは「DiscordのWebhook URLにPOSTする」ために使います。
import requests


# --- 定数の定義 ---

# 1日あたりの秒数。「日」を「秒」に換算するのに使います。
SECONDS_PER_DAY = 24 * 60 * 60

# Discordの仕様による上限です。これを超えると投稿がエラー(400)になるため、
# 超えないように記事を「切り分け」ます。
# 【重要】Discordの制限は「個数」だけでなく「文字数」にもあります。
#   embed 1枚の合計文字数（title + 全fieldの name + value の文字数合計）は
#   6000文字以下でなければなりません。これは「メッセージ単位」でも同じで、
#   1通に入れるembedの合計も6000文字以下です。
#   個数だけで分割すると長いタイトルで容易に突破するため、
#   「文字数を見積もりながら積み、上限に達したら次の枠へ」という作り方にしています。
MAX_FIELDS_PER_EMBED = 25      # embed 1枚に入れられる field の個数上限
MAX_EMBEDS_PER_MESSAGE = 10    # メッセージ1通に入れられる embed の枚数上限
MAX_FIELD_NAME_LENGTH = 256    # field の name（記事タイトル）の文字数上限
MAX_FIELD_VALUE_LENGTH = 1024  # field の value（URL）の文字数上限
MAX_EMBED_CHARS = 6000         # embed 1枚・メッセージ1通の合計文字数上限
CHAR_MARGIN = 100              # 上限ピッピで事故らないための安全マージン


# =========================================================================
# データベースを操作するための部品（関数）
# =========================================================================


def open_db(db_path: str) -> sqlite3.Connection:
    """
    データベースを開き、必要なテーブルが無ければ作る。

    コネクションとは:
        データベースとの「回線」のようなものです。この回線を通して
        SQL文という命令を送り、結果を受け取ります。
    """
    conn = sqlite3.connect(db_path)

    # 【マイグレーション（古い表の引っ越し）】
    # 以前のバージョンの seen テーブルは url 列しか持っていませんでした。
    # 「seen_at列が無い古い表」が残っているなら、作り直します。
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
    """
    # cutoff（境界線）= 今の時刻 − 保持日数分の秒数。
    # これより古い（小さい）seen_at の行を消します。
    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    cur = conn.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
    return cur.rowcount


def is_seen(conn: sqlite3.Connection, url: str) -> bool:
    """このURLがすでに「既読」として記録されているか調べる。"""
    row = conn.execute("SELECT 1 FROM seen WHERE url = ?", (url,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, url: str) -> None:
    """このURLを「いつ出したか」の日時付きで既読記録に追加する。"""
    conn.execute(
        "INSERT OR IGNORE INTO seen (url, seen_at) VALUES (?, ?)",
        (url, time.time()),
    )


# =========================================================================
# Discord に送信するための部品（関数）
# =========================================================================
#
# 【Webhookの仕組み】
#   DiscordのWebhook URLに対して、HTTPという通信のPOSTという手段で
#   「JSON（構造化されたテキスト）」を送ると、そのチャンネルに投稿が現れます。
#   送信データは以下のような形です:
#
#     {
#       "embeds": [                       ... embed（リッチ表示の枠）のリスト
#         {
#           "title":  "【Gigazine】",      ... 見出し
#           "color":  28470,               ... 枠の色（16進数の数字）
#           "fields": [                     ... 中身の項目リスト
#             { "name": "記事タイトル", "value": "https://..." },
#             ...
#           ]
#         }
#       ]
#     }
#
#   Pythonの辞書（dict）はこの「JSON」という形式と相性が良く、
#   requests.post(..., json=...) と渡すと自動でJSON文字列に変換してくれます。
#


def _make_embed(label: str, fields: list, part: int) -> dict:
    """
    field のリストから embed（1枚のリッチ表示）を作る。

    引数:
        label: フィード名（見出しにする）
        fields: [{"name": ..., "value": ...}, ...] のリスト
        part: 何枚目の「続き」か（0なら最初の枚）
    """
    title = f"【{label}】" if part == 0 else f"【{label}】（続き{part}）"
    return {
        "title": title,
        "color": 0x00A67D,   # 16進数の色指定（緑系）。Discordの枠の色になります
        "fields": fields,
    }


def _embed_chars(embed: dict) -> int:
    """
    embed 1枚がDiscord上で「何文字扱いされるか」の見積もりを返す。

    Discordは文字数ベースで上限判定します。
    title の文字数 + 全fieldの (name + value) の文字数の合計が、
    そのembedのサイズ感になります（ほぼ正確な目安です）。
    """
    total = len(embed["title"])
    for field in embed["fields"]:
        total += len(field["name"]) + len(field["value"])
    return total


def build_embeds(new_articles: list) -> list:
    """
    新着記事リストを、Discord embed の形に変換する。

    引数:
        new_articles: [
            {"label": "Gigazine", "title": "...", "link": "https://..."},
            ...
        ] のリスト
    戻り値:
        Discordが要求する形式の embed（辞書）のリスト

    【分割の理屈（2つの上限を両方守る）】
        記事を1件ずつ今のembedに積みながら、次のどちらかに当たったら新しいembedへ：
          a) field が 25個に達した場合
          b) 次の記事を積むと合計文字数が6000（−安全マージン）を超える場合
        同じラベルの続きには「（続きN）」と番号を付けて、読み手が区別できるようにします。
    """
    # ------------------------------------------------------------------
    # ステップ1: ラベル（フィード名）ごとに記事をまとめる
    # ------------------------------------------------------------------
    # setdefault(キー, 初期値) は「キーが無ければ初期値をセットして、
    # 既にあればそのまま」。これで「ラベル → そのラベルの記事リスト」を作ります。
    by_label = {}
    for article in new_articles:
        by_label.setdefault(article["label"], []).append(article)

    # ------------------------------------------------------------------
    # ステップ2: 文字数を見ながら embed に積み上げる
    # ------------------------------------------------------------------
    embeds = []
    for label, items in by_label.items():
        fields = []          # 今積んでいるembedのfield達
        size = len(label) + 4  # embedのタイトル【label】分の文字数を先に予約
        part = 0             # 続き番号

        for article in items:
            # Discordの個別上限（name 256 / value 1024）で先に切る。
            # [:256] は「先頭256文字で切る」スライス。空titleの保険も兼ねます。
            name = (article["title"] or "(無題)")[:MAX_FIELD_NAME_LENGTH]
            value = article["link"][:MAX_FIELD_VALUE_LENGTH]

            # 「今のembedが空でない」かつ「これ以上積むと上限オーバー」なら
            # 今のembedを確定して、新しいembedを始めます。
            if fields and (
                len(fields) >= MAX_FIELDS_PER_EMBED
                or size + len(name) + len(value) > MAX_EMBED_CHARS - CHAR_MARGIN
            ):
                embeds.append(_make_embed(label, fields, part))
                part += 1
                fields = []
                size = len(label) + 4  # 新embedのタイトル分をリセット

            fields.append({"name": name, "value": value})
            size += len(name) + len(value)

        # ループ終了時に残ったfield達もembedとして確定します。
        if fields:
            embeds.append(_make_embed(label, fields, part))

    return embeds


def send_to_discord(webhook_url: str, embeds: list) -> None:
    """
    embedのリストをDiscord Webhookへ送信する。

    【HTTP POSTとは】
        Webサーバーに「データを送り付けて処理させる」通信の方法です。
        サーバーは処理結果をステータスコードという数字で教えてくれます。
        2xx = 成功（Webhookは 204 No Content）、4xx/5xx = エラー。

    【embedは6000文字ごとに送る】
        embed自体は6000文字以内に収まるよう build_embeds で作られています。
        しかし「メッセージ1通に入れるembedの合計」も6000文字以下が必要なため、
        embedがどれくらい文字を食っているか足し算しながら、
        詰め替え可能な枚数・文字数のどちらかを超えたら送信してリセットします。
    """
    message = []  # 1通にまとめるembed達をためる箱

    for embed in embeds:
        # 今の message に次の embed を追加できるか判定します。
        current_chars = sum(_embed_chars(e) for e in message)
        if message and (
            len(message) >= MAX_EMBEDS_PER_MESSAGE
            or current_chars + _embed_chars(embed) > MAX_EMBED_CHARS
        ):
            # 追加できない → 今の message を1通として送ってリセット
            _post(webhook_url, message)
            message = []

        message.append(embed)

    # ループ終了後、送られていない残りがあります（0枚なら _post しません）。
    if message:
        _post(webhook_url, message)


def _post(webhook_url: str, embeds: list) -> None:
    """embedのリストを1通としてWebhookにPOSTする（送信の最下層の部品）。"""
    payload = {"embeds": embeds}

    # requests.post(url, json=...) がPOST送信本体です。
    # json= を渡すと辞書をJSON文字列に変換して送り、
    # 「Content-Type: application/json」というヘッダーも自動で付きます。
    # timeout=20 は「20秒応答がなければエラー」という安全装置（無限待ち受け防止）。
    resp = requests.post(webhook_url, json=payload, timeout=20)

    # raise_for_status() は、ステータスコードが4xx/5xxなら例外（HTTPError）を起こします。
    # 「失敗したのに成功と勘違いする」事故を防ぐお約束の一行です。
    # 例外が起きればこの関数はそこで停止し、main側で「送信失敗」の処理に進みます。
    resp.raise_for_status()


# =========================================================================
# メインの処理
# =========================================================================


def main():
    """
    このプログラムのメイン処理。
    1. 設定ファイルを読み込む
    2. データベースを開き、期間切れの既読記録を削除する
    3. 各フィードから「未読の記事だけ」を取り出して表示する（まだ既読化しない）
    4. 新着をDiscordへ送信する
    5. 送信できた（or送信不要で表示まで完了した）記事だけを既読記録する
    """

    # ---------------------------------------------------------------------
    # 1. 設定ファイルを読み込む
    # ---------------------------------------------------------------------

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # config は以下のような辞書になっています:
    # {
    #     "retention_days": 30,
    #     "discord": {"webhook_url": "https://discord.com/api/webhooks/..."},
    #     "feeds": [{"label": "Gigazine", "url": "https://..."}, ...]
    # }
    feeds = config["feeds"]
    retention_days = int(config.get("retention_days", 30))

    # webhook_url の取り出し。
    # config.get("discord") が無い場合は None になる可能性があるので
    # `or {}` で「無いなら空辞書」という形に正規化してから中身を取ります。
    # .strip() は文字列の前後の空白（コピペ時に紛れみやすい）を取ります。
    webhook_url = ((config.get("discord") or {}).get("webhook_url") or "").strip()

    # ---------------------------------------------------------------------
    # 2. データベースを開き、古い既読記録を掃除する
    # ---------------------------------------------------------------------

    db_path = "seen.db"
    conn = open_db(db_path)
    deleted = remove_old_entries(conn, retention_days)

    # ---------------------------------------------------------------------
    # 3. 見出しを表示
    # ---------------------------------------------------------------------

    print("=" * 60)
    print("  RSSリーダー（既読管理 + Discord配信）")
    print("=" * 60)
    print()
    print(f"読み込むフィード数: {len(feeds)}件")
    print(f"既読データベース: {db_path}")
    print(f"既読の保持期間: {retention_days}日間")
    print(f"期間切れの削除: {deleted}件")
    print(f"Discord配信: {'設定済み' if webhook_url else '未設定（コンソール表示のみ）'}")
    print("-" * 60)
    print()

    # 今回新着として扱った記事（Discordに送る元データ）を入れるリスト。
    # 「送信してから既読記録」をしたいので、記録は後回しにします。
    new_articles = []

    # 表示する新着記事の数を数える変数
    total_new = 0

    # feeds の中身を1つずつ取り出して処理します
    for feed in feeds:
        label = feed["label"]
        url = feed["url"]

        # 1つのフィードを取得（feedparserがHTTP取得とXML解析を一緒にやってくれます）
        parsed = feedparser.parse(url)

        print(f"【{label}】")

        fetched = len(parsed.entries)
        new_count = 0

        # 各記事を1件ずつチェック
        for entry in parsed.entries:
            title = entry.get("title", "タイトルなし")
            link = entry.get("link", "")

            # URLが空の記事はスキップ（既読管理のキーも出典リンクも作れないため）
            if not link:
                continue

            # すでに既読なら表示をスキップする
            if is_seen(conn, link):
                continue

            # --- 新着記事として表示 & 送信リストに追加（まだ既読化しない） ---
            new_count += 1
            total_new += 1
            print(f"  {new_count}. {title}")
            print(f"     {link}")
            print()

            # 送信まで完了したら既読化する分として保持しておく
            new_articles.append({"label": label, "title": title, "link": link})

        # --- このフィードの集計を表示 ---
        skipped = fetched - new_count
        print(f"  → 取得{fetched}件 / 新着{new_count}件 / 既読スキップ{skipped}件")
        print()
        print("-" * 60)
        print()

    # ---------------------------------------------------------------------
    # 4. Discordへ送信する
    # ---------------------------------------------------------------------

    # 「記事を既読化して良いか」のフラグです。
    # 送信成功、または送信不要（URL未設定・新着ゼロ）なら True になります。
    can_mark_read = False

    if not webhook_url:
        # 未設定は「エラー」ではなく「コンソール表示モード」という仕様です。
        # 表示まで済んだので既読化して問題ありません。
        print("Discord の webhook_url が未設定のため、送信はスキップしました。")
        can_mark_read = True
    elif total_new == 0:
        print("送信する新着がありませんでした。")
        can_mark_read = True
    else:
        try:
            # embedを組み、WebhookへPOSTします。
            embeds = build_embeds(new_articles)
            send_to_discord(webhook_url, embeds)
            print(f"Discordへ {total_new}件の新着を送信しました。")
            can_mark_read = True
        except requests.RequestException as e:
            # ネットワーク障害・URL切れ・Discord側のエラーなど、
            # requestsで起きる失敗は全部この例外になります。
            # 送信できなかった以上、既読化して記事を失うわけにはいきません。
            print(f"Discord送信に失敗したため、既読記録は更新しませんでした: {e}")
            print("→ 次の実行で同じ記事が再送候補になります。")

    # ---------------------------------------------------------------------
    # 5. 送信できた記事だけを既読記録し、データベースを閉じる
    # ---------------------------------------------------------------------

    if can_mark_read:
        for article in new_articles:
            mark_seen(conn, article["link"])

    # メモリ上のデータをファイルに書き出します（commit忘れると記録が消える）
    conn.commit()

    # データベースの回線を閉じます。開いたものは必ず閉じるのがお作法です。
    conn.close()

    print("=" * 60)
    print(f"今回の新着記事は合計 {total_new}件でした")
    print("=" * 60)


# --- プログラムのエントリポイント ---
# このおまじないは、「このファイルが直接実行されたときだけ main() を動かす」
# という意味です。他のファイルから import されたときには自動実行されません。
if __name__ == "__main__":
    main()
