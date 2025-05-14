-- Bảng Users: Lưu trữ thông tin người dùng
CREATE TABLE IF NOT EXISTS Users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    github_user_id VARCHAR(255) UNIQUE, -- Sẽ được cập nhật sau khi OAuth với GitHub
    github_access_token_encrypted TEXT, -- Token truy cập GitHub của người dùng, đã mã hóa
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Bảng Projects: Lưu trữ thông tin các dự án người dùng thêm vào
CREATE TABLE IF NOT EXISTS Projects (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES Users(id) ON DELETE CASCADE NOT NULL, -- Khóa ngoại tới bảng Users
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

-- Bảng PRAnalysisRequests: Lưu trữ thông tin về các yêu cầu phân tích Pull Request
CREATE TABLE IF NOT EXISTS PRAnalysisRequests (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES Projects(id) ON DELETE CASCADE NOT NULL, -- Khóa ngoại tới bảng Projects
    pr_number INT NOT NULL, -- Số của Pull Request trên GitHub
    pr_title TEXT,
    pr_github_url VARCHAR(2048), -- URL tới Pull Request trên GitHub
    head_sha VARCHAR(40), -- SHA của commit mới nhất trong PR
    status VARCHAR(20) CHECK (status IN ('pending', 'processing', 'data_fetched', 'completed', 'failed')) DEFAULT 'pending',
    error_message TEXT, -- Thông báo lỗi nếu quá trình phân tích thất bại
    requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP, -- Thời điểm webhook được nhận hoặc yêu cầu được tạo
    started_at TIMESTAMPTZ, -- Thời điểm worker bắt đầu xử lý
    completed_at TIMESTAMPTZ -- Thời điểm worker hoàn thành xử lý
);

-- Bảng AnalysisFindings: Lưu trữ các phát hiện/gợi ý từ quá trình phân tích code
CREATE TABLE IF NOT EXISTS AnalysisFindings (
    id SERIAL PRIMARY KEY,
    pr_analysis_request_id INT REFERENCES PRAnalysisRequests(id) ON DELETE CASCADE NOT NULL, -- Khóa ngoại tới bảng PRAnalysisRequests
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

-- Áp dụng trigger cho bảng Users
DROP TRIGGER IF EXISTS set_timestamp_users ON Users;
CREATE TRIGGER set_timestamp_users
BEFORE UPDATE ON Users
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- Áp dụng trigger cho bảng Projects
DROP TRIGGER IF EXISTS set_timestamp_projects ON Projects;
CREATE TRIGGER set_timestamp_projects
BEFORE UPDATE ON Projects
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- (Không cần trigger updated_at cho PRAnalysisRequests và AnalysisFindings vì chúng thường không được cập nhật nhiều sau khi tạo)

-- Indexes (Cân nhắc thêm các index cần thiết để tăng tốc độ truy vấn)
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON Projects(user_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_project_id ON PRAnalysisRequests(project_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_status ON PRAnalysisRequests(status);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_pr_analysis_request_id ON AnalysisFindings(pr_analysis_request_id);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_severity ON AnalysisFindings(severity);

COMMIT; -- Hoặc bỏ qua nếu bạn chạy từng lệnh một