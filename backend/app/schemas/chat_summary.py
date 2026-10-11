"""摘要模型输出契约；覆盖水位由后端决定。"""

from pydantic import BaseModel, Field, field_validator


class SummaryContent(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)

    @field_validator("summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("摘要不能为空")
        return value
