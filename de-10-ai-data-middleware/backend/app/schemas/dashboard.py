from pydantic import BaseModel, Field


RowValue = str | int | float | bool | None


class DashboardRefineRequest(BaseModel):
    question: str = Field(..., description="Plain-English request for how the dashboard should look.")
    source_question: str | None = Field(
        None,
        description="Original business question that produced the current result set.",
    )
    columns: list[str] = Field(..., description="Columns from the current query result.")
    rows: list[list[RowValue]] = Field(
        default_factory=list,
        description="Rows from the current query result.",
    )
    current_dashboard: dict | None = Field(
        default=None,
        description="Optional current dashboard spec to refine.",
    )
