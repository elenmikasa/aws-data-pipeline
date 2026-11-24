FROM python:3.12-slim

# コンテナ実行時・ビルド後に有効となる環境変数を設定する
# - PYTHONUNBUFFERED=1: ログをバッファリングせず標準出力へ即時出力する（CloudWatch Logs 向け）
# - PIP_NO_CACHE_DIR=1: pip インストール時にキャッシュを残さずイメージサイズを抑える
# - WORK_TMP_DIR=/tmp/work: 一時作業ディレクトリの親パスを指定する
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORK_TMP_DIR=/tmp/work

WORKDIR /app

# 依存ライブラリをインストール
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 取込スクリプトを配置
COPY scripts/ ./scripts/

# 一時作業用ディレクトリを作成
RUN mkdir -p "${WORK_TMP_DIR}"

CMD ["python", "scripts/ingest_kaggle_to_s3.py"]