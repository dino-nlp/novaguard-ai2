import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request 
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse 
from sqlalchemy.orm import Session
from datetime import timedelta
import httpx 
from urllib.parse import urlencode 
import secrets # Để tạo state ngẫu nhiên
from typing import Optional, List, Dict, Any
import logging

from app.core.db import get_db
from app.auth_service import schemas as auth_schemas 
from app.auth_service import crud_user as auth_crud_user 
from app.core.security import (
    create_access_token, 
    verify_password,
    encrypt_data,
)
from app.core.config import settings
from app.models import User 

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/register", response_model=auth_schemas.UserPublic, status_code=status.HTTP_201_CREATED)
async def api_register_new_user(
    user_in: auth_schemas.UserCreate, 
    db: Session = Depends(get_db)
):
    logger.info(f"API attempting to register new user with email: {user_in.email}")
    db_user = auth_crud_user.get_user_by_email(db, email=user_in.email)
    if db_user:
        logger.warning(f"API Registration failed: Email {user_in.email} already registered.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    created_user = auth_crud_user.create_user(db=db, user=user_in)
    logger.info(f"API User {created_user.email} (ID: {created_user.id}) registered successfully.")
    return created_user

@router.post("/login", response_model=auth_schemas.Token)
async def api_login_for_access_token( 
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    logger.info(f"API Login attempt for user: {form_data.username}")
    user = auth_crud_user.get_user_by_email(db, email=form_data.username)
    if not user or not verify_password(form_data.password, user.password_hash):
        logger.warning(f"API Login failed for user {form_data.username}: Incorrect email or password.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    nova_guard_jwt = create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    logger.info(f"API User {user.email} logged in successfully. NovaGuard JWT generated.")
    return {"access_token": nova_guard_jwt, "token_type": "bearer"}

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

@router.get("/github", summary="Redirect to GitHub for OAuth", name="api_github_oauth_redirect") # Giữ tên route này
async def api_github_login_or_connect(request: Request):
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_REDIRECT_URI:
        logger.error("GitHub OAuth (Client ID or Redirect URI) is not configured on the server.")
        logger.error(f"{settings.GITHUB_REDIRECT_URI=}")
        logger.error(f"{settings.GITHUB_CLIENT_ID=}")
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="GitHub OAuth client not configured.")
    
    scopes = ["repo", "read:user", "user:email"]
    oauth_state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = oauth_state # Lưu state vào session
    logger.debug(f"Generated OAuth state: {oauth_state} and stored in session.")
    
    query_params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": settings.GITHUB_REDIRECT_URI, 
        "scope": " ".join(scopes),
        "state": oauth_state 
    }
    authorize_url_with_params = f"{GITHUB_AUTHORIZE_URL}?{urlencode(query_params)}"
    logger.info(f"Redirecting user to GitHub for OAuth: {authorize_url_with_params}")
    return RedirectResponse(authorize_url_with_params)


@router.get("/github/callback", summary="Handle GitHub OAuth Callback", name="api_github_oauth_callback")
async def api_github_callback(
    request: Request,
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db)
):
    logger.info(f"Received GitHub OAuth callback. Code: {'******' if code else 'None'}, State: {state}")

    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET or not settings.GITHUB_REDIRECT_URI:
        logger.error("GitHub OAuth (Client ID, Client Secret, or Redirect URI) is not configured.")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="github_oauth_config_error_server"), status_code=status.HTTP_302_FOUND)

    expected_state = request.session.pop("oauth_state", None)
    if not state or not expected_state or state != expected_state:
        logger.error(f"Invalid OAuth state. Expected: '{expected_state}', Got: '{state}'. Possible CSRF attack.")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Invalid OAuth state. Please try logging in with GitHub again."), status_code=status.HTTP_302_FOUND)
    logger.debug("OAuth state validated successfully.")

    token_payload = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.GITHUB_REDIRECT_URI,
    }
    headers_token_req = {"Accept": "application/json"}
    github_access_token: Optional[str] = None
    async with httpx.AsyncClient() as client:
        try:
            token_response = await client.post(GITHUB_ACCESS_TOKEN_URL, data=token_payload, headers=headers_token_req)
            token_response.raise_for_status()
            token_data = token_response.json()
            github_access_token = token_data.get("access_token")
        except httpx.HTTPStatusError as e:
            error_detail = e.response.json().get('error_description', 'Could not obtain token from GitHub')
            logger.error(f"Error requesting GitHub access token: Status {e.response.status_code}, Detail: {error_detail}")
            return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error=f"GitHub auth error: {error_detail}"), status_code=status.HTTP_302_FOUND)
        except Exception as e:
            logger.exception("Unexpected error requesting GitHub access token.")
            return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Could not connect to GitHub for token exchange."), status_code=status.HTTP_302_FOUND)

    if not github_access_token:
        logger.error("GitHub access_token not found in response from GitHub.")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="GitHub access token not received."), status_code=status.HTTP_302_FOUND)
    logger.info("Successfully obtained GitHub access token.")

    user_api_url = "https://api.github.com/user"
    emails_api_url = "https://api.github.com/user/emails"
    auth_headers_gh = {
        "Authorization": f"token {github_access_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    github_user_data: Dict[str, Any] = {}
    emails_data: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient() as client:
            user_info_response = await client.get(user_api_url, headers=auth_headers_gh)
            user_info_response.raise_for_status()
            github_user_data = user_info_response.json()
            emails_response = await client.get(emails_api_url, headers=auth_headers_gh)
            emails_response.raise_for_status()
            emails_data = emails_response.json()
    except Exception as e:
        logger.error(f"Failed to get user info from GitHub: {e}")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Failed to retrieve user information from GitHub."), status_code=status.HTTP_302_FOUND)

    github_user_id_str = str(github_user_data.get("id"))
    github_login = github_user_data.get("login", "N/A")
    primary_email_obj = next((e_obj for e_obj in emails_data if isinstance(e_obj, dict) and e_obj.get("primary") and e_obj.get("verified")), None)
    github_email = primary_email_obj.get("email") if primary_email_obj else github_user_data.get("email")
    if not github_email:
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Could not retrieve a verified primary email from GitHub."), status_code=status.HTTP_302_FOUND)
    logger.info(f"Retrieved GitHub user: {github_login}, email: {github_email}, ID: {github_user_id_str}")

    db_user_by_github_id = auth_crud_user.get_user_by_github_id(db, github_id=github_user_id_str)
    db_user_by_email = auth_crud_user.get_user_by_email(db, email=github_email)
    encrypted_github_token = encrypt_data(github_access_token)
    if not encrypted_github_token:
        logger.error(f"Failed to encrypt GitHub token for user {github_login}.")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Server error storing GitHub token."), status_code=status.HTTP_302_FOUND)

    final_user_for_session: Optional[User] = None
    try:
        if db_user_by_github_id:
            final_user_for_session = db_user_by_github_id
            final_user_for_session.github_access_token_encrypted = encrypted_github_token
            if final_user_for_session.email != github_email and not (db_user_by_email and db_user_by_email.id != final_user_for_session.id) :
                final_user_for_session.email = github_email
            logger.info(f"Updated GitHub token for existing user (by GitHub ID): {final_user_for_session.email} (ID: {final_user_for_session.id})")
        elif db_user_by_email:
            if db_user_by_email.github_user_id and db_user_by_email.github_user_id != github_user_id_str:
                logger.error(f"NovaGuard account {github_email} already linked to GitHub ID {db_user_by_email.github_user_id}. Cannot link to {github_user_id_str}.")
                return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Account already linked to a different GitHub ID."), status_code=status.HTTP_302_FOUND)
            final_user_for_session = db_user_by_email
            final_user_for_session.github_user_id = github_user_id_str
            final_user_for_session.github_access_token_encrypted = encrypted_github_token
            logger.info(f"Linked GitHub account (ID: {github_user_id_str}) to existing user (by email): {final_user_for_session.email} (ID: {final_user_for_session.id})")
        else:
            placeholder_password = f"oauth_gh_{github_user_id_str}_{settings.SECRET_KEY[:10]}"
            user_in_create = auth_schemas.UserCreate(email=github_email, password=placeholder_password)
            new_user = auth_crud_user.create_user(db=db, user=user_in_create, is_oauth_user=True)
            new_user.github_user_id = github_user_id_str
            new_user.github_access_token_encrypted = encrypted_github_token
            final_user_for_session = new_user
            logger.info(f"Created new user via GitHub OAuth: {final_user_for_session.email} (ID: {final_user_for_session.id}), linked to GitHub ID {github_user_id_str}")
        
        db.commit()
        if final_user_for_session: db.refresh(final_user_for_session)
    except Exception as e_db:
        db.rollback(); logger.exception("DB commit/refresh error in GitHub callback")
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="Database error during user processing."), status_code=status.HTTP_302_FOUND)

    if not final_user_for_session:
        return RedirectResponse(url=request.url_for("ui_login_get").include_query_params(error="User processing failed after GitHub OAuth."), status_code=status.HTTP_302_FOUND)

    request.session["user_id"] = final_user_for_session.id
    request.session["user_email"] = final_user_for_session.email
    logger.info(f"UI Session created for user {final_user_for_session.email} (ID: {final_user_for_session.id}) after GitHub OAuth.")
    
    dashboard_url = request.url_for("ui_dashboard_get") 
    return RedirectResponse(url=dashboard_url, status_code=status.HTTP_302_FOUND)