import logging # Thêm logging
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import timedelta
import httpx # Cho việc gọi API GitHub
from urllib.parse import urlencode

from app.core.db import get_db
from app.auth_service import schemas, crud_user
from app.core.security import (
    create_access_token,
    verify_password,
    encrypt_data,
    # decrypt_data # Sẽ cần khi sử dụng token đã lưu
)
from app.core.config import settings
from app.models import User # Model User SQLAlchemy

router = APIRouter()
logger = logging.getLogger(__name__) # Khởi tạo logger

# --- Endpoints Đăng ký / Đăng nhập bằng Email/Password ---

@router.post("/register", response_model=schemas.UserPublic, status_code=status.HTTP_201_CREATED)
async def register_new_user(
    user_in: schemas.UserCreate, 
    db: Session = Depends(get_db)
):
    """
    Đăng ký một người dùng mới.
    - Kiểm tra xem email đã tồn tại chưa.
    - Tạo user mới nếu chưa tồn tại.
    """
    logger.info(f"Attempting to register new user with email: {user_in.email}")
    db_user = crud_user.get_user_by_email(db, email=user_in.email)
    if db_user:
        logger.warning(f"Registration failed: Email {user_in.email} already registered.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    created_user = crud_user.create_user(db=db, user=user_in)
    logger.info(f"User {created_user.email} (ID: {created_user.id}) registered successfully.")
    return created_user

@router.post("/login", response_model=schemas.Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Đăng nhập người dùng và trả về access token.
    - Xác thực user bằng email (form_data.username) và password.
    - Tạo và trả về JWT nếu xác thực thành công.
    """
    logger.info(f"Login attempt for user: {form_data.username}")
    user = crud_user.get_user_by_email(db, email=form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        logger.warning(f"Login failed for user {form_data.username}: Incorrect email or password.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    logger.info(f"User {user.email} logged in successfully. Token generated.")
    return {"access_token": access_token, "token_type": "bearer"}

# --- Endpoints GitHub OAuth (Sử dụng OAuth App) ---

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

@router.get("/github", summary="Redirect to GitHub for OAuth", name="github_oauth_redirect")
async def github_login_or_connect():
    """
    Chuyển hướng người dùng đến GitHub để ủy quyền ứng dụng NovaGuard-AI.
    Scope 'repo' cho phép truy cập repo.
    Scope 'read:user' và 'user:email' để lấy thông tin user.
    """
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_REDIRECT_URI:
        logger.error("GitHub OAuth (Client ID or Redirect URI) is not configured on the server.")
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="GitHub OAuth not configured (Client ID or Redirect URI missing).")
    
    scopes = ["repo", "read:user", "user:email"]
    # Tạo một state ngẫu nhiên và lưu vào session/cookie phía server để kiểm tra sau callback (chống CSRF)
    # Cho MVP này, chúng ta dùng một state cố định, nhưng nên thay thế bằng state động cho production.
    oauth_state = "novaguard_csrf_protect_state_string_example" # Nên tạo ngẫu nhiên và lưu lại
    
    query_params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
        "scope": " ".join(scopes),
        "state": oauth_state 
    }
    authorize_url_with_params = f"{GITHUB_AUTHORIZE_URL}?{urlencode(query_params)}"
    logger.info(f"Redirecting user to GitHub for OAuth: {authorize_url_with_params}")
    return RedirectResponse(authorize_url_with_params)


@router.get("/github/callback", summary="Handle GitHub OAuth Callback", name="github_oauth_callback")
async def github_callback(
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db),
    # request: Request # Để có thể lưu state vào session của request nếu cần
):
    """
    Xử lý callback từ GitHub.
    - Lấy 'code' từ query param.
    - (Nên) Kiểm tra 'state' để chống CSRF.
    - Yêu cầu access_token từ GitHub.
    - Lấy thông tin user từ GitHub API.
    - Tạo/Cập nhật user trong DB NovaGuard, lưu trữ GitHub token đã mã hóa.
    - Tạo JWT token của NovaGuard và trả về.
    """
    logger.info(f"Received GitHub OAuth callback with code: {code}, state: {state}")

    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET or not settings.GITHUB_REDIRECT_URI:
        logger.error("GitHub OAuth (Client ID, Client Secret, or Redirect URI) is not configured on the server.")
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="GitHub OAuth not configured completely.")

    # TODO: Kiểm tra 'state' đã lưu trước đó với 'state' nhận được từ callback để chống CSRF.
    # Ví dụ: expected_state = request.session.pop("oauth_state", None)
    # if not state or state != expected_state:
    #     logger.error("Invalid OAuth state. Possible CSRF attack.")
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid OAuth state.")

    # 1. Yêu cầu access_token từ GitHub
    token_payload = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
    }
    headers = {"Accept": "application/json"} # Yêu cầu response JSON
    
    async with httpx.AsyncClient() as client:
        try:
            logger.debug(f"Requesting GitHub access token from: {GITHUB_ACCESS_TOKEN_URL}")
            token_response = await client.post(GITHUB_ACCESS_TOKEN_URL, data=token_payload, headers=headers)
            token_response.raise_for_status()
            token_data = token_response.json()
            logger.debug(f"Received token data from GitHub: {token_data}")
        except httpx.HTTPStatusError as e:
            error_detail = e.response.json().get('error_description', 'Unknown error from GitHub token endpoint')
            logger.error(f"Error requesting GitHub access token: Status {e.response.status_code}, Detail: {error_detail}, Response: {e.response.text}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to obtain GitHub access token: {error_detail}")
        except Exception as e:
            logger.exception("Unexpected error requesting GitHub access token.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not obtain GitHub access token due to an unexpected error.")

    github_access_token = token_data.get("access_token")
    if not github_access_token:
        logger.error(f"GitHub access_token not found in response: {token_data}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub access_token not found in response.")
    logger.info("Successfully obtained GitHub access token.")

    # 2. Lấy thông tin user từ GitHub API
    user_api_url = "https://api.github.com/user"
    emails_api_url = "https://api.github.com/user/emails"
    auth_headers = {
        "Authorization": f"token {github_access_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    github_user_data = {}
    emails_data = []
    async with httpx.AsyncClient() as client:
        try:
            logger.debug("Fetching GitHub user information...")
            user_info_response = await client.get(user_api_url, headers=auth_headers)
            user_info_response.raise_for_status()
            github_user_data = user_info_response.json()
            logger.debug(f"GitHub user data: {github_user_data}")

            logger.debug("Fetching GitHub user emails...")
            emails_response = await client.get(emails_api_url, headers=auth_headers)
            emails_response.raise_for_status()
            emails_data = emails_response.json()
            logger.debug(f"GitHub emails data: {emails_data}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Error fetching GitHub user info: Status {e.response.status_code}, Response: {e.response.text}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to fetch GitHub user information.")
        except Exception as e:
            logger.exception("Unexpected error fetching GitHub user info.")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not fetch GitHub user information.")

    github_user_id_str = str(github_user_data.get("id"))
    github_login = github_user_data.get("login", "N/A")
    
    primary_email_obj = next((email_obj for email_obj in emails_data if email_obj.get("primary") and email_obj.get("verified")), None)
    github_email = primary_email_obj.get("email") if primary_email_obj else None

    if not github_email:
        github_email = github_user_data.get("email") # Thử lấy email từ profile nếu có
        if not github_email:
             logger.warning(f"Could not determine a verified primary email for GitHub user {github_login} (ID: {github_user_id_str}).")
             raise HTTPException(status_code=400, detail="Could not retrieve a verified primary email from GitHub. Please ensure you have a public or primary email set and verified on GitHub.")
    logger.info(f"Retrieved GitHub user: {github_login}, email: {github_email}, ID: {github_user_id_str}")

    # 3. Tạo/Cập nhật user trong DB NovaGuard
    db_user_by_github_id = db.query(User).filter(User.github_user_id == github_user_id_str).first()
    db_user_by_email = crud_user.get_user_by_email(db, email=github_email)

    encrypted_github_token = encrypt_data(github_access_token)
    if not encrypted_github_token:
        logger.error(f"Failed to encrypt GitHub token for user {github_login}. FERNET_KEY might be missing or invalid.")
        raise HTTPException(status_code=500, detail="Internal server error: Could not securely store GitHub token.")

    final_user: User | None = None
    is_new_user_creation = False

    if db_user_by_github_id:
        final_user = db_user_by_github_id
        final_user.github_access_token_encrypted = encrypted_github_token
        if final_user.email != github_email:
            if db_user_by_email and db_user_by_email.id != final_user.id:
                logger.warning(f"GitHub email {github_email} for user {github_login} is already used by another NovaGuard account (ID: {db_user_by_email.id}). Keeping original email {final_user.email}.")
            else:
                logger.info(f"Updating email for user {final_user.id} from {final_user.email} to {github_email}.")
                final_user.email = github_email
        logger.info(f"Updated GitHub token for existing user (by GitHub ID): {final_user.email} (ID: {final_user.id})")
    elif db_user_by_email:
        if db_user_by_email.github_user_id and db_user_by_email.github_user_id != github_user_id_str:
            logger.error(f"NovaGuard account {github_email} is already linked to a different GitHub ID ({db_user_by_email.github_user_id}). Attempted to link with GitHub ID {github_user_id_str}.")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This NovaGuard account is already linked to a different GitHub account.")
        final_user = db_user_by_email
        final_user.github_user_id = github_user_id_str
        final_user.github_access_token_encrypted = encrypted_github_token
        logger.info(f"Linked GitHub account (ID: {github_user_id_str}) to existing user (by email): {final_user.email} (ID: {final_user.id})")
    else:
        # Không nên dùng mật khẩu thực tế ở đây vì người dùng sẽ đăng nhập qua GitHub
        # Tạo một placeholder password không thể đoán được và không dùng để đăng nhập.
        # Hoặc, điều chỉnh model User để password_hash có thể là NULL cho user OAuth.
        # Hiện tại, crud_user.create_user yêu cầu password.
        placeholder_password = f"oauth_gh_{github_user_id_str}_{settings.SECRET_KEY[:8]}" # Tạo pass ngẫu nhiên
        
        user_in_create = schemas.UserCreate(email=github_email, password=placeholder_password)
        final_user = crud_user.create_user(db=db, user=user_in_create)
        final_user.github_user_id = github_user_id_str
        final_user.github_access_token_encrypted = encrypted_github_token
        is_new_user_creation = True
        logger.info(f"Created new user via GitHub OAuth: {final_user.email} (ID: {final_user.id}), linked to GitHub ID {github_user_id_str}")
    
    try:
        db.commit()
        if final_user: # Chỉ refresh nếu final_user được gán
             db.refresh(final_user)
    except Exception as e:
        db.rollback()
        logger.exception("Error committing user data during GitHub OAuth callback.")
        raise HTTPException(status_code=500, detail="Failed to save user data.")

    if not final_user:
        logger.error("User object is unexpectedly None after DB operations in GitHub callback.")
        raise HTTPException(status_code=500, detail="User processing failed after GitHub OAuth.")

    # 4. Tạo JWT của NovaGuard và trả về
    nova_access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    nova_access_token = create_access_token(
        subject=final_user.email, expires_delta=nova_access_token_expires
    )
    logger.info(f"Generated NovaGuard JWT for user {final_user.email} after GitHub OAuth.")
    
    # Trong môi trường thực tế, bạn sẽ redirect về frontend với token này,
    # hoặc frontend sẽ mở popup, sau đó popup đóng và truyền token về cửa sổ chính.
    # Ví dụ: return RedirectResponse(f"http://localhost:3000/oauth-callback?token={nova_access_token}")
    
    # Hiện tại, trả về JSON để dễ test
    return {
        "message": "GitHub OAuth successful." + (" New user created." if is_new_user_creation else " User linked/updated."),
        "access_token": nova_access_token, # NovaGuard's JWT
        "token_type": "bearer",
        "user_info": schemas.UserPublic.model_validate(final_user) # Chuyển đổi model sang schema
    }

# (Có thể thêm endpoint /users/me để test token sau này)