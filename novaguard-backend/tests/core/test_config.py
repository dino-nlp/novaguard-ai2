# novaguard-backend/tests/core/test_config.py
import unittest
from unittest.mock import patch
import os

# Sử dụng get_settings từ config để test cache, và Settings trực tiếp để test default
from app.core.config import Settings, get_settings 

class TestSettings(unittest.TestCase):

    def test_default_settings_loaded(self):
        """Test if default settings are loaded correctly when no .env or env vars are present."""
        # Để test giá trị mặc định, chúng ta KHÔNG muốn nó đọc từ .env thật (nếu có)
        # hoặc các biến môi trường đã được set.
        # Cách tốt nhất là tạm thời mock os.environ để nó rỗng cho scope của test này
        # HOẶC, chúng ta phải đảm bảo Settings() không bị ảnh hưởng bởi cache của get_settings()
        # và không bị ảnh hưởng bởi các biến môi trường bên ngoài.
        
        # Cách 1: Clear cache và tạo Settings() mới, giả định không có .env ảnh hưởng trong test runner
        get_settings.cache_clear() # Quan trọng để get_settings() không trả về instance cũ
                                # Tuy nhiên, Settings() trực tiếp không dùng cache này.
        
        # Nếu Settings() đọc từ .env, test này có thể không ổn định.
        # Giả sử Settings() chỉ lấy default nếu không có env var và không có .env file (hoặc env_file không được set)
        # Với cấu hình model_config = SettingsConfigDict(env_file=".env",...), nó SẼ cố đọc .env
        
        # Để test chính xác giá trị mặc định CỦA CLASS, không phải giá trị đã load từ env/file:
        # Cách tiếp cận là không dùng instance settings toàn cục (đã load từ .env)
        # mà là kiểm tra giá trị default của field trong class Settings
        default_secret_key_from_class = Settings.model_fields['SECRET_KEY'].default
        self.assertEqual(default_secret_key_from_class, "default_jwt_secret_needs_override_from_env")
        
        # Test các giá trị mặc định khác nếu cần
        default_db_url = Settings.model_fields['DATABASE_URL'].default
        self.assertEqual(default_db_url, "postgresql://novaguard_user:novaguard_password@postgres_db:5432/novaguard_db")


    @patch.dict(os.environ, {
        "DATABASE_URL": "env_db_url_test_config", # Đổi tên biến để tránh xung đột
        "SECRET_KEY": "env_secret_key_test_config",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        "OLLAMA_BASE_URL": "http://env_ollama_url_test_config",
        "FERNET_ENCRYPTION_KEY": "env_fernet_key_test_config" # Thêm các biến khác nếu cần
    }, clear=True) # clear=True để đảm bảo chỉ các biến này được set
    def test_settings_loaded_from_env(self):
        """Test if settings are correctly overridden by environment variables."""
        get_settings.cache_clear() # Xóa cache để get_settings() tạo instance mới từ env vars
        settings_from_env = get_settings()

        self.assertEqual(settings_from_env.DATABASE_URL, "env_db_url_test_config")
        self.assertEqual(settings_from_env.SECRET_KEY, "env_secret_key_test_config")
        self.assertEqual(settings_from_env.ACCESS_TOKEN_EXPIRE_MINUTES, 30)
        self.assertEqual(settings_from_env.OLLAMA_BASE_URL, "http://env_ollama_url_test_config")
        self.assertEqual(settings_from_env.FERNET_ENCRYPTION_KEY, "env_fernet_key_test_config")

        get_settings.cache_clear() # Dọn dẹp sau test

    def test_settings_are_cached(self):
        """Test that get_settings() returns a cached instance."""
        get_settings.cache_clear() 
        s1 = get_settings()
        s2 = get_settings()
        self.assertIs(s1, s2)
        get_settings.cache_clear()

# Bỏ if __name__ == '__main__': unittest.main() nếu chạy bằng discover