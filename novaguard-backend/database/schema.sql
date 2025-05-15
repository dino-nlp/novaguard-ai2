-- Bảng users: Lưu trữ thông tin người dùng
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    github_user_id VARCHAR(255) UNIQUE,
    github_access_token_encrypted TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Bảng projects: Lưu trữ thông tin các dự án người dùng thêm vào
-- Tạm thời chưa có last_full_scan_request_id làm FOREIGN KEY trực tiếp ở đây
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    github_repo_id VARCHAR(255) NOT NULL,
    repo_name VARCHAR(255) NOT NULL,
    main_branch VARCHAR(255) NOT NULL,
    language VARCHAR(100),
    custom_project_notes TEXT,
    github_webhook_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    -- Các cột mới cho full scan, nhưng khóa ngoại sẽ được thêm sau
    last_full_scan_request_id INT, -- Sẽ thêm FK sau
    last_full_scan_status VARCHAR(20), -- Sẽ dùng ENUM type sau
    last_full_scan_at TIMESTAMPTZ,
    UNIQUE(user_id, github_repo_id)
);

-- Bảng fullprojectanalysisrequests: Lưu trữ thông tin về các yêu cầu phân tích toàn bộ dự án
-- Tạo ENUM type trước nếu chưa có và SQLAlchemy không tự tạo trong schema.sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'full_project_analysis_status_enum') THEN
        CREATE TYPE full_project_analysis_status_enum AS ENUM ('pending', 'processing', 'source_fetched', 'ckg_building', 'analyzing', 'completed', 'failed');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS fullprojectanalysisrequests (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES projects(id) ON DELETE CASCADE NOT NULL, -- Tham chiếu đến projects
    branch_name VARCHAR(255) NOT NULL,
    status full_project_analysis_status_enum DEFAULT 'pending' NOT NULL, -- Sử dụng ENUM đã tạo
    error_message TEXT,
    requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    source_fetched_at TIMESTAMPTZ,
    ckg_built_at TIMESTAMPTZ,
    analysis_completed_at TIMESTAMPTZ,
    total_files_analyzed INT,
    total_findings INT
);

-- Bây giờ thêm FOREIGN KEY cho projects.last_full_scan_request_id
ALTER TABLE projects
ADD CONSTRAINT fk_projects_last_full_scan
FOREIGN KEY (last_full_scan_request_id)
REFERENCES fullprojectanalysisrequests(id)
ON DELETE SET NULL;

-- Và cập nhật kiểu dữ liệu cho projects.last_full_scan_status để sử dụng ENUM
ALTER TABLE projects
ALTER COLUMN last_full_scan_status TYPE full_project_analysis_status_enum
USING last_full_scan_status::full_project_analysis_status_enum;


-- Bảng pranalysisrequests: Lưu trữ thông tin về các yêu cầu phân tích Pull Request
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pr_analysis_status_enum') THEN
        CREATE TYPE pr_analysis_status_enum AS ENUM ('pending', 'processing', 'data_fetched', 'completed', 'failed');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS pranalysisrequests (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES projects(id) ON DELETE CASCADE NOT NULL,
    pr_number INT NOT NULL,
    pr_title TEXT,
    pr_github_url VARCHAR(2048),
    head_sha VARCHAR(40),
    status pr_analysis_status_enum DEFAULT 'pending' NOT NULL,
    error_message TEXT,
    requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Bảng analysisfindings: Lưu trữ các phát hiện/gợi ý từ quá trình phân tích code
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'analysis_severity_enum') THEN
        CREATE TYPE analysis_severity_enum AS ENUM ('Error', 'Warning', 'Note', 'Info');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS analysisfindings (
    id SERIAL PRIMARY KEY,
    pr_analysis_request_id INT REFERENCES pranalysisrequests(id) ON DELETE CASCADE NOT NULL,
    file_path VARCHAR(1024) NOT NULL,
    line_start INT,
    line_end INT,
    severity analysis_severity_enum NOT NULL, -- Sử dụng ENUM
    message TEXT NOT NULL,
    suggestion TEXT,
    agent_name VARCHAR(100),
    code_snippet TEXT,
    user_feedback VARCHAR(50),
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_fullprojectanalysisrequests_project_id ON fullprojectanalysisrequests(project_id);
CREATE INDEX IF NOT EXISTS idx_fullprojectanalysisrequests_status ON fullprojectanalysisrequests(status);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_project_id ON pranalysisrequests(project_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_status ON pranalysisrequests(status);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_pr_analysis_request_id ON analysisfindings(pr_analysis_request_id);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_severity ON analysisfindings(severity);

COMMIT;