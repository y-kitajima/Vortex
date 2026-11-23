# =============================================================================
# Step 1: 最小のRSSリーダー
# =============================================================================
# 
# このプログラムの目的:
#   1つのRSSフィードを取得して、記事のタイトルを画面に表示する
#
# 使い方:
#   1. まず、必要なライブラリをインストールします:
#      py -m pip install -r requirements.txt
#
#   2. このファイルを実行します:
#      py step1_rss_reader.py
#
#   3. 画面に記事の一覧が表示されたら成功です！
#
# =============================================================================

# --- 1行目の説明 ---
# feedparser は、RSSフィード（XML形式のデータ）を読み込むための外部ライブラリです。
# Pythonには標準では入っていないので、pipでインストールする必要があります。
import feedparser

# --- 定数の定義 ---
# URLを何度も書くのは面倒なので、変数に入れておきます。
# ここでは例として Gigazine のRSSフィードを使っています（ITニュースサイト）。
# 他のサイトのRSSに変えてみるのもおすすめです。
RSS_URL = "https://gigazine.net/news/rss_2.0/"


# --- メインの処理 ---
def main():
    """
    このプログラムのメイン処理。
    1. RSSフィードを取得する
    2. 記事データを解析する
    3. 画面に表示する
    """
    
    # --- 見出しを表示 ---
    print("=" * 50)
    print("  Step 1: 最小のRSSリーダー")
    print("=" * 50)
    print()
    print(f"取得中のRSS: {RSS_URL}")
    print("-" * 50)
    print()
    
    # --- RSSフィードを取得 ---
    # feedparser.parse() にURLを渡すだけで、自動的にHTTPリクエストを送って
    # XMLを解析し、Pythonで扱いやすいデータ構造に変換してくれます。
    # 
    # 戻り値の parsed には、以下のようなデータが入っています:
    #   parsed.feed     - フィード全体の情報（サイト名、説明など）
    #   parsed.entries  - 記事のリスト（1記事 = 1つの辞書）
    parsed = feedparser.parse(RSS_URL)
    
    # --- フィード全体の情報を表示 ---
    # parsed.feed は辞書型（dict）で、サイトの情報が格納されています。
    # .get() を使うと、キーが存在しなくてもエラーにならず None を返してくれます。
    feed_title = parsed.feed.get("title", "タイトル不明")
    print(f"【サイト名】{feed_title}")
    print()
    
    # --- 各記事の情報を表示 ---
    # parsed.entries は記事のリストになっています。
    # 1つ1つが辞書型で、以下のようなキーを持っています:
    #   - title: 記事のタイトル
    #   - link: 記事のURL
    #   - summary: 記事の要約（存在しない場合もある）
    #   - published: 公開日時（存在しない場合もある）
    
    print("【新着記事】")
    print()
    
    # enumerate() を使うと、インデックス番号と中身を同時に取り出せます。
    # 第2引数の start=1 で「1番」から始まるようにしています。
    for i, entry in enumerate(parsed.entries, start=1):
        # 記事のタイトルとURLを取得
        title = entry.get("title", "タイトルなし")
        link = entry.get("link", "")
        
        # 画面に表示
        print(f"  {i}. {title}")
        print(f"     {link}")
        print()
    
    # --- 総件数の表示 ---
    # len() でリストの要素数（=記事数）を取得できます。
    total = len(parsed.entries)
    print("-" * 50)
    print(f"合計 {total} 件の記事を取得しました。")
    print()
    print("Step 1 完了！おめでとうございます！")


# --- プログラムのエントリポイント ---
# このおまじないは、「このファイルが直接実行されたときだけ main() を動かす」
# という意味です。他のファイルから import されたときには自動実行されません。
# Python ではよく見るパターンなので、覚えておくと便利です。
if __name__ == "__main__":
    main()
