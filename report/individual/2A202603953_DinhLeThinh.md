# Báo cáo cá nhân — Day 6: Data Ingestion

> Bản báo cáo đã điền theo bằng chứng trong repo. Cập nhật commit chốt sau khi tạo commit nộp bài. Các điểm coherence/grounding được AI hỗ trợ và đã được tác giả kiểm tra; đây không phải đánh giá độc lập mù.

## Thông tin học viên

- Họ tên: Đinh Lê Thịnh
- MSSV: 2A202603953
- Lớp: L3B
- Repo bài nộp: https://github.com/ThinkLee04/K5-L3B-DAY06-DinhLeThinh-2A202603953-DataIngestion
- Commit chốt bài: Chưa tạo commit chứa các thay đổi hoàn thiện lab.

## Dữ liệu và pipeline

Corpus gồm hai bài báo về hệ thống giao dịch cổ phiếu và phân tích tài chính định lượng, tổng cộng 32 trang nội dung hữu ích: 21 trang của `arxiv-2412.05297v1` (trang vật lý 2–22) và 11 trang của `arxiv-2601.19504v1` (trang 1–11). Manifest ghi nguồn, phiên bản, giấy phép, số trang, quy tắc tính trang và số ký tự chuẩn hóa. Trang tiêu đề của bài thứ nhất và các trang chỉ có tài liệu tham khảo bị loại; các trang cuối có cả nội dung và tài liệu tham khảo vẫn được tính.

PDF thứ nhất ghi quyền phân phối không độc quyền từ arXiv nhưng không phải giấy phép tái phân phối chung. Vì vậy file được giữ cục bộ và bị loại khỏi commit qua `.gitignore`; người chấm có thể truy cập URL arXiv trong manifest. PDF thứ hai mang CC BY 4.0; cần giữ ghi công và liên kết giấy phép nếu chia sẻ.

Pipeline trích xuất PDF, chuẩn hóa Unicode/NFC và khoảng trắng, gắn source/page/year/document_type/subject, chia chunk, tạo embedding thật qua OpenRouter và lưu trong PostgreSQL 16 + pgvector 0.8.7. Retrieval dùng exact cosine distance (`<=>`); metadata filter lưu trong JSONB. Model là `qwen/qwen3-embedding-8b`, 4096 chiều. Cấu hình giống nhau giữa A/B/C; chỉ C lọc năm do câu hỏi cung cấp.

## Cấu hình và benchmark

- **A:** fixed 500 ký tự, overlap 50.
- **B:** paragraph-aware, tối đa 850 ký tự.
- **C:** giống B, chỉ áp dụng year filter khi câu hỏi nêu năm.
- Benchmark hiện tại có 20 câu: 15 dev và 5 test mới F01–F05; trong đó 3 câu test yêu cầu lọc metadata. Gold source/page/evidence được kiểm tra bằng validator.
- Bộ test cũ đã bị xem trước khi khóa cấu hình nên được lưu riêng trong `starter/benchmark/exploratory_test_questions.json` và không được báo như holdout độc lập. Tiêu chí chọn đã ghi trước khi chạy test mới: ưu tiên Evidence Hit@3 dev, sau đó Hit@1 dev, rồi số chunk/chi phí. Trên lần thăm dò, A/B/C hòa Evidence Hit@3 (9/15), A thắng tie-break Hit@1 (8/15 so với 7/15 của B và C). Vì vậy A được chọn trước lần chạy test mới; không thay lựa chọn sau khi xem test.

## Kết quả retrieval

Run cuối: `20261114T042842Z`, PostgreSQL + pgvector, OpenRouter Qwen3 Embedding 8B. Ba chiến lược chạy trên cùng corpus và benchmark.

| Strategy | Split | Hit@1 | Evidence Hit@3 | Số chunk |
|---|---|---:|---:|---:|
| A | Dev | 8/15 (53.3%) | 10/15 (66.7%) | 196 |
| A | Test | 4/5 (80.0%) | 4/5 (80.0%) | 196 |
| B | Dev | 10/15 (66.7%) | 12/15 (80.0%) | 125 |
| B | Test | 2/5 (40.0%) | 4/5 (80.0%) | 125 |
| C | Dev | 10/15 (66.7%) | 12/15 (80.0%) | 125 |
| C | Test | 3/5 (60.0%) | 5/5 (100.0%) | 125 |

Ba câu test có filter đều tuân thủ: C đạt 3/3 trên test và 3/3 trên dev. A/B không áp dụng filter theo thiết kế. C tốt nhất trên test nhỏ này, nhưng A vẫn là lựa chọn theo tiêu chí đã khóa trước test; không dùng test để điều chỉnh hay đổi quyết định. Kết quả dev khác lần thăm dò dù cùng cấu hình được khai báo. Điều này có thể liên quan tới route/backend embedding từ xa thay đổi, nhưng nguyên nhân chưa được xác minh; không diễn giải chênh lệch như tác động chắc chắn của cấu hình.

Run mất 1.416 giây để ingestion, 0.010 giây chunking và 122.04 giây cho embedding/storage/retrieval. Provider báo 40,500 token embedding. Chi phí ước tính là **$0.000405** theo mức $0.01/triệu token [OpenRouter pricing](https://openrouter.ai/qwen/qwen3-embedding-8b/pricing); đối chiếu hóa đơn OpenRouter trước khi báo chi phí chính thức. Đây là tổng của cả run, không chia theo từng chiến lược.

## Audit coherence, metadata và grounding

Các nhãn dưới đây là **đánh giá có AI hỗ trợ và đã được tác giả kiểm tra**. Đây không phải audit độc lập mù. Các CSV tương ứng lưu sample, top-3 evidence, lý do và ghi chú kiểm tra.

| Strategy | Coherence trung bình /2 (10 vị trí) | Metadata source/page/year đúng (45 bản ghi) | Filter test | Grounding test (0/1/2; trung bình /2) |
|---|---:|---:|---:|---|
| A | 1.0 | 45/45 | N/A | 0:0, 1:1, 2:4; trung bình 1.8 |
| B | 1.4 | 45/45 | N/A | 0:1, 1:0, 2:4; trung bình 1.6 |
| C | 1.4 | 45/45 | 3/3 | 0:0, 1:0, 2:5; trung bình 2.0 |

Coherence sử dụng cùng 10 vị trí giữa ba cấu hình, chọn trên 10 trang nội dung đầu của nguồn 2024 trước khi đối chiếu chiến lược. Mỗi sample/strategy có một chunk đại diện được chấm; các chunk còn lại trong CSV không được tính điểm. Metadata source và page giúp truy vết bằng chứng, year cho phép thu hẹp corpus theo năm. Kiểm tra 45/45 là so khớp metadata trong trace với manifest; tác giả vẫn cần spot-check trang nguồn. Filter chỉ có giá trị khi năm được nêu rõ trong câu hỏi; corpus ít nguồn/năm nên chưa đo được khả năng lọc tinh hơn.

Grounding chấm mức độ top-3 cung cấp bằng chứng để trả lời câu hỏi, không phải độ chính xác của câu trả lời do LLM tạo. Nhãn 0–2 trong CSV là đánh giá có AI hỗ trợ đã được tác giả kiểm tra, với lý do theo từng câu.

## Phân tích và giới hạn

- A có nhiều chunk hơn B/C (196 so với 125), nhưng trong lần chạy cuối có Evidence Hit@3 dev thấp hơn. B/C ngang nhau trên dev; C đạt 5/5 Evidence Hit@3 trên test mới, trong khi B đạt 4/5. Chênh lệch test chỉ dựa trên 5 câu nên không đủ để khái quát.
- Ví dụ lỗi: **F03**, A chỉ lấy được đoạn nói về tính module hóa nhưng không chứng minh số lượng tám module; B không lấy đúng đoạn tổng quan hệ thống; C lấy được bằng chứng phù hợp sau lọc năm. **F04**, B/C xếp đúng phần future work ở top-1; A cũng đưa đoạn chứa danh sách nền tảng vào top-3. Chi tiết theo từng chunk ở `manual_grounding_template.csv` và `traces.jsonl`.
- Metadata đúng trong trace không đồng nghĩa mọi câu trả lời đúng; source/page/year xác nhận provenance và filter, còn chất lượng ngữ nghĩa phụ thuộc chunk/retrieval. OCR/text layout của PDF và đoạn/bảng có thể ảnh hưởng coherence.
- Corpus đạt ngưỡng trang theo audit nhưng chỉ có hai bài báo và chỉ hai giá trị year. Thời gian/chi phí đo trên một lần chạy, thời gian mạng phụ thuộc provider. Embedding backend từ xa có thể thay đổi; kết quả chưa được lặp lại để ước lượng độ dao động.
- Có thể nhờ người chấm độc lập xác nhận nhãn coherence/grounding; metadata cũng cần được spot-check với trang gốc. Nên bổ sung nguồn có nhiều giá trị filter và ghi lại endpoint/version thực tế của provider nếu có.

## Tái lập

Từ thư mục `starter/`, cấu hình `.env` cục bộ và khởi động Docker Compose/PostgreSQL như trong README. Lệnh kiểm tra benchmark: `python scripts/validate_benchmark.py`. Lệnh chạy pipeline: `python -m src.run`. Kết quả tham chiếu và audit nằm trong `starter/results/runs/20261114T042842Z/`; run artifacts chứa nội dung trích từ tài liệu, nên cần giữ quyền truy cập phù hợp. Log thí nghiệm ở `artifacts/experiment_log.csv`.
