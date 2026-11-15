"""Authentication request schemas shared by account and notification routes."""
from typing import Literal
from unicodedata import category

from pydantic import BaseModel, Field, field_validator, model_validator

from .email_verification import normalize_email


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=18)


class NicknameInput(BaseModel):
    nickname: str | None = Field(max_length=32)

    @field_validator('nickname', mode='before')
    @classmethod
    def clean_nickname(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(category(char) in ('Cc', 'Cs') for char in value):
                raise ValueError('Nickname must contain displayable text')
            return value or None
        return value


class EmailInput(BaseModel):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class CodeRequest(EmailInput):
    language: Literal["en", "zh-CN"] = "en"


class BindingCodeRequest(CodeRequest):
    current_password: str = Field(min_length=8, max_length=18)


class CodeInput(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


class RegistrationCredentials(Credentials, EmailInput, CodeInput):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    confirm_password: str = Field(min_length=8, max_length=18)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class NewPassword(BaseModel):
    new_password: str = Field(min_length=8, max_length=18)
    confirm_password: str = Field(min_length=8, max_length=18)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class PasswordChange(NewPassword):
    method: Literal["password", "email"]
    current_password: str | None = Field(default=None, min_length=8, max_length=18)
    challenge_id: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")

    @model_validator(mode="after")
    def require_proof(self):
        if self.method == "password":
            valid = self.current_password is not None and self.challenge_id is None and self.code is None
        else:
            valid = self.current_password is None and self.challenge_id is not None and self.code is not None
        if not valid:
            raise ValueError("Provide the selected verification method.")
        return self


class PasswordReset(NewPassword, EmailInput, CodeInput):
    pass


class PasswordCodeRequest(BaseModel):
    language: Literal["en", "zh-CN"] = "en"


