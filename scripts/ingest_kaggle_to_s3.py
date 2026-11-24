import json
import os
import subprocess
import tempfile
from pathlib import Path

import boto3


def get_env_required(name: str) -> str:
    """必須の環境変数を取得する。未設定または空文字の場合は即座にエラーとする。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"必須の環境変数 '{name}' が設定されていません。")
    return value


def get_kaggle_credentials(secret_id: str, region_name: str) -> dict[str, str]:
    """AWS Secrets Manager から Kaggle の認証情報（username, key）を取得する。"""
    # 指定リージョンの Secrets Manager を操作するクライアントを生成
    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_id)
    # ARN等のメタ情報を除いたシークレット文字列を取得
    # 取得例: '{"username": "your_kaggle_user", "key": "your_kaggle_api_key"}'
    secret_string = response.get("SecretString")
    if not secret_string:
        raise RuntimeError(f"シークレット '{secret_id}' に SecretString が存在しません。")

    # JSON文字列をPythonの辞書(dict)に変換し、username と key を取り出せるようにする
    creds = json.loads(secret_string)
    if not creds.get("username") or not creds.get("key"):
        raise RuntimeError("シークレット JSON に 'username' または 'key' が含まれていません。")
    return creds


def download_to_dir(dataset: str, dest_dir: Path, creds: dict[str, str]) -> None:
    """一時ディレクトリへ Kaggle データセットをダウンロード・展開する。"""
    # kaggle コマンドを解決するための PATH環境変数が必要なので、コピーする
    env = os.environ.copy()
    env["KAGGLE_USERNAME"] = creds["username"]
    env["KAGGLE_KEY"] = creds["key"]

    subprocess.run(
        [
            "kaggle",      # 実行するCLIツール名
            "datasets",    # 操作対象（データセット）
            "download",    # 実行するアクション（ダウンロードする）
            "-d",          # オプション：対象データセットを指定するフラグ
            dataset,       # ダウンロードするデータセット名（例: "owner/dataset-name"）
            "-p",          # オプション：保存先ディレクトリを指定するフラグ
            str(dest_dir), # 保存先フォルダのパス
            "--unzip",     # オプション：ダウンロード後にZIPを自動解凍するフラグ
        ],
        env=env,
        check=True,  # kaggle コマンドが失敗したら例外を発生させ、後続の S3 アップロード等に進まないようにする
    )


def upload_files_to_s3(
    source_dir: Path, bucket: str, prefix: str, region_name: str
) -> int:
    """一時ディレクトリ配下のファイルを S3 へアップロードし、アップロード件数を返す。"""
    # S3 と連絡を取ってアップロード等の手続きを代行してくれる専任の窓口（S3 クライアント）を作成
    s3 = boto3.client("s3", region_name=region_name)
    uploaded_count = 0
    clean_prefix = prefix.strip("/")

    # 一時ディレクトリ配下を再帰的にすべて探し、名前順に並べて順に取り出す（フォルダも混ざるため次行でファイルに絞る）
    for file_path in sorted(source_dir.rglob("*")):
        if file_path.is_file():
            # 一時フォルダのパスを削り、配下のパスを / 区切りで取り出す（例: /tmp/xxx/sub/data.csv -> sub/data.csv）
            rel_path = file_path.relative_to(source_dir).as_posix()
            s3_key = f"{clean_prefix}/{rel_path}" if clean_prefix else rel_path
            s3.upload_file(str(file_path), bucket, s3_key)
            print(f"Uploaded: s3://{bucket}/{s3_key}")
            uploaded_count += 1

    if uploaded_count == 0:
        raise RuntimeError("ダウンロード・展開後のファイルが見つかりませんでした。")
    return uploaded_count


def get_temp_base_dir() -> Path | None:
    """一時ディレクトリの親パスを環境変数から取得する（未設定なら None）。"""
    # WSL2 の /tmp (tmpfs) 容量上限を避けるため、環境変数でディスク上の作業先を指定できるようにする
    # .strip() は、文字列の両端（先頭と末尾）にある空白文字（半角スペース、タブ、改行 \n など）を取り除くメソッド
    base_dir_str = os.environ.get("WORK_TMP_DIR", "").strip()
    if not base_dir_str:
        return None
    base_dir = Path(base_dir_str).expanduser()
    # 親ディレクトリが存在しない場合でも作成できるようにする
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def main() -> None:
    secret_id = get_env_required("SECRET_ID")
    s3_bucket = get_env_required("S3_BUCKET")

    aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-1"
    dataset = os.environ.get(
        "KAGGLE_DATASET",
        "retailrocket/ecommerce-dataset",
    )
    # S3バケット(s3_bucket)配下の格納先プレフィックス（例: s3://<bucket>/raw/kaggle/ecommerce-dataset/<file>）
    s3_prefix = "raw/kaggle/ecommerce-dataset"
    temp_base_dir = get_temp_base_dir()

    # Secrets ManagerからKaggle認証情報(username, key)を取得し、辞書形式で受け取る
    creds = get_kaggle_credentials(secret_id, aws_region)

    # ダウンロード・展開用の作業ディレクトリを一時作成（with ブロック終了時に中身ごと自動削除）
    with tempfile.TemporaryDirectory(dir=temp_base_dir) as tmp_dir:
        # 一時ディレクトリの文字列パスを Path オブジェクト化（/ でのパス結合やファイル検索をしやすくする）
        tmp_path = Path(tmp_dir)
        download_to_dir(dataset, tmp_path, creds)
        count = upload_files_to_s3(tmp_path, s3_bucket, s3_prefix, aws_region)

    print(f"完了: {count} ファイルを s3://{s3_bucket}/{s3_prefix.strip('/')} へアップロードしました。")


if __name__ == "__main__":
    main()