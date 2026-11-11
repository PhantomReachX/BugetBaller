# PuffPricer Engine

An automated price-per-gram ($/g) aggregator for High-THCA hemp flower using **Crawl4AI + Gemini 1.5 Flash**.

## Features & Taxonomy Rules
- **Pure THCA Only**: Automatically excludes all CBD, THCP, CBG, CBN, Delta-8, Delta-10, and plant mass / biomass.
- **Strict Normalization**:
  - `Premium` $\rightarrow$ mapped to `Indoor` (never `Exotic`).
  - `Trim` $\rightarrow$ normalized to `Shake`.
- **Unit Pricing ($/g)**: Every multi-tier listing (3.5g, 7g, 14g, 28g, lbs) is indexed by $/g.
- **Market Benchmarks**: Dynamic "% below average" calculation vs category medians.
- **State Legal Shipping**: Exclude vendors that restrict your state.

## Setup & Running

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   playwright install