# NovaGuard AI

### Setup env

- Cài đặt các docker image cần thiết

```bash
docker-compose up -d postgres_db zookeeper kafka ollama
```

- Kiểm tra logs của chúng để đảm bảo không có lỗi:

```bash
docker-compose logs postgres_db
docker-compose logs kafka
docker-compose logs ollama
```

- Tải ollama model

```bash
docker-compose exec ollama ollama pull codellama:7b-instruct-q4_K_M
```

- Khởi tạo database

```bash
cat novaguard-backend/database/schema.sql | docker-compose exec -T postgres_db psql -U novaguard_user -d novaguard_db
```

- Run backend

```bash
docker-compose up -d --build novaguard_backend_api
# Hoặc nếu muốn rebuild tất cả và chạy:
# docker-compose up -d --build
```

- Theo dõi log của 1 container:

```bash
docker-compose logs -f novaguard_backend_api
```

Test APIs tại: http://localhost:8000/docs

- Xóa DB cũ để chạy lại sau khi có thay đổi

```bash
# Kết nối vào psql trong container
docker-compose exec postgres_db psql -U novaguard_user -d novaguard_db
# Bên trong psql:
DROP TABLE IF EXISTS "AnalysisFindings" CASCADE;
DROP TABLE IF EXISTS "PRAnalysisRequests" CASCADE;
DROP TABLE IF EXISTS "Projects" CASCADE;
DROP TABLE IF EXISTS "Users" CASCADE;
\q 
# Sau đó, áp dụng lại schema
cat novaguard-backend/database/schema.sql | docker-compose exec -T postgres_db psql -U novaguard_user -d novaguard_db
```

## Thiết lập Webhook GitHub cho Môi trường Nội bộ

Để NovaGuard-AI có thể tự động phân tích Pull Request, bạn cần cấu hình webhook trên repository GitHub (hoặc GitHub Enterprise Server - GHES) của bạn để gửi sự kiện đến NovaGuard-AI.

### 1. Chuẩn bị NovaGuard-AI Backend

* Đảm bảo NovaGuard-AI backend (cụ thể là service `novaguard_backend_api`) đang chạy và có thể tiếp nhận request tại endpoint `/webhooks/github`.
* **Tạo Webhook Secret:**
    1.  Tạo một chuỗi bí mật (secret) mạnh mẽ và ngẫu nhiên. Ví dụ sử dụng `openssl rand -hex 32` hoặc một trình quản lý mật khẩu.
    2.  Cấu hình secret này trong môi trường của NovaGuard-AI backend. Nếu sử dụng Docker Compose, bạn có thể đặt biến môi trường `GITHUB_WEBHOOK_SECRET` trong file `.env` (đã được gitignore):
        ```env
        # Trong file .env ở thư mục gốc của dự án
        GITHUB_WEBHOOK_SECRET="your_generated_strong_secret_here"
        ```
        Và đảm bảo `docker-compose.yml` nạp biến này cho service `novaguard_backend_api`.

### 2. Xác định Payload URL cho Webhook

Đây là URL mà GitHub sẽ gửi các sự kiện đến. Endpoint của NovaGuard-AI là `/webhooks/github`.

* **Nếu sử dụng GitHub Enterprise Server (GHES) nội bộ:**
    * Payload URL sẽ là địa chỉ nội bộ của máy chủ đang chạy NovaGuard-AI.
    * Ví dụ: `http://<ip-may-chu-novaguard>:8000/webhooks/github` hoặc `http://novaguard.your-internal-domain.com/webhooks/github`.
    * Đảm bảo GHES có thể truy cập được địa chỉ này.

* **Nếu sử dụng GitHub.com và NovaGuard-AI chạy nội bộ (không có IP public trực tiếp):**
    Bạn cần một cách để GitHub.com có thể gửi request đến server nội bộ của bạn.
    * **Lựa chọn A: Sử dụng Reverse Proxy (Khuyến nghị cho môi trường شبه-production nội bộ)**
        * Nếu công ty bạn có một reverse proxy (Nginx, Traefik, Caddy, F5, ...) được cấu hình để có thể truy cập từ internet và có thể trỏ đến các dịch vụ nội bộ, hãy cấu hình nó.
        * Ví dụ: Cấu hình một tên miền con như `novaguard-webhook.yourcompany.com` để trỏ đến địa chỉ nội bộ của NovaGuard-AI, ví dụ `http://<ip-may-chu-novaguard-noi-bo>:8000`.
        * Payload URL trên GitHub sẽ là: `https://novaguard-webhook.yourcompany.com/webhooks/github` (ưu tiên HTTPS).
        * **Quan trọng:** Trên reverse proxy, hãy cân nhắc chỉ cho phép các request đến từ dải IP của GitHub (tham khảo API `GET /meta` của GitHub để lấy danh sách IP) và đảm bảo HTTPS được cấu hình.

    * **Lựa chọn B: Sử dụng ngrok (Cho Phát triển/Thử nghiệm)**
        1.  [Tải và cài đặt ngrok](https://ngrok.com/download).
        2.  Xác thực ngrok (nếu cần).
        3.  Nếu NovaGuard-AI backend đang chạy trên máy local của bạn ở port 8000, chạy lệnh:
            ```bash
            ngrok http 8000
            ```
        4.  Ngrok sẽ cung cấp một URL "Forwarding" dạng `https_//<random-string>.ngrok-free.app` (hoặc tương tự tùy phiên bản ngrok).
        5.  Payload URL trên GitHub sẽ là: `https_//<random-string>.ngrok-free.app/webhooks/github`.
        *Lưu ý: URL của ngrok (phiên bản miễn phí) sẽ thay đổi mỗi khi bạn khởi động lại ngrok. Điều này phù hợp cho dev, nhưng không ổn định cho production.*

### 3. Cấu hình Webhook trên GitHub/GHES Repository

1.  Truy cập repository của bạn trên GitHub hoặc GHES.
2.  Đi đến **Settings** > **Webhooks** (trong mục "Code and automation").
3.  Nhấp vào **"Add webhook"**.
4.  **Payload URL:** Nhập URL bạn đã xác định ở Bước 2.
5.  **Content type:** Chọn `application/json`.
6.  **Secret:** Nhập **chính xác** chuỗi `GITHUB_WEBHOOK_SECRET` mà bạn đã tạo và cấu hình cho NovaGuard-AI ở Bước 1.
7.  **Which events would you like to trigger this webhook?**
    * Chọn **"Let me select individual events."**
    * Bỏ chọn "Pushes".
    * **Chọn "Pull requests"**. Các sự kiện quan trọng cần được chọn là:
        * `opened` (khi PR được tạo)
        * `reopened` (khi PR được mở lại)
        * `synchronize` (khi có commit mới được push lên PR)
8.  Đảm bảo checkbox **"Active"** được chọn.
9.  Nhấp vào **"Add webhook"**.

### 4. Kiểm tra Webhook

* Sau khi thêm, GitHub/GHES sẽ gửi một "ping" event. Kiểm tra tab "Recent Deliveries" trong cài đặt webhook để xem trạng thái (response code 202 là thành công cho ping nếu endpoint của bạn xử lý nó, hoặc có thể là một response khác tùy logic ping của bạn). Endpoint `/webhooks/github` hiện tại của chúng ta sẽ trả về "Event type ignored" cho ping, nhưng vẫn là dấu hiệu kết nối thành công.
* Tạo một Pull Request mới, hoặc push một commit mới vào một Pull Request hiện có trong repository.
* Kiểm tra log của `novaguard_backend_api` và `novaguard_analysis_worker` để xem:
    * `novaguard_backend_api`: Có nhận được webhook, xác thực signature thành công, và gửi task vào Kafka không.
    * `novaguard_analysis_worker`: Có nhận được task từ Kafka không.

---

## Cách lấy thông tin Github repository

```bash
curl -L \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/dino-nlp/novaguard-test-project
```