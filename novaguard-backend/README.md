# NOVAGUARD Backend

- Run test

```bash
# Đảm bảo bạn đã cài đặt các thư viện trong requirements.txt vào virtual env của bạn
# và PYTHONPATH được thiết lập để trỏ đến thư mục gốc của backend code (thường là novaguard-backend)
# export PYTHONPATH=$(pwd):$PYTHONPATH  (chạy từ thư mục novaguard-backend)
python -m unittest discover -s tests

## OR

python -m unittest tests.core.test_config

```