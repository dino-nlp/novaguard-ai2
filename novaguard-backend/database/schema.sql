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
    last_full_scan_request_id INT, -- Sẽ thêm FK sau khi bảng fullprojectanalysisrequests được tạo
    last_full_scan_status VARCHAR(50), -- Sẽ dùng ENUM type sau, tạm thời VARCHAR
    last_full_scan_at TIMESTAMPTZ,
    UNIQUE(user_id, github_repo_id)
);

-- ENUM type cho FullProjectAnalysisStatus
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'full_project_analysis_status_enum') THEN
        CREATE TYPE full_project_analysis_status_enum AS ENUM (
            'pending', 
            'processing', 
            'source_fetched', 
            'ckg_building', 
            'analyzing', 
            'completed', 
            'failed'
        );
    END IF;
END$$;

-- Bảng fullprojectanalysisrequests: Lưu trữ thông tin về các yêu cầu phân tích toàn bộ dự án
CREATE TABLE IF NOT EXISTS fullprojectanalysisrequests (
    id SERIAL PRIMARY KEY,
    project_id INT REFERENCES projects(id) ON DELETE CASCADE NOT NULL,
    branch_name VARCHAR(255) NOT NULL,
    status full_project_analysis_status_enum DEFAULT 'pending' NOT NULL,
    error_message TEXT,
    requested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    source_fetched_at TIMESTAMPTZ,
    ckg_built_at TIMESTAMPTZ,
    analysis_completed_at TIMESTAMPTZ,
    total_files_analyzed INT,
    total_findings INT 
);

-- Thêm FOREIGN KEY cho projects.last_full_scan_request_id sau khi fullprojectanalysisrequests đã được tạo
ALTER TABLE projects
ADD CONSTRAINT fk_projects_last_full_scan
FOREIGN KEY (last_full_scan_request_id)
REFERENCES fullprojectanalysisrequests(id)
ON DELETE SET NULL;

-- Cập nhật kiểu dữ liệu cho projects.last_full_scan_status để sử dụng ENUM
-- Đảm bảo rằng giá trị hiện tại (nếu có) trong cột last_full_scan_status có thể cast sang ENUM
-- Nếu có giá trị không hợp lệ, bạn cần cập nhật chúng trước hoặc xử lý lỗi cast.
-- Ví dụ, nếu cột đang là VARCHAR và có giá trị không khớp với ENUM, lệnh ALTER này sẽ lỗi.
-- Bạn có thể cần một bước trung gian để NULL các giá trị không hợp lệ.
-- ALTER TABLE projects ALTER COLUMN last_full_scan_status DROP DEFAULT; -- (Nếu có default cũ)
ALTER TABLE projects
ALTER COLUMN last_full_scan_status TYPE full_project_analysis_status_enum
USING last_full_scan_status::full_project_analysis_status_enum;


-- ENUM type cho PRAnalysisStatus
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pr_analysis_status_enum') THEN
        CREATE TYPE pr_analysis_status_enum AS ENUM (
            'pending', 
            'processing', 
            'data_fetched', 
            'completed', 
            'failed'
        );
    END IF;
END$$;

-- Bảng pranalysisrequests: Lưu trữ thông tin về các yêu cầu phân tích Pull Request
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

-- ENUM types mới cho bảng analysisfindings (nếu chưa tồn tại)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'analysis_severity_enum') THEN
        CREATE TYPE analysis_severity_enum AS ENUM ('Error', 'Warning', 'Note', 'Info');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'scan_type_enum') THEN
        CREATE TYPE scan_type_enum AS ENUM ('pr', 'full_project');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'finding_level_enum') THEN
        CREATE TYPE finding_level_enum AS ENUM ('file', 'module', 'project');
    END IF;
END$$;

-- Bảng analysisfindings: Lưu trữ các phát hiện/gợi ý từ quá trình phân tích code
-- Đây là phiên bản cập nhật của bảng analysisfindings
DROP TABLE IF EXISTS analysisfindings CASCADE; -- Xóa bảng cũ để tạo lại với schema mới
CREATE TABLE analysisfindings (
    id SERIAL PRIMARY KEY,
    
    -- Liên kết với một trong hai loại request
    pr_analysis_request_id INT REFERENCES pranalysisrequests(id) ON DELETE CASCADE, -- Nullable
    full_project_analysis_request_id INT REFERENCES fullprojectanalysisrequests(id) ON DELETE CASCADE, -- Nullable
    
    scan_type scan_type_enum NOT NULL,

    file_path VARCHAR(1024), -- Có thể null cho project-level findings
    line_start INT,
    line_end INT,
    severity analysis_severity_enum NOT NULL,
    message TEXT NOT NULL,
    suggestion TEXT,
    agent_name VARCHAR(100),
    code_snippet TEXT,
    
    -- Các cột mới đã thảo luận
    finding_type VARCHAR(100), -- Ví dụ: 'code_smell', 'security_vuln', 'architectural_issue'
    finding_level finding_level_enum NOT NULL DEFAULT 'file',
    module_name VARCHAR(255), -- Tên module nếu phát hiện ở mức module
    meta_data JSONB, -- Dữ liệu cấu trúc bổ sung, dùng JSONB để hiệu năng tốt hơn JSON

    user_feedback VARCHAR(50), -- Giữ lại nếu bạn vẫn dùng trường này
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    -- Ràng buộc để đảm bảo một trong hai request_id (hoặc cả hai đều null nếu logic cho phép)
    -- Trong trường hợp này, chúng ta muốn một trong hai phải có giá trị.
    CONSTRAINT chk_one_request_id_must_exist CHECK (
        (pr_analysis_request_id IS NOT NULL AND full_project_analysis_request_id IS NULL) OR
        (pr_analysis_request_id IS NULL AND full_project_analysis_request_id IS NOT NULL)
    )
);

-- Thêm comment cho các cột mới (tùy chọn, nhưng tốt cho việc đọc hiểu schema)
COMMENT ON COLUMN analysisfindings.finding_type IS 'Type of finding, e.g., code_smell, security_vuln, architectural_issue';
COMMENT ON COLUMN analysisfindings.finding_level IS 'Level of finding: file, module, or project';
COMMENT ON COLUMN analysisfindings.module_name IS 'Module name if finding is module-level';
COMMENT ON COLUMN analysisfindings.meta_data IS 'Additional structured data as JSONB for better querying and details';


-- Optional: Tạo trigger để tự động cập nhật trường updated_at cho users và projects
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Áp dụng trigger cho bảng users
DROP TRIGGER IF EXISTS set_timestamp_users ON users; -- Xóa trigger cũ nếu có
CREATE TRIGGER set_timestamp_users
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- Áp dụng trigger cho bảng projects
DROP TRIGGER IF EXISTS set_timestamp_projects ON projects; -- Xóa trigger cũ nếu có
CREATE TRIGGER set_timestamp_projects
BEFORE UPDATE ON projects
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- Indexes (bao gồm các index mới cho analysisfindings)
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);

CREATE INDEX IF NOT EXISTS idx_fullprojectanalysisrequests_project_id ON fullprojectanalysisrequests(project_id);
CREATE INDEX IF NOT EXISTS idx_fullprojectanalysisrequests_status ON fullprojectanalysisrequests(status);

CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_project_id ON pranalysisrequests(project_id);
CREATE INDEX IF NOT EXISTS idx_pranalysisrequests_status ON pranalysisrequests(status);

-- Indexes cho analysisfindings (một số đã có, cập nhật thêm)
CREATE INDEX IF NOT EXISTS idx_analysisfindings_pr_req_id ON analysisfindings(pr_analysis_request_id) WHERE pr_analysis_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analysisfindings_full_proj_req_id ON analysisfindings(full_project_analysis_request_id) WHERE full_project_analysis_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analysisfindings_scan_type ON analysisfindings(scan_type);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_severity ON analysisfindings(severity);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_finding_level ON analysisfindings(finding_level);
CREATE INDEX IF NOT EXISTS idx_analysisfindings_finding_type ON analysisfindings(finding_type);
-- CREATE INDEX IF NOT EXISTS idx_analysisfindings_meta_data_gin ON analysisfindings USING GIN (meta_data); -- Index GIN cho JSONB nếu bạn có nhu cầu query sâu vào meta_data

COMMIT;