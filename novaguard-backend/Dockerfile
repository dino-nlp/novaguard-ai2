# Sử dụng một Python base image chính thức
FROM python:3.11-slim

# Thiết lập thư mục làm việc trong container
WORKDIR /app

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Sao chép file requirements vào thư mục làm việc
COPY ./requirements.txt /app/requirements.txt

# Cài đặt các Python dependencies
RUN pip uninstall tree-sitter tree-sitter-languages
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép toàn bộ code của thư mục hiện tại (novaguard-backend) vào /app trong container
COPY . /app

# Expose port mà FastAPI sẽ chạy (ví dụ: 8000)
EXPOSE 8000

# Lệnh để chạy ứng dụng FastAPI khi container khởi động
# Sử dụng uvicorn, trỏ đến đối tượng app trong app.main
# --host 0.0.0.0 để chấp nhận kết nối từ bên ngoài container
# --port 8000 là port bên trong container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]