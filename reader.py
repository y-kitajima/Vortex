# =============================================================================
# reader.py ― RSSリーダー本体（全体の流れを指揮するファイル）
# =============================================================================
#
# このプログラムの目的:
#   1. config.yaml のRSSフィードから記事を集める
#   2. 既読（前回までに表示済み）の記事をスキップする
#   3. 新着記事を Discord に embed（リッチな枠）で送信する
#   4. 「送信に成功した記事だけ」を既読として記録する
#
# 使い方:
#   1. py -m pip install -r requirements.txt
#   2. config.yaml を編集（webhook_url は空でも動く＝コンソール表示のみ）
#   3. py reader.py
#
# 【プロジェクトの構成（分割後）】
#   reader.py   ... 全体の流れの指揮だけ（このファイル）
#   settings.py ... config.yaml を読む・正規化する
#   store.py    ... 既読データベース（seen.db）の読み書き
#   feeds.py    ... RSS取得と記事の正規化
#   notifier.py ... Discord embed の組み立てと送信
#
#   分割の原則は「変更する理由が同じものは同じファイル」。
#   RSSの取得方法を変えたくなったら feeds.py だけ、
#   Discordの仕様が変わったら notifier.py だけ を直せば済みます。
#   reader.py は各担当を「順番に呼ぶ指揮者」なので、
#   アプリの全体像は常にこのファイルを読めば分かります。
#
#   ※実行すると __pycache__ というフォルダが作されます。
#   Pythonが import の結果を高速化用にキャッシュする仕組みで、
#   勝手に作られるものなので気にしなくてOKです（Gitには入れません）。
#
# 【なぜ「送信してから既読記録」なのか】
#   表示した瞬間に既読登録してしまうと、その後のDiscord送信が失敗したときに
#   その記事は「二度とどこにも出ない」まま消えてしまいます。
#   そこで順番を「送信成功 → その後で既読記録」にします。
#   失敗した場合は既読にならないので、次回の実行で再挑戦されます。
#   （この考え方を「少なくとも1回届ける（at-least-once）」と呼ぶことがあります）
#
# =============================================================================

# --- 自分のファイルたち（モジュール）の読み込み ---
# pipは不要です。同じフォルダの .py ファイルは名前だけで import できます。
# 「settingsのloadを呼ぶ」→ settings.load() という読み方になります。
import settings

# 「storeのopen_dbを呼ぶ」→ store.open_db()
import store

# feeds.py の fetch_feed（RSS取得）を使います。
# （config.yaml の "feeds" というキー名と名前が被りますが、
#   モジュールとしての feeds と辞書のキーは別物なので問題ありません）
import feeds

# Discordへの組み立て・送信は notifier が担当です。
# notifier_build_embeds という長い名を避けて、
# 「notifier.build_embeds()」とドット付きで呼ぶ方式にしています。
import notifier

# requests: 送信失敗（例外）を受け止めるために、main側でも import します。
# 「どの例外型を拾うか」はmainが知っておく必要があるためです。
import requests


# =========================================================================
# メインの処理（ここでは「誰をどの順番で呼ぶか」だけを指揮する）
# =========================================================================


def main():
    """
    このプログラムのメイン処理。
    1. settings 経由で設定を読み込む
    2. store 経由でデータベースを開き、期間切れの既読記録を削除する
    3. feeds 経由で各フィードから「未読の記事だけ」を取り出して表示する
    4. notifier 経由で新着をDiscordへ送信する
    5. 送信できた（or送信不要で表示まで完了した）記事だけを既読記録する
    """

    # ---------------------------------------------------------------------
    # 1. 設定を読み込む（settings.py の担当）
    # ---------------------------------------------------------------------
    # load() が返す辞書は形が保証されています:
    # { "feeds": [...], "retention_days": 30, "webhook_url": "https://..." }
    cfg = settings.load()

    # ---------------------------------------------------------------------
    # 2. データベースを開き、古い既読記録を掃除する（store.py の担当）
    # ---------------------------------------------------------------------
    db_path = "seen.db"
    conn = store.open_db(db_path)
    deleted = store.remove_old_entries(conn, cfg["retention_days"])

    # ---------------------------------------------------------------------
    # 3. 各フィードから新着を取り出して表示する（feeds.py + store.py）
    # ---------------------------------------------------------------------

    print("=" * 60)
    print("  RSSリーダー（既読管理 + Discord配信）")
    print("=" * 60)
    print()
    print(f"読み込むフィード数: {len(cfg['feeds'])}件")
    print(f"既読データベース: {db_path}")
    print(f"既読の保持期間: {cfg['retention_days']}日間")
    print(f"期間切れの削除: {deleted}件")
    print(f"Discord配信: {'設定済み' if cfg['webhook_url'] else '未設定（コンソール表示のみ）'}")
    print("-" * 60)
    print()

    # 今回新着として扱った記事（Discordに送る元データ）を入れるリスト。
    # 「送信してから既読記録」をしたいので、記録は後回しにします。
    new_articles = []

    # 表示する新着記事の数を数える変数
    total_new = 0

    # 各フィードを1つずつ処理します
    for feed in cfg["feeds"]:
        # RSS取得と正規化は feeds.py に任せます。
        # ここでは「label/title/link を持つ辞書のリスト」が返ってくることだけ相信します。
        articles = feeds.fetch_feed(feed["label"], feed["url"])

        label = feed["label"]
        print(f"【{label}】")

        fetched = len(articles)
        new_count = 0

        # 各記事を1件ずつチェック
        for article in articles:
            # すでに既読なら表示をスキップする（store.py に聞く）
            if store.is_seen(conn, article["link"]):
                continue

            # --- 新着記事として表示 & 送信リストに追加（まだ既読化しない） ---
            new_count += 1
            total_new += 1
            print(f"  {new_count}. {article['title']}")
            print(f"     {article['link']}")
            print()

            # 送信まで完了したら既読化する分として保持しておく
            new_articles.append(article)

        # --- このフィードの集計を表示 ---
        skipped = fetched - new_count
        print(f"  → 取得{fetched}件 / 新着{new_count}件 / 既読スキップ{skipped}件")
        print()
        print("-" * 60)
        print()

    # ---------------------------------------------------------------------
    # 4. Discordへ送信する（notifier.py の担当）
    # ---------------------------------------------------------------------

    # 「記事を既読化して良いか」のフラグです。
    # 送信成功、または送信不要（URL未設定・新着ゼロ）なら True になります。
    can_mark_read = False

    if not cfg["webhook_url"]:
        # 未設定は「エラー」ではなく「コンソール表示モード」という仕様です。
        # 表示まで済んだので既読化して問題ありません。
        print("Discord の webhook_url が未設定のため、送信はスキップしました。")
        can_mark_read = True
    elif total_new == 0:
        print("送信する新着がありませんでした。")
        can_mark_read = True
    else:
        try:
            # embedへの組み立てと送信、どちらも notifier の仕事です。
            embeds = notifier.build_embeds(new_articles)
            notifier.send_to_discord(cfg["webhook_url"], embeds)
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
            store.mark_seen(conn, article["link"])

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
