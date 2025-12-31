"""
Request validation models and utilities
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from src.utils.config import settings


class SessionParams(BaseModel):
    """Standard session parameters"""
    year: int = Field(..., ge=2018, le=2030, description="Race year")
    gp: int = Field(..., ge=1, le=24, description="Grand Prix round number")
    session: str = Field(..., description="Session type")
    
    @field_validator('year')
    @classmethod
    def validate_year(cls, v):
        if v < settings.min_year or v > settings.max_year:
            raise ValueError(f'Year must be between {settings.min_year} and {settings.max_year}')
        return v
    
    @field_validator('gp')
    @classmethod
    def validate_gp(cls, v):
        if v < settings.min_round or v > settings.max_round:
            raise ValueError(f'GP round must be between {settings.min_round} and {settings.max_round}')
        return v
    
    @field_validator('session')
    @classmethod
    def validate_session(cls, v):
        if v not in settings.valid_sessions_list:
            raise ValueError(f'Session must be one of: {", ".join(settings.valid_sessions_list)}')
        return v


class DriverComparisonParams(SessionParams):
    """Parameters for driver comparison endpoints"""
    driver1: str = Field(..., min_length=3, max_length=3, description="First driver code")
    driver2: str = Field(..., min_length=3, max_length=3, description="Second driver code")
    
    @field_validator('driver1', 'driver2')
    @classmethod
    def validate_driver(cls, v):
        if not v.isupper():
            raise ValueError('Driver code must be uppercase')
        return v


class DriverSessionParams(SessionParams):
    """Parameters for single driver endpoints"""
    driver: str = Field(..., min_length=3, max_length=3, description="Driver code")
    
    @field_validator('driver')
    @classmethod
    def validate_driver(cls, v):
        if not v.isupper():
            raise ValueError('Driver code must be uppercase')
        return v


class ErrorResponse(BaseModel):
    """Standard error response"""
    detail: str
    error_code: Optional[str] = None
    timestamp: Optional[str] = None


class HealthCheckResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    environment: str
    checks: dict


class SessionMetadata(BaseModel):
    """Session metadata"""
    year: int
    round_number: int
    session_type: str
    event_name: str
    generated_at: str
