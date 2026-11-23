# =============================================================================
# reader.py ― RSSリーダー本体（全体の流れを指揮するファイル）
# =============================================================================
#
# このプログラムの目的:
#   1. config.yaml のトピック（RSSのまとめ先）ごとに記事を集める
#   2. 時間窓（window_hours）と既読（seen.db）で絞り込む
#   3. 公開日が新しい順に max_items_per_topic 件まで Discord へ送信する
#   4. 「送信に成功した記事だけ」を既読として記録する
#
# 使い方:
#   1. py -m pip install -r requirements.txt
#   2. config.yaml を編集（webhook_url は空でも動く＝コンソール表示のみ）
#   3. py reader.py
#
# 【プロジェクトの構成】
#   reader.py   ... 全体の流れの指揮だけ（このファイル）
#   models.py   ... データの箱の定義（Topic / Article）
#   settings.py ... config.yaml を読み、箱に詰め替える
#   store.py    ... 既読データベース（seen.db）の読み書き
#   feeds.py    ... RSS取得と記事の正規化（Article箱に変換）
#   notifier.py ... Discord embed の組み立てと送信
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
#   （上限件数で切り捨てられた記事も同じ理屈で「未読のまま」です。
#     次回も窓内なら再挑戦、窓を過ぎれば自然と消えます）
#
# =============================================================================

# --- 自分のファイルたち（モジュール）の読み込み ---
# pipは不要です。同じフォルダの .py ファイルは名前だけで import できます。
import settings
import store
import feeds
import notifier

# requests: 送信失敗（例外）を受け止めるために、main側でも import します。
import requests

# time: 現在時刻（UNIX秒）を取得する標準モジュール。
# 「何時間以内の記事か」の窓計算に使います。
import time


# --- 定数 ---
# 1時間あたりの秒数。window_hours を秒に換算するのに使います。
SECONDS_PER_HOUR = 60 * 60


# =========================================================================
# メインの処理（ここでは「誰をどの順番で呼ぶか」だけを指揮する）
# =========================================================================


def main():
    """
    このプログラムのメイン処理。
    1. settings 経由で設定（Topicのリスト等）を読み込む
    2. store 経由でデータベースを開き、期間切れの既読記録を削除する
    3. トピックごとに feeds 経由で記事を取得し、
       窓フィルタ→既読フィルタ→新しい順ソート→上限カットする
       （フィードの取得失敗は止めずに warnings に回収して最後にまとめて表示）
    4. notifier 経由で新着をDiscordへ送信する
    5. 送信できた（or送信不要で表示まで完了した）記事だけを既読記録する
    """

    # ---------------------------------------------------------------------
    # 1. 設定を読み込む（settings.py の担当）
    # ---------------------------------------------------------------------
    cfg = settings.load()

    # ---------------------------------------------------------------------
    # 2. データベースを開き、古い既読記録を掃除する（store.py の担当）
    # ---------------------------------------------------------------------
    db_path = "seen.db"
    conn = store.open_db(db_path)
    deleted = store.remove_old_entries(conn, cfg["retention_days"])

    # ---------------------------------------------------------------------
    # 3. トピックごとに集めて・絞って・表示する
    # ---------------------------------------------------------------------

    print("=" * 60)
    print("  RSSリーダー（トピック配信版）")
    print("=" * 60)
    print()
    print(f"トピック数: {len(cfg['topics'])}件")
    print(f"既読データベース: {db_path}")
    print(f"既読の保持期間: {cfg['retention_days']}日間")
    print(f"記事の対象窓: 過去{cfg['window_hours']}時間以内")
    print(f"1トピック上限: {cfg['max_items_per_topic']}件")
    print(f"期間切れの削除: {deleted}件")
    print(f"Discord配信: {'設定済み' if cfg['webhook_url'] else '未設定（コンソール表示のみ）'}")
    print("-" * 60)
    print()

    # --- 時間窓の境界線（cutoff）の計算 ---
    # cutoff = 今 − window_hours時間（秒換算）。
    # 公開時刻がこれより前（＝古い）記事は「古すぎる」として除外します。
    now = time.time()
    cutoff = now - cfg["window_hours"] * SECONDS_PER_HOUR

    # 今回「送信候補」と確定した記事（上限カット後のArticle達）を入れるリスト。
    # 「送信してから既読記録」をしたいので、記録は後回しにします。
    new_articles = []

    # 警告を集めるリスト。
    # 取得失敗・記事0件のフィードは全体を止めず、ここに記録して最後にまとめて出します。
    warnings = []

    # 各トピックを1つずつ処理します（topic は settings が作ってくれた Topic 箱）
    for topic in cfg["topics"]:
        print(f"【{topic.label}】")

        fetched = 0        # このトピックで取得した記事の総数
        too_old = 0        # 時間窓より古かった数
        fresh = []         # 窓内で未読の記事（まだ上限は適用前）

        # トピックに紐づく全フィードを回す
        for url in topic.feeds:
            # 取得と正規化は feeds.py に任せます。
            # 戻り値は (Articleリスト, 警告 or None) のタプルです。
            articles, warning = feeds.fetch_feed(topic, url)
            if warning:
                warnings.append(warning)

            fetched += len(articles)

            for article in articles:
                # --- フィルタ1: 公開時刻の時間窓 ---
                # published は UNIX秒 or None（RSSに日付が無い場合）。
                # None は「新しめ」として通過させます。
                if article.published is not None and article.published < cutoff:
                    too_old += 1
                    continue

                # --- フィルタ2: 既読ならスキップする（store.py に聞く） ---
                if store.is_seen(conn, article.link):
                    continue

                fresh.append(article)

        # --- フィルタ3: 公開日が新しい順に並べて上限件数で切る ---
        #
        # 【sort(key=..., reverse=True) の読み方】
        #   「何を目安に並べるか」を key に渡します。
        #   ここでは各 Article から published（公開時刻）を取り出して比較します。
        #
        # 【lambda（ラムダ式）とは】
        #   「1行で書ける小さい関数」です。
        #   lambda a: a.published は「aを渡されたらa.publishedを返す」関数で、
        #   def で名前付き関数を作るほどでもない短い処理に向いています。
        #
        # 【日付無し（None）記事の扱い】
        #   published が None の記事には now（今）を代入して「最新の仲間」として
        #   先頭側に並べます。None のまま比較すると float と比較できず
        #   TypeError になるので、必ず数字に置き換えてから扱います。
        #   （これは分岐演算子で「もし〜ならA、そうでなければB」を表しています）
        fresh.sort(
            key=lambda a: a.published if a.published is not None else now,
            reverse=True,  # True = 降順（大きい＝新しいが先頭）
        )

        # [:N] は「先頭からN件」のスライス。上限件数で切ります。
        top = fresh[:cfg["max_items_per_topic"]]
        dropped = len(fresh) - len(top)   # 上限で切り捨てた数（次回も窓内なら再挑戦）

        # --- 送信候補として表示（まだ既読化しない） ---
        for i, article in enumerate(top, start=1):
            new_articles.append(article)
            print(f"  {i}. {article.source}: {article.title}")
            print(f"     {article.link}")
            print()

        # --- このトピックの集計を表示 ---
        # 残りは既読スキップ。「取得 = 窓外 + 既読 + 新着候補」なので逆算します。
        seen_skipped = fetched - too_old - len(fresh)
        print(
            f"  → 取得{fetched}件 / 新着{len(top)}件 / 窓外{too_old}件 / "
            f"既読スキップ{seen_skipped}件 / 上限カット{dropped}件"
        )
        print()
        print("-" * 60)
        print()

    total_new = len(new_articles)

    # --- 回収した警告をまとめて表示 ---
    if warnings:
        print(f"注意したソース {len(warnings)}件:")
        for w in warnings:
            print("  -", w)
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
            store.mark_seen(conn, article.link)

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
