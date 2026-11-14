# Tiêu chí chốt cấu hình

Ghi trước khi đánh giá bộ test mới. Lần chạy đầu đã xem bộ test cũ trước khi chốt cấu hình; bộ đó được lưu riêng trong `exploratory_test_questions.json` và không dùng làm holdout cuối.

1. Ưu tiên Evidence Hit@3 trên dev.
2. Nếu bằng nhau, ưu tiên Hit@1 trên dev.
3. Nếu vẫn bằng, ưu tiên cấu hình ít chunk hơn và chi phí thấp hơn.

Trong lần chạy thăm dò, cả A/B/C đạt Evidence Hit@3 = 9/15. Hit@1 lần lượt là A 8/15, B 7/15, C 7/15. Vì vậy chọn **A (fixed 500 ký tự, overlap 50)** trước khi chạy bộ test mới. Cấu hình B và C giữ nguyên để làm đối chứng; không điều chỉnh tham số sau khi xem test mới.
