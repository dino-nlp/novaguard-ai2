-- Bảng users: Lưu trữ thông tin người dùng
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    github_user_id VARCHAR(255) UNIQUE, -- Sẽ được cập nhật sau khi OAuth với GitHub
    github_access_token_encrypted TEXT, -- Token truy cập GitHub của người dùng, đã mã hóa
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Bảng projects: Lưu trữ thông tin các dự án người dùng thêm vào
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE NOT NULL, -- Khóa ngoại tới bảng users
    github_repo_id VARCHAR(255) NOT NULL, -- ID của repository trên GitHub
    repo_name VARCHAR(255) NOT NULL, -- Tên repository (ví dụ: 'owner/repo_name')
    main_branch VARCHAR(255) NOT NULL, -- Nhánh chính của dự án (ví dụ: 'main', 'master')
    language VARCHAR(100), -- Ngôn ngữ lập trình chính của dự án (người dùng cấu hình)
    custom_project_notes TEXT, -- Ghi chú tùy chỉnh về kiến trúc hoặc quy ước code của dự án
    github_webhook_id VARCHAR(255), -- ID của webhook đã được tạo trên GitHub cho repository này
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, github_repo_id) -- Đảm bảo mỗi người dùng chỉ thêm một repo GitHub một lần
);

-- Bảng pranalysisrequests: Lưu trữ thông tin về các yêu cầu phân tích Pull Request
CREATE TABLE IF NOT EXISTS pranalysisrequests (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES projects(id) ON DELETE CASCADE NOT NULL,
    pr_number INT NOT NULL,
    pr_title TEXT,
    pr_github_url VARCHAR(2048),
    head_sha VARCHAR(40),
    status VARCHAR(20) CHECK (status IN ('pending', 'processing', 'data_fetched', 'completed', 'failed')) DEFAULT 'pending' NOT NULL, -- Thêm NOT NULL nếu status luôn phải có giá trị
    error_message TEXT,
    requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Bảng analysisfindings: Lưu trữ các phát hiện/gợi ý từ quá trình phân tích code
CREATE TABLE IF NOT EXISTS analysisfindings (
    id SERIAL PRIMARY KEY,
    pr_analysis_request_id INT REFERENCES pranalysisrequests(id) ON DELETE CASCADE NOT NULL, -- Khóa ngoại tới bảng pranalysisrequests
    file_path VARCHAR(1024) NOT NULL, -- Đường dẫn file liên quan đến phát hiện
    line_start INT, -- Dòng bắt đầu (nếu có)
    line_end INT, -- Dòng kết thúc (nếu có)
    severity VARCHAR(50) CHECK (severity IN ('Error', 'Warning', 'Note', 'Info')) NOT NULL, -- Mức độ nghiêm trọng
    message TEXT NOT NULL, -- Mô tả vấn đề
    suggestion TEXT, -- Gợi ý sửa lỗi (từ LLM)
    agent_name VARCHAR(100), -- Tên của Agent đã tạo ra phát hiện này (ví dụ: 'DeepLogicBugHunterAI_MVP1')
    code_snippet TEXT,
    user_feedback VARCHAR(50), -- Phản hồi từ người dùng (ví dụ: 'Helpful', 'NotHelpful', 'Ignore') - tùy chọn MVP1
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Optional: Tạo trigger để tự động cập nhật trường updated_at
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Áp dụng trigger cho bảng users
DROP TRIGGER IF EXISTS set_timestamp_users ON users;
CREATE TRIGGER set_timestamp_users
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- Áp dụng trigger cho bảng projects
DROP TRIGGER IF EXISTS set_timestamp_projects ON projects;
CREATE TRIGGER set_timestamp_projects
BEFORE UPDATE ON projects
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- (Không cần trigger updated_at cho pranalysisrequests và analysisfindings vì chúng thường không được cập nhật nhiều sau khi tạo)

-- Indexes (Cân nhắc thêm các index cần thiết để tăng tốc độ truy vấn)
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_project_id ON pranalysisrequests(project_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_status ON pranalysisrequests(status);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_pr_analysis_request_id ON analysisfindings(pr_analysis_request_id);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_severity ON analysisfindings(severity);

COMMIT; -- Hoặc bỏ qua nếu bạn chạy từng lệnh một