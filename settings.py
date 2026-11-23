# =============================================================================
# settings.py ― 設定ファイル（config.yaml）を読み込む担当
# =============================================================================
#
# 【このファイルの責務（役割）】
#   「config.yaml を読んで、他の部品がそのまま使える形の辞書にする」ことだけ。
#
# ファイルを分割する原則は「変更する理由が同じものは同じファイル」です。
# フィードのURLを変えたり保持日数を変えたりするのは「設定の変化」なので、
# その変化に対応するのはこのファイルだけ、という作りにしています。
#
# 【他のファイルから使うには】
#   同じフォルダの.pyファイルは、pipなしで名前だけで import できます。
#   例えば reader.py からは次のように書けます:
#       import settings
#       cfg = settings.load()
#   「settings.（ドット）load」は「settings.py の中の load 関数」という意味です。
#   モジュール名がそのまま名前空間（名前の苗字）になるので、
#   どこ出身の関数なのか一目で分かる利点があります。
#
# =============================================================================

# yaml: YAML形式の設定ファイルを読み込む外部ライブラリ
import yaml


def load(path: str = "config.yaml") -> dict:
    """
    config.yaml を読み、正規化済みの設定辞書を返す。

    引数:
        path: 設定ファイルの場所（既定では "config.yaml"）
    戻り値:
        {
            "feeds": [{"label": ..., "url": ...}, ...],  ... フィード一覧
            "retention_days": 30,                         ... 既読保持日数（整数保証）
            "webhook_url": "https://...",                 ... 前後空白なしの文字列保証
        }

    【この関数の価値は「正規化（せいしか）」】
        YAMLをそのまま読むと「キーが無い」「None が混じる」「文字列に空白が付く」
        といった揺らぎがあります。それをここで吸収して、
        「returnされた辞書は形が約束通りである」という状態にします。
        こうすれば後続の他のファイル（store, feeds, notifier）は
        「無い場合」を一切心配しなくて済みます。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # raw は例えば次のような辞書です（YAMLの構造がそのままPythonになります）:
    # {
    #     "retention_days": 30,
    #     "discord": {"webhook_url": "https://discord.com/api/webhooks/..."},
    #     "feeds": [{"label": "Gigazine", "url": "https://..."}, ...]
    # }

    # raw.get("キー", 既定値) は「キーが無ければ既定値を使う」取り出し方。
    # int(...) は「"30"のような文字列でも整数 30 に揃える」変換です。
    retention_days = int(raw.get("retention_days", 30))

    # config.get("discord") が無い場合は None になる可能性があるので、
    # `or {}` で「無いなら空辞書」に正規化してから中身を取ります。
    # .strip() は文字列の前後の空白（URLコピペで紛れみやすい）を取ります。
    webhook_url = ((raw.get("discord") or {}).get("webhook_url") or "").strip()

    # feeds は「無いとアプリが始まらない」必須項目なので、
    # 普通に [] で取得します。無ければ KeyError で即座にクラッシュし、
    # 「config.yaml の feeds が壊れている」という原因がすぐ分かります。
    feeds = raw["feeds"]

    # 正規化 결과를まとめて返します。以降のコードはこの辞書だけを見ます。
    return {
        "feeds": feeds,
        "retention_days": retention_days,
        "webhook_url": webhook_url,
    }
