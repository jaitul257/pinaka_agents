"""Product data schema for Pinaka Jewellery.

Defines the product JSON structure including gem certification fields
required for FTC compliance.
"""

from pydantic import BaseModel, Field


class Materials(BaseModel):
    metal: str = Field(..., description="e.g. '14K Yellow Gold'")
    weight_grams: float
    diamond_type: list[str] = Field(..., description="e.g. ['lab-grown', 'VS1', 'F color']")
    total_carat: float


class PriceVariant(BaseModel):
    cost: float = Field(..., description="COGS — never expose to customers")
    retail: float


class Certification(BaseModel):
    certificate_number: str
    grading_lab: str = Field(..., description="GIA or IGI")
    carat_weight_certified: float
    clarity: str
    color: str


class Product(BaseModel):
    sku: str
    name: str
    category: str
    materials: Materials
    pricing: dict[str, PriceVariant] = Field(
        ..., description="Variant name -> pricing, e.g. 'lab-grown-7inch'"
    )
    story: str = Field(..., description="Brand story for this product")
    care_instructions: str
    occasions: list[str] = Field(default_factory=list)
    certification: Certification | None = None
    images: list[str] = Field(default_factory=list, description="Image file paths")
    tags: list[str] = Field(default_factory=list, description="Etsy search tags")
