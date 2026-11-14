# Starter · PostgreSQL + pgvector

Bộ khởi động **tùy chọn** cho lab cá nhân. Docker Compose tạo PostgreSQL + pgvector local; các file trong `src/` là **khung pseudocode có TODO**, chưa đọc tài liệu, tạo embedding, lưu vector hay chạy benchmark. Bạn cần tự hiện thực. Vẫn có thể chọn Qdrant, Chroma hoặc vector store tương đương theo [GUIDE.md](../GUIDE.md).

## Cấu trúc

```text
starter/
├── docker-compose.yml     # PostgreSQL 16 + pgvector, chỉ mở cổng local
├── init.sql               # bật extension khi tạo database mới
├── requirements.txt       # psycopg, pypdf và python-dotenv
├── .env.example           # cấu hình mẫu; copy thành .env
├── scripts/check_setup.py # kiểm tra kết nối và extension (đã chạy được)
├── src/
│   ├── models.py          # cấu trúc dữ liệu mẫu
│   ├── ingestion.py       # TODO: đọc / chuẩn hóa tài liệu
│   ├── chunking.py        # TODO: chia chunk
│   ├── embeddings.py      # TODO: chọn model API hoặc local
│   ├── store.py           # TODO: lưu vector, metadata và filter
│   ├── evaluation.py      # TODO: chấm truy xuất, lưu trace
│   └── run.py             # TODO: kết nối các bước thành pipeline
├── data/                  # bạn tự chuẩn bị corpus được phép sử dụng
└── results/               # lưu kết quả của bạn
```

## 1. Cài và kiểm tra database

Cần Docker Desktop hoặc Docker Engine + Compose, Python **3.11–3.14**, và mạng để tải image/dependencies lần đầu. Các lệnh sau chạy từ thư mục `starter/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
```

Set the OpenRouter key in the local `.env`; never commit that file. The example configuration uses Qwen3 Embedding 8B with 4096 dimensions. PostgreSQL is exposed only on localhost port 55433. Stop the service without removing its data with `docker compose -p day6_student_starter stop`.

## Run

```powershell
python scripts/validate_benchmark.py
python -m src.run
```

Each run creates `results/runs/<run_id>/` containing top-3 traces, aggregate metrics, run configuration and usage, coherence/grounding assessment sheets, and a metadata trace audit. `results/latest_run.txt` identifies the most recent run. Do not run another embedding pass unless a new experiment is intended; it sends requests to the configured provider and may incur cost.

The three strategies share corpus, model, vector store, and questions:

- **A:** fixed 500-character chunks with 50-character overlap.
- **B:** paragraph-aware chunks up to 850 characters.
- **C:** the same chunks as B, with a year filter only when the question supplies one.

Search uses exact cosine distance. `scripts/validate_benchmark.py` checks question splits and evidence. `scripts/audit_pdf_pages.py` audits source page extraction and page counts.

## Current corpus and benchmark

`data/manifest.csv` documents the two source papers and their permissions; `data/page_audit.csv` records included pages. The corpus has 32 content pages. The benchmark has 20 questions (15 dev, 5 fresh test) and includes metadata-filter questions. The previously exposed test set is archived in `benchmark/exploratory_test_questions.json` and is not the final holdout.

The 2024 paper's arXiv record does not grant a general redistribution license. Its PDF is kept locally and excluded from Git; use the source URL in the manifest to retrieve it. The 2026 paper is CC BY 4.0 and requires attribution when redistributed.

The latest completed run is `results/runs/20261114T042842Z/`. Coherence and grounding labels there are AI-assisted provisional assessments and need author review before being described as manual judgments. Metadata consistency flags are generated from the trace and manifest; spot-check source pages before submission. See the individual report draft under `../report/individual/` for run results, limitations, and remaining student details.
