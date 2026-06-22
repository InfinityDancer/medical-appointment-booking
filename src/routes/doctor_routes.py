from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, validator
from typing import List
from src.services.set_doctor_availability_service import update_doctor_availability

router = APIRouter()

# status map for service err codes
_ERROR_MAP = {
    "MISSING_DOCTOR_ID":    422,
    "MISSING_ORG_ID":       422,
    "MISSING_BLOCKS":       422,
    "INVALID_TIMEZONE":     422,
    "INVALID_DATE":         422,
    "MISSING_LOCATION":     422,
    "INVALID_TIME":         422,
    "INVALID_TIME_RANGE":   422,
    "LOCATION_NOT_FOUND":   404,
    "DAY_LOCKED":           409,
    "DAY_HAS_AVAILABILITY": 409,
    "DUPLICATE_BLOCK":      409,
    "OVERLAP_CONFLICT":     409,
    "DB_READ_ERROR":        503,
    "DB_WRITE_ERROR":       503,
}


class TimeBlock(BaseModel):
    start_time: str = Field(..., description="hh:mm (24hr) or hh:mm am/pm")
    end_time: str = Field(..., description="hh:mm (24hr) or hh:mm am/pm")
    is_unavailable: bool
    location: str

    @validator("start_time", "end_time")
    def non_empty_time(cls, v: str) -> str:
        """ensure time param is provided.
        params: v (str)
        output: stripped str
        """
        if not v or not v.strip():
            raise ValueError("time field empty")
        return v.strip()

    @validator("location")
    def non_empty_location(cls, v: str) -> str:
        """ensure location param is provided.
        params: v (str)
        output: stripped str
        """
        if not v or not v.strip():
            raise ValueError("location empty")
        return v.strip()


class DoctorAvailabilityPayload(BaseModel):
    doctor_id: str
    organisation_id: str
    date: str = Field(..., description="yyyy-mm-dd")
    timezone: str = Field(..., description="iana tz e.g. asia/kolkata")
    blocks: List[TimeBlock] = Field(..., min_items=1)

    @validator("doctor_id", "organisation_id", "date", "timezone")
    def non_empty(cls, v: str) -> str:
        """ensure req params aren't empty.
        params: v (str)
        output: stripped str
        """
        if not v or not v.strip():
            raise ValueError("field empty")
        return v.strip()


@router.post("/availability")
async def update_availability(payload: DoctorAvailabilityPayload):
    """post /v1/doctor/availability -- validate and save availability blocks.
    params: payload (DoctorAvailabilityPayload)
    output: json response with status/msg
    """
    blocks_dict = [
        {
            "start_time": b.start_time,
            "end_time": b.end_time,
            "is_unavailable": b.is_unavailable,
            "location": b.location
        }
        for b in payload.blocks
    ]

    result = update_doctor_availability(
        doctor_id=payload.doctor_id,
        organisation_id=payload.organisation_id,
        date_str=payload.date,
        timezone_str=payload.timezone,
        blocks=blocks_dict
    )

    if result.get("success"):
        return {"result_code": 200, "message": result["message"]}

    error_code = result.get("error_code", "UNKNOWN")
    status = _ERROR_MAP.get(error_code, 500)
    raise HTTPException(status_code=status, detail={"error_code": error_code, "message": result.get("message")})
