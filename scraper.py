import asyncio
import json
import os
import re
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy

# ---------------------------------------------------------------------------
# Strict Canonical Taxonomies
# ---------------------------------------------------------------------------
class GradeEnum(str, Enum):
    INDOOR = "Indoor"
    EXOTIC = "Exotic"
    GREENHOUSE = "Greenhouse"
    OUTDOOR = "Outdoor"
    SMALLS = "Smalls"
    SHAKE = "Shake"
    CONCENTRATES = "Concentrates"

# Strict regex patterns for banned products (Alt-cannabinoids and biomass)
BANNED_REGEX = re.compile(
    r"\b(cbd|thcp|cbg|cbn|cgn|delta[\s\-_]?8|d8|delta[\s\-_]?10|d10|hhc)\b|"
    r"(plant\s+mass|plant\s+matter|biomass|crude\s+material|extraction\s+grade)",
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Pydantic Models & Data Sanitization
# ---------------------------------------------------------------------------
class SizeVariant(BaseModel):
    size_label: str = Field(description="Display label e.g., '3.5g', '14g', '1 oz', '1 lb'")
    weight_grams: float = Field(description="Weight in grams: 3.5, 7.0, 14.0, 28.0, 113.4, 226.8, 453.6")
    price: float = Field(description="Numeric USD price for this weight option")

class PuffPricerProduct(BaseModel):
    product_name: str = Field(description="Strain or product title")
    vendor: str = Field(description="Retailer or shop name")
    grade: str = Field(description="Flower grade/tier")
    strain_type: Optional[str] = Field(None, description="'Indica', 'Sativa', or 'Hybrid'")
    thca_percentage: Optional[float] = Field(None, description="THCA percentage as a float (e.g. 26.5)")
    coupon_code: Optional[str] = Field(None, description="Promo/coupon code if listed")
    coupon_discount_percent: Optional[float] = Field(0.0, description="Percentage off (e.g. 10 for 10% off)")
    restricted_states: List[str] = Field(default=[], description="2-letter state codes not shipped to (e.g. ['ID', 'OR'])")
    product_url: str = Field(description="Direct URL to product")
    sizes: List[SizeVariant] = Field(description="Available weight options")
    last_updated: Optional[str] = None

    # RULE 1: Grade Normalization
    @field_validator("grade", mode="before")
    def normalize_grade(cls, value: str) -> str:
        if not value:
            return GradeEnum.GREENHOUSE.value
        v = value.strip().lower()

        # Trim = Shake
        if any(term in v for term in ["shake", "trim", "sugar leaf", "ground"]):
            return GradeEnum.SHAKE.value

        # Smalls / Popcorn
        if any(term in v for term in ["small", "mini", "popcorn", "micro"]):
            return GradeEnum.SMALLS.value

        # Premium = Indoor (NOT Exotic)
        if any(term in v for term in ["premium", "top shelf", "indoor", "hydro"]):
            # If a vendor uses "Premium Exotic", prioritize Indoor rule
            if "exotic" in v and "premium" not in v:
                return GradeEnum.EXOTIC.value
            return GradeEnum.INDOOR.value

        if "exotic" in v:
            return GradeEnum.EXOTIC.value

        if "greenhouse" in v or "dep" in v:
            return GradeEnum.GREENHOUSE.value

        if "outdoor" in v or "sun grown" in v:
            return GradeEnum.OUTDOOR.value

        if "concentrate" in v or "badder" in v or "rosin" in v or "wax" in v:
            return GradeEnum.CONCENTRATES.value

        return GradeEnum.GREENHOUSE.value

    # RULE 2: Exclude Banned Cannabinoids & Biomass
    @model_validator(mode="after")
    def reject_invalid_cannabinoids_and_matter(self):
        combined = f"{self.product_name} {self.grade}".lower()
        match = BANNED_REGEX.search(combined)
        if match:
            raise ValueError(f"Excluded: Matched banned keyword '{match.group(0)}'")
        
        # Ensure sizes are valid and have positive weights
        if not self.sizes or any(s.weight_grams <= 0 for s in self.sizes):
            raise ValueError("Excluded: Listing missing valid weight tiers")
            
        return self

# ---------------------------------------------------------------------------
# Target URLs to Crawl
# ---------------------------------------------------------------------------
TARGET_URLS = [
    "https://example-hemp-site.com/flower",
    "https://another-thca-dispensary.com/collections/thca-flower"
]

# ---------------------------------------------------------------------------
# Crawler Runner
# ---------------------------------------------------------------------------
async def run_scraper():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Error: Missing GEMINI_API_KEY environment variable.")

    # Guided prompt instructs Gemini Flash before Pydantic sanitization
    instruction = (
        "Extract all High-THCA smokable flower and concentrate listings with all weight variants.\n"
        "STRICT FILTERING & CLASSIFICATION RULES:\n"
        "1. EXCLUDE COMPLETELY: Any product that is CBD, THCP, CBG, CBN, CGN, Delta-8 (D8), "
        "   Delta-10 (D10), HHC, or is described as 'plant mass', 'plant matter', or 'biomass'.\n"
        "2. ONLY extract THCA products.\n"
        "3. TAXONOMY:\n"
        "   - 'Premium' or 'Top-Shelf' MUST be classified as 'Indoor' (NEVER 'Exotic').\n"
        "   - 'Trim', 'Sugar leaf', or 'Pre-ground' MUST be classified as 'Shake'.\n"
        "   - 'Minis', 'Popcorn', or 'Micros' MUST be classified as 'Smalls'.\n"
        "4. Extract all available weight tiers (3.5g, 7g, 14g, 28g, 113.4g, 453.6g) with their listed price in USD."
    )

    llm_strategy = LLMExtractionStrategy(
        provider="gemini/gemini-1.5-flash",
        api_token=api_key,
        schema=PuffPricerProduct.model_json_schema(),
        extraction_type="schema",
        instruction=instruction,
        chunk_token_threshold=1800,
        overlap_rate=0.1
    )

    browser_config = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(extraction_strategy=llm_strategy, cache_mode=CacheMode.BYPASS)

    valid_products = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for url in TARGET_URLS:
            print(f"Crawling: {url}...")
            result = await crawler.arun(url=url, config=run_config)

            if result.success and result.extracted_content:
                try:
                    data = json.loads(result.extracted_content)
                    raw_items = data if isinstance(data, list) else [data]

                    for item in raw_items:
                        try:
                            # Enforce model validators (drops CBD, D8, D10, plant mass, etc.)
                            validated_product = PuffPricerProduct(**item)
                            validated_product.last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                            valid_products.append(validated_product.model_dump())
                        except Exception:
                            # Silently drops banned cannabinoids or biomass
                            continue
                except json.JSONDecodeError:
                    print(f"Failed to parse JSON for {url}")
            else:
                print(f"Failed crawling {url}: {result.error_message}")

    output_path = "prices.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(valid_products, f, indent=2)

    print(f"\nCompleted! Saved {len(valid_products)} valid THCA listings to {output_path}")

if __name__ == "__main__":
    asyncio.run(run_scraper())