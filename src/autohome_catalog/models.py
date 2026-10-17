"""Validate source shapes without discarding original cell attributes."""

from pydantic import BaseModel, ConfigDict, Field


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class Cell(SourceModel):
    titleid: int
    itemname: str | None = None
    sublist: list[dict] | None = None
    priceinfo: str | None = None
    colorinfo: dict | list | None = None


class Title(SourceModel):
    itemid: int
    titleid: int
    itemname: str


class TitleGroup(SourceModel):
    groupname: str
    itemtype: str
    items: list[Title]


class Bread(SourceModel):
    seriesid: int
    seriesname: str
    brandname: str


class RawSpec(SourceModel):
    specid: int
    specname: str
    specstatus: int
    paramconflist: list[Cell]


class ParameterResponse(SourceModel):
    bread: Bread
    titlelist: list[TitleGroup]
    datalist: list[RawSpec]


class SourceReceipt(SourceModel):
    url: str
    final_url: str
    fetched_at: str
    sha256: str
    file: str
    status: int
    elapsed_seconds: float


class CatalogSeries(SourceModel):
    seriesid: int
    seriesname: str
    seriesstate: int
    newenergy: int = 0
    fctname: str = ""
    seriesimg: str = ""


class SpecListItem(SourceModel):
    id: int
    specname: str
    specstate: int


class SpecList(SourceModel):
    seriesid: int
    items: list[SpecListItem] = Field(default_factory=list)
