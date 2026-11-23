# =============================================================================
# feeds.py ― RSSフィードの取得と記事の「正規化」を担当
# =============================================================================
#
# 【このファイルの責務】
#   RSSという外部データの形を、このアプリ共通の形の辞書に変換する。
#   外部データ（RSS）はサイトごとに形がバラバラなので、
#   「アプリ内部では常に同じ形」に揃えるゲートを1箇所に集中させます。
#   この変換を正規化（regularization / normalization）と呼びます。
#
# 【記事の共通形】
#   このアプリの中で記事は、次のキーを持つ辞書としてやり取りします:
#       {"label": "Gigazine", "title": "記事タイトル", "link": "https://..."}
#   feeds.py の外では、この形だけを信じれば良くなります。
#
# =============================================================================

# feedparser: RSSフィード（XML形式のデータ）を読み込む外部ライブラリ
import feedparser


def fetch_feed(label: str, url: str) -> list:
    """
    1つのRSSフィードを取得し、記事リストに正規化して返す。

    引数:
        label: フィードの表示名（例: "Gigazine"）。記事に焼き付けて返す
        url:   RSSフィードのURL
    戻り値:
        [ {"label": ..., "title": ..., "link": ...}, ... ]

    【feedparserの仕組み】
        feedparser.parse(url) はURLを渡すだけで
        1. HTTPリクエストでXMLを取得し
        2. XMLを解析し
        3. parsed.entries（記事のリスト）にまとめてくれます。
        各 entry は辞書型で、title / link などのキーを持っています。
    """
    parsed = feedparser.parse(url)

    articles = []
    for entry in parsed.entries:
        # entry.get("キー", 既定値) で取り出す。
        # RSSによっては title が無かったり link が無かったりするので、
        # ここで「無い場合」を吸収しておくのが正規化の仕事です。
        title = entry.get("title", "タイトルなし")
        link = (entry.get("link") or "").strip()

        # URLが空の記事はスキップ。
        # 理由: linkは「既読管理のキー」かつ「Discordに出すリンク」なので、
        # これが無い記事はこのアプリで何も扱えません。
        if not link:
            continue

        articles.append({"label": label, "title": title, "link": link})

    return articles
