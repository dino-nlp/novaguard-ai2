import logging # Thêm logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__) # Khởi tạo logger

# --- Password Hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# --- JSON Web Tokens (JWT) ---
ALGORITHM = settings.ALGORITHM
# Đảm bảo SECRET_KEY được load đúng từ settings, không phải giá trị mặc định nếu có lỗi load .env
# SECRET_KEY sẽ được settings object quản lý.
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

def create_access_token(subject: Union[str, Any], expires_delta: timedelta | None = None) -> str:
    if not settings.SECRET_KEY or settings.SECRET_KEY == "default_jwt_secret_needs_override_from_env":
        logger.error("JWT Secret Key is not configured or is default. Cannot create token.")
        raise ValueError("JWT Secret Key is not properly configured.")

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> dict | None:
    if not settings.SECRET_KEY or settings.SECRET_KEY == "default_jwt_secret_needs_override_from_env":
        logger.error("JWT Secret Key is not configured or is default. Cannot decode token.")
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"JWT decoding error: {e}")
        return None

# --- Fernet Encryption for GitHub Tokens ---
_fernet_instance = None

def get_fernet() -> Fernet | None:
    global _fernet_instance
    if not settings.FERNET_ENCRYPTION_KEY:
        logger.error("FERNET_ENCRYPTION_KEY is not configured! Cannot use Fernet encryption.")
        return None
    
    if _fernet_instance is None:
        try:
            key_bytes = settings.FERNET_ENCRYPTION_KEY.encode('utf-8')
            _fernet_instance = Fernet(key_bytes)
        except Exception as e:
            logger.exception(f"Error initializing Fernet with key. Ensure FERNET_ENCRYPTION_KEY is a valid Fernet key: {e}")
            _fernet_instance = None # Đảm bảo không dùng instance lỗi
    return _fernet_instance

def encrypt_data(data: str) -> str | None:
    fernet = get_fernet()
    if not fernet or not data:
        if not fernet: logger.error("Fernet instance not available for encryption.")
        return None
    try:
        return fernet.encrypt(data.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.exception(f"Encryption failed: {e}")
        return None

def decrypt_data(encrypted_data: str) -> str | None:
    fernet = get_fernet()
    if not fernet or not encrypted_data:
        if not fernet: logger.error("Fernet instance not available for decryption.")
        return None
    try:
        return fernet.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        logger.warning("Decryption failed: Invalid Fernet token.")
        return None
    except Exception as e:
        logger.exception(f"Decryption failed with an unexpected error: {e}")
        return None

if __name__ == '__main__':
    print("Running security module self-tests...")
    # Password Hashing Test
    plain_pw = "mysecretpassword"
    hashed_pw = get_password_hash(plain_pw)
    print(f"\n--- Password Hashing ---")
    print(f"Plain password: {plain_pw}")
    print(f"Hashed password: {hashed_pw}")
    print(f"Verification (correct): {verify_password(plain_pw, hashed_pw)}")
    print(f"Verification (incorrect): {verify_password('wrongpassword', hashed_pw)}")

    # JWT Test
    print(f"\n--- JWT ---")
    if not settings.SECRET_KEY or settings.SECRET_KEY == "default_jwt_secret_needs_override_from_env":
        print("Skipping JWT test as SECRET_KEY is not properly set in .env")
    else:
        user_identifier = "user@example.com"
        token = create_access_token(user_identifier)
        print(f"Generated JWT for '{user_identifier}': {token}")
        payload = decode_access_token(token)
        if payload:
            print(f"Decoded payload: {payload}")
        else:
            print("Failed to decode token.")

    # Fernet Test
    print(f"\n--- Fernet Encryption ---")
    if not settings.FERNET_ENCRYPTION_KEY:
        print("Skipping Fernet test as FERNET_ENCRYPTION_KEY is not set in .env")
        print("Generate one using: from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    else:
        original_text = "my_very_secret_github_oauth_token_string"
        print(f"Original text: {original_text}")
        encrypted = encrypt_data(original_text)
        if encrypted:
            print(f"Encrypted: {encrypted}")
            decrypted = decrypt_data(encrypted)
            print(f"Decrypted: {decrypted}")
            if decrypted == original_text:
                print("Fernet encryption/decryption test PASSED.")
            else:
                print("Fernet encryption/decryption test FAILED.")
        else:
            print("Fernet encryption failed, check FERNET_ENCRYPTION_KEY and logs.")