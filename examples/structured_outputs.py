#!/usr/bin/env python3
"""Sophisticated structured outputs examples with advanced Pydantic features.

This module demonstrates real-world business scenarios using structured outputs
with the Claude Agent SDK. Each example showcases advanced Pydantic modeling
techniques including enums, validators, computed fields, and complex nesting.

Requirements:
- Claude Code CLI v2.x+ with structured outputs support
- pydantic >= 2.0 for advanced features

Beta Feature:
Structured outputs require the beta header: "structured-outputs-2025-11-13"
"""

import asyncio
import re
import sys
from datetime import datetime
from enum import Enum

try:
    from pydantic import BaseModel, Field, computed_field, field_validator
except ImportError:
    print("Error: Pydantic is required for structured outputs examples")
    print("Install with: pip install pydantic")
    sys.exit(1)

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

# =============================================================================
# Example 1: E-Commerce Product Analytics
# =============================================================================


class ProductStatus(str, Enum):
    """Product availability status."""

    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"


class ProductCondition(str, Enum):
    """Product condition."""

    NEW = "new"
    REFURBISHED = "refurbished"
    USED = "used"


class ShippingMethod(str, Enum):
    """Available shipping methods."""

    STANDARD = "standard"
    EXPRESS = "express"
    OVERNIGHT = "overnight"
    INTERNATIONAL = "international"


class Supplier(BaseModel):
    """Supplier information."""

    name: str = Field(description="Supplier company name")
    contact_email: str | None = Field(
        default=None, description="Supplier contact email"
    )
    country: str = Field(description="Supplier country of origin")


class InventoryDetails(BaseModel):
    """Inventory tracking information."""

    quantity: int = Field(ge=0, description="Current stock quantity")
    warehouse_location: str = Field(description="Warehouse storage location")
    supplier: Supplier = Field(description="Product supplier information")

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Ensure quantity is non-negative."""
        if v < 0:
            raise ValueError("Quantity cannot be negative")
        return v


class Product(BaseModel):
    """E-commerce product with full details."""

    sku: str = Field(description="Stock keeping unit identifier")
    name: str = Field(description="Product name")
    regular_price: float = Field(gt=0, description="Regular retail price")
    sale_price: float | None = Field(
        default=None, gt=0, description="Current sale price"
    )
    status: ProductStatus = Field(description="Current availability status")
    condition: ProductCondition = Field(description="Product condition")
    shipping_method: ShippingMethod = Field(description="Primary shipping method")
    categories: list[str] = Field(
        default_factory=list, description="Product categories"
    )
    related_products: list[str] = Field(
        default_factory=list, description="Related product SKUs"
    )
    inventory: InventoryDetails = Field(description="Inventory details")

    @field_validator("sku")
    @classmethod
    def validate_sku(cls, v: str) -> str:
        """Validate SKU format (alphanumeric with hyphens)."""
        if not re.match(r"^[A-Z0-9\-]+$", v):
            raise ValueError("SKU must be alphanumeric with hyphens only")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def discount_percentage(self) -> float:
        """Calculate discount percentage from regular to sale price."""
        if self.sale_price is None:
            return 0.0
        discount = ((self.regular_price - self.sale_price) / self.regular_price) * 100
        return round(discount, 2)


async def example_ecommerce() -> None:
    """Demonstrate e-commerce product data extraction with advanced validation."""
    print("\n=== E-Commerce Product Analytics ===")
    print("Demonstrates: Enums, nested models, validators, computed fields")
    print("-" * 70)

    options = ClaudeAgentOptions(
        anthropic_beta="structured-outputs-2025-11-13",
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    prompt = (
        "Extract product details: 'Premium Wireless Headphones - SKU: WH-1000XM5, "
        "$349.99 (regularly $399.99), In Stock: 47 units at Warehouse A, "
        "Ships via Express, Categories: Electronics, Audio, Wireless, "
        "Supplier: AudioTech Inc (contact@audiotech.com, Japan), "
        "Condition: New, Related: WH-900XM4, EB-5000'"
    )

    async for message in query(prompt=prompt, options=options, output_format=Product):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"\nExtracted Product Data:\n{block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd:
            print(f"\nCost: ${message.total_cost_usd:.4f}")


# =============================================================================
# Example 2: Legal Document Analysis
# =============================================================================


class RiskLevel(str, Enum):
    """Contract risk assessment level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClauseType(str, Enum):
    """Type of contract clause."""

    PAYMENT = "payment"
    TERMINATION = "termination"
    LIABILITY = "liability"
    CONFIDENTIALITY = "confidentiality"
    DISPUTE_RESOLUTION = "dispute_resolution"


class JurisdictionType(str, Enum):
    """Legal jurisdiction type."""

    STATE = "state"
    FEDERAL = "federal"
    INTERNATIONAL = "international"


class ContractParty(BaseModel):
    """Contract party information."""

    name: str = Field(description="Legal entity name")
    jurisdiction: str = Field(description="State or country of incorporation")
    entity_type: str = Field(description="Type of legal entity")


class FinancialObligation(BaseModel):
    """Financial terms and obligations."""

    amount: float = Field(gt=0, description="Monetary amount")
    currency: str = Field(default="USD", description="Currency code")
    frequency: str = Field(description="Payment frequency")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        """Ensure amount is reasonable (under $10M for typical contracts)."""
        if v > 10_000_000:
            raise ValueError("Amount exceeds reasonable contract limits")
        return v


class ContractClause(BaseModel):
    """Individual contract clause with details."""

    clause_type: ClauseType = Field(description="Type of clause")
    summary: str = Field(description="Brief clause summary")
    risk_level: RiskLevel = Field(description="Risk assessment for this clause")
    financial_obligation: FinancialObligation | None = Field(
        default=None, description="Associated financial terms if applicable"
    )


class Contract(BaseModel):
    """Legal contract with risk assessment."""

    contract_name: str = Field(description="Contract title or type")
    party_a: ContractParty = Field(description="First contracting party")
    party_b: ContractParty = Field(description="Second contracting party")
    effective_date: str = Field(description="Contract effective date")
    expiration_date: str = Field(description="Contract expiration date")
    jurisdiction: str = Field(description="Governing law jurisdiction")
    jurisdiction_type: JurisdictionType = Field(description="Type of jurisdiction")
    clauses: list[ContractClause] = Field(description="Contract clauses")
    overall_risk: RiskLevel = Field(description="Overall contract risk assessment")

    @field_validator("effective_date", "expiration_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """Validate date is in reasonable format."""
        # Accept various date formats - this is simplified
        if not re.match(r"\d{4}-\d{2}-\d{2}|\w+ \d{1,2}, \d{4}", v):
            raise ValueError("Date must be in YYYY-MM-DD or 'Month DD, YYYY' format")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_financial_obligation(self) -> float:
        """Calculate total financial obligations across all clauses."""
        total = 0.0
        for clause in self.clauses:
            if clause.financial_obligation:
                total += clause.financial_obligation.amount
        return round(total, 2)


async def example_legal() -> None:
    """Demonstrate legal document analysis with risk assessment."""
    print("\n=== Legal Document Analysis ===")
    print("Demonstrates: Complex enums, deep nesting, validators, computed fields")
    print("-" * 70)

    options = ClaudeAgentOptions(
        anthropic_beta="structured-outputs-2025-11-13",
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    prompt = (
        "Analyze this contract: 'Master Services Agreement effective 2025-01-01, "
        "expires 2027-12-31. Party A: TechCorp LLC (Delaware corporation), "
        "Party B: ServiceProvider Inc (California LLC). Jurisdiction: New York state courts. "
        "Payment clause: Monthly fee of $50,000 USD, medium risk. "
        "Termination clause: 90 days notice required, low risk. "
        "Liability clause: Cap at $500,000, high risk due to low cap. "
        "Overall risk assessment: Medium.'"
    )

    async for message in query(prompt=prompt, options=options, output_format=Contract):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"\nContract Analysis:\n{block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd:
            print(f"\nCost: ${message.total_cost_usd:.4f}")


# =============================================================================
# Example 3: Scientific Research Paper Metadata
# =============================================================================


class ResearchType(str, Enum):
    """Type of research publication."""

    ORIGINAL_RESEARCH = "original_research"
    REVIEW = "review"
    META_ANALYSIS = "meta_analysis"
    CASE_STUDY = "case_study"
    COMMENTARY = "commentary"


class PeerReviewStatus(str, Enum):
    """Peer review status."""

    PEER_REVIEWED = "peer_reviewed"
    PREPRINT = "preprint"
    SUBMITTED = "submitted"


class AccessLevel(str, Enum):
    """Publication access level."""

    OPEN_ACCESS = "open_access"
    SUBSCRIPTION = "subscription"
    HYBRID = "hybrid"


class Author(BaseModel):
    """Research paper author information."""

    name: str = Field(description="Author full name")
    orcid: str | None = Field(default=None, description="ORCID identifier")
    affiliation: str = Field(description="Primary institutional affiliation")
    email: str | None = Field(default=None, description="Contact email")

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, v: str | None) -> str | None:
        """Validate ORCID format (XXXX-XXXX-XXXX-XXXX)."""
        if v is None:
            return v
        if not re.match(r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$", v):
            raise ValueError("ORCID must be in format XXXX-XXXX-XXXX-XXXX")
        return v


class ResearchMethodology(BaseModel):
    """Research methodology details."""

    method_type: str = Field(description="Type of methodology used")
    description: str = Field(description="Brief description of methodology")
    sample_size: int | None = Field(
        default=None, ge=1, description="Sample size if applicable"
    )


class FundingSource(BaseModel):
    """Research funding information."""

    agency: str = Field(description="Funding agency name")
    grant_number: str | None = Field(default=None, description="Grant identifier")
    amount: float | None = Field(default=None, ge=0, description="Funding amount")


class ResearchPaper(BaseModel):
    """Scientific research paper with comprehensive metadata."""

    title: str = Field(description="Paper title")
    doi: str = Field(description="Digital Object Identifier")
    authors: list[Author] = Field(description="List of paper authors")
    publication_year: int = Field(ge=1900, le=2030, description="Year of publication")
    journal: str = Field(description="Publication journal or venue")
    research_type: ResearchType = Field(description="Type of research")
    peer_review_status: PeerReviewStatus = Field(description="Peer review status")
    access_level: AccessLevel = Field(description="Access level")
    methodologies: list[ResearchMethodology] = Field(
        description="Research methodologies used"
    )
    keywords: list[str] = Field(description="Research keywords")
    citation_count: int = Field(ge=0, description="Number of citations")
    funding: list[FundingSource] | None = Field(
        default=None, description="Funding sources"
    )

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, v: str) -> str:
        """Validate DOI format."""
        if not re.match(r"^10\.\d{4,}/[\w\.\-]+$", v):
            raise ValueError("DOI must start with 10.XXXX/ followed by identifier")
        return v

    @field_validator("citation_count")
    @classmethod
    def validate_citation_count(cls, v: int) -> int:
        """Ensure citation count is reasonable (under 100k for most papers)."""
        if v > 100_000:
            raise ValueError("Citation count exceeds reasonable limits")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def impact_score(self) -> float:
        """Calculate simple impact score based on citations and years since publication."""
        current_year = datetime.now().year
        years_since_pub = max(1, current_year - self.publication_year)
        # Citations per year as a simple impact metric
        return round(self.citation_count / years_since_pub, 2)


async def example_research() -> None:
    """Demonstrate research paper metadata extraction with validation."""
    print("\n=== Scientific Research Paper Metadata ===")
    print("Demonstrates: Complex validators (DOI/ORCID), nested lists, computed fields")
    print("-" * 70)

    options = ClaudeAgentOptions(
        anthropic_beta="structured-outputs-2025-11-13",
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    prompt = (
        "Extract metadata: 'Deep Learning for Medical Imaging Analysis - "
        "DOI: 10.1038/s41586-024-07856-5. Authors: Dr. Sarah Chen "
        "(ORCID: 0000-0001-2345-6789, Stanford Medicine, schen@stanford.edu), "
        "Prof. James Liu (ORCID: 0000-0002-3456-7890, MIT CSAIL). "
        "Published: Nature, 2024. Type: Original Research. Peer-reviewed. "
        "Open Access. Methodologies: Convolutional Neural Networks (sample size: 10,000 images), "
        "Transfer Learning. Keywords: medical imaging, deep learning, neural networks, diagnostics. "
        "Citations: 127. Funding: NIH Grant R01-AI123456 ($2.5M), NSF Grant IIS-9876543.'"
    )

    async for message in query(prompt=prompt, options=options, output_format=ResearchPaper):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"\nResearch Paper Metadata:\n{block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd:
            print(f"\nCost: ${message.total_cost_usd:.4f}")


# =============================================================================
# Example 4: SaaS Feature Request Triage
# =============================================================================


class Priority(str, Enum):
    """Feature request priority level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ImpactArea(str, Enum):
    """Product areas impacted by feature."""

    SECURITY = "security"
    PERFORMANCE = "performance"
    UX = "user_experience"
    INTEGRATION = "integration"
    SCALABILITY = "scalability"
    COMPLIANCE = "compliance"


class Complexity(str, Enum):
    """Implementation complexity assessment."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class TeamAssignment(str, Enum):
    """Team responsible for implementation."""

    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    DEVOPS = "devops"
    SECURITY = "security"


class UserSegment(BaseModel):
    """User segment affected by feature."""

    segment_name: str = Field(description="Name of user segment")
    user_count: int = Field(ge=1, description="Number of users in segment")
    total_seats: int = Field(ge=1, description="Total licensed seats")

    @field_validator("user_count", "total_seats")
    @classmethod
    def validate_counts(cls, v: int) -> int:
        """Ensure user counts are reasonable (under 1M for typical SaaS)."""
        if v > 1_000_000:
            raise ValueError("User count exceeds reasonable limits")
        return v


class BusinessImpact(BaseModel):
    """Business impact metrics."""

    blocked_contracts_value: float = Field(
        ge=0, description="Value of contracts blocked by missing feature"
    )
    mrr_impact: float = Field(description="Monthly recurring revenue impact")
    estimated_churn_reduction: float = Field(
        ge=0, le=1, description="Estimated churn reduction (0-1)"
    )


class FeatureRequest(BaseModel):
    """SaaS feature request with triage details."""

    feature_name: str = Field(description="Name of requested feature")
    description: str = Field(description="Feature description")
    requesting_segments: list[UserSegment] = Field(
        description="User segments requesting this feature"
    )
    impact_areas: list[ImpactArea] = Field(description="Product areas impacted")
    priority: Priority = Field(description="Priority level")
    complexity: Complexity = Field(description="Implementation complexity")
    effort_points: int = Field(
        ge=1, le=100, description="Story points or effort estimate (1-100)"
    )
    estimated_weeks: int = Field(ge=1, description="Estimated weeks to complete")
    team_assignment: TeamAssignment = Field(description="Team to implement")
    business_impact: BusinessImpact = Field(description="Business impact metrics")

    @field_validator("effort_points")
    @classmethod
    def validate_effort_points(cls, v: int) -> int:
        """Ensure effort points are within fibonacci-like scale."""
        valid_points = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
        if v not in valid_points and v not in range(1, 101):
            raise ValueError(
                f"Effort points should use fibonacci scale: {valid_points}"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def priority_score(self) -> float:
        """Calculate priority score (higher = more urgent)."""
        priority_weights = {
            Priority.CRITICAL: 10,
            Priority.HIGH: 7,
            Priority.MEDIUM: 4,
            Priority.LOW: 1,
        }
        complexity_weights = {
            Complexity.LOW: 1,
            Complexity.MEDIUM: 2,
            Complexity.HIGH: 4,
            Complexity.VERY_HIGH: 8,
        }

        priority_val = priority_weights[self.priority]
        complexity_val = complexity_weights[self.complexity]

        # Score: (priority * business_impact) / complexity
        business_value = self.business_impact.blocked_contracts_value / 1000
        score = (priority_val * (1 + business_value)) / complexity_val

        return round(score, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def value_ratio(self) -> float:
        """Calculate value-to-effort ratio."""
        if self.effort_points == 0:
            return 0.0
        value = self.business_impact.mrr_impact
        return round(value / self.effort_points, 2)


async def example_saas() -> None:
    """Demonstrate SaaS feature request triage with priority scoring."""
    print("\n=== SaaS Feature Request Triage ===")
    print("Demonstrates: Multiple computed fields, validators, business logic")
    print("-" * 70)

    options = ClaudeAgentOptions(
        anthropic_beta="structured-outputs-2025-11-13",
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    prompt = (
        "Triage this feature request: 'SSO Integration with Okta and Azure AD. "
        "Add enterprise single sign-on supporting SAML 2.0 and OAuth 2.0. "
        "Requested by Enterprise segment (47 customers, 1,200 total seats). "
        "Impact areas: Security, Integration, User Experience. Priority: Critical. "
        "Complexity: High (requires new authentication service). "
        "Estimated effort: 34 story points, 8 weeks. Team: Backend. "
        "Business impact: Blocks $450,000 in contracts, MRR impact +$37,500, "
        "expected to reduce churn by 15%.'"
    )

    async for message in query(prompt=prompt, options=options, output_format=FeatureRequest):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"\nFeature Triage Result:\n{block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd:
            print(f"\nCost: ${message.total_cost_usd:.4f}")


# =============================================================================
# Main Runner
# =============================================================================


async def example_error_handling() -> None:
    """Demonstrate error handling for structured outputs."""
    print("\n=== Error Handling Examples ===")
    print("Demonstrates: Common errors and how to handle them")
    print("-" * 70)

    # Example 1: Invalid schema type
    print("\n1. Invalid schema type (not dict or Pydantic model):")
    try:
        options = ClaudeAgentOptions(
            anthropic_beta="structured-outputs-2025-11-13",
            permission_mode="bypassPermissions",
            max_turns=1,
        )
        async for _ in query(prompt="test", options=options, output_format="invalid"):  # type: ignore
            pass
    except TypeError as e:
        print(f"   ✓ Caught TypeError: {e}")

    # Example 2: Pydantic not installed
    print("\n2. Using Pydantic without installation:")
    print("   If Pydantic is not installed, you'll get an ImportError when")
    print("   trying to use Pydantic models. Use raw JSON schemas instead:")
    print("   output_format={'type': 'object', 'properties': {...}}")

    # Example 3: CLI doesn't support structured outputs yet
    print("\n3. Current limitation - CLI doesn't support schema passing yet:")
    print("   ⚠️  Even with valid schemas, structured outputs won't work until")
    print("   the CLI implements schema passing (see anthropics/claude-code#9058)")
    print("   The SDK will accept schemas but Claude will return markdown, not JSON.")

    class SimpleModel(BaseModel):
        message: str

    options = ClaudeAgentOptions(
        anthropic_beta="structured-outputs-2025-11-13",
        permission_mode="bypassPermissions",
        max_turns=1,
    )

    print("\n   Attempting query with valid schema (will return markdown):")
    async for message in query(
        prompt="Say hello", options=options, output_format=SimpleModel
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"   Response: {block.text[:100]}...")
                    print("   ⚠️  Note: This is markdown, not the structured JSON we requested")


async def main() -> None:
    """Run all sophisticated structured output examples."""
    print("=" * 70)
    print("Sophisticated Structured Outputs Examples")
    print("=" * 70)
    print("\nThese examples demonstrate advanced Pydantic features with real-world")
    print("business scenarios, including enums, validators, computed fields, and")
    print("complex nesting patterns.\n")

    try:
        await example_ecommerce()
        await example_legal()
        await example_research()
        await example_saas()
        await example_error_handling()

        print("\n" + "=" * 70)
        print("All examples completed successfully!")
        print("=" * 70)

    except Exception as e:
        print(f"\nError running examples: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
