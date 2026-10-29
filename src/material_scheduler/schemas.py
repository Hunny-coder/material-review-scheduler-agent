"""Validated external contracts. Unknown fields are rejected, never treated as approval."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def aware_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间必须带时区偏移")
    if parsed.second or parsed.microsecond or parsed.minute % 30:
        raise ValueError("时间必须落在 30 分钟槽边界")
    return parsed


class TimeWindow(StrictModel):
    start: str
    end: str

    @model_validator(mode="after")
    def valid_window(self):
        start, end = aware_time(self.start), aware_time(self.end)
        if not 0 < (end - start).total_seconds() <= 7 * 86400:
            raise ValueError("窗口必须大于零且不超过 7 天")
        return self


class Requirement(StrictModel):
    kind: Literal["exact", "window", "preferred"] = "exact"
    at: str | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: Literal[30] = 30

    @model_validator(mode="after")
    def valid_requirement(self):
        if self.kind == "exact":
            if not self.at or self.start or self.end:
                raise ValueError("exact 必须且只能指定 at")
            aware_time(self.at)
        else:
            if not self.start or not self.end:
                raise ValueError("window/preferred 必须指定 start、end")
            window = TimeWindow(start=self.start, end=self.end)
            if (aware_time(window.end) - aware_time(window.start)).total_seconds() < 1800:
                raise ValueError("窗口不足一个槽")
            if self.kind == "window" and self.at:
                raise ValueError("window 不接受 at")
            if self.kind == "preferred":
                if not self.at:
                    raise ValueError("preferred 必须指定 at")
                at = aware_time(self.at)
                if not aware_time(self.start) <= at < aware_time(self.end):
                    raise ValueError("偏好时刻必须在授权窗口内")
        return self


class MaterialItem(StrictModel):
    material_id: str = Field(min_length=1, max_length=80)
    channel: str = Field(min_length=1, max_length=80)
    account_id: str = Field(min_length=1, max_length=80)
    publish_requirement: Requirement
    alternative_search_window: TimeWindow | None = None


class ScheduleRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=120)
    request_revision: int = Field(default=1, ge=1)
    timezone: str
    # Validate items separately so an invalid item does not discard valid siblings.
    items: list[dict] = Field(min_length=1, max_length=5)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        ZoneInfo(value)
        return value


class Rule(StrictModel):
    id: str
    field: str
    operator: Literal["in", "contains", "max_length"]
    severity: Literal["warning", "needs_review", "blocked"]
    reason: str
    values: list[str] = Field(default_factory=list)
    value: int | None = None

    @model_validator(mode="after")
    def valid_operator_args(self):
        if self.operator == "max_length" and (self.value is None or self.value < 1):
            raise ValueError("max_length 需要正整数 value")
        if self.operator != "max_length" and not self.values:
            raise ValueError("in/contains 需要非空 values")
        return self


class RulesConfig(StrictModel):
    version: str
    required_fields: list[str] = Field(min_length=1)
    business_hours: list[int] = Field(min_length=2, max_length=2)
    rules: list[Rule]

    @field_validator("business_hours")
    @classmethod
    def valid_hours(cls, value):
        if not 0 <= value[0] < value[1] <= 24:
            raise ValueError("营业时间无效")
        return value


class ScheduleEntry(TimeWindow):
    channel: str
    account_id: str
    schedule_id: str | None = None
    material_id: str | None = None


class ScheduleData(StrictModel):
    version: str
    items: list[ScheduleEntry]
