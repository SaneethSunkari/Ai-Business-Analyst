from pydantic import BaseModel, Field


class SignUpRequest(BaseModel):
    full_name: str = Field(..., description="Display name for the new account.")
    email: str = Field(..., description="Email address used to sign in.")
    password: str = Field(..., min_length=8, description="Password for the new account.")


class LoginRequest(BaseModel):
    email: str = Field(..., description="Email address used to sign in.")
    password: str = Field(..., description="Password for the account.")


class RefreshSessionRequest(BaseModel):
    refresh_token: str = Field(..., description="Supabase refresh token.")
