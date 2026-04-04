"""Product embedding pipeline using ChromaDB's built-in embeddings.

Uses ChromaDB's default all-MiniLM-L6-v2 model (ONNX, no API key needed).
Pipeline is idempotent — can be regenerated from Supabase if data is lost.
"""

import logging

from src.product.schema import Product

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pinaka_products"


class ProductEmbeddings:
    """Manage product embeddings in Chroma vector DB."""

    def __init__(self, persist_dir: str = "./chroma_data"):
        import chromadb

        self._chroma = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _product_to_text(self, product: Product) -> str:
        """Convert a product to a text representation for embedding."""
        parts = [
            f"Product: {product.name}",
            f"Category: {product.category}",
            f"Materials: {product.materials.metal}, {product.materials.total_carat}ct total",
            f"Diamond type: {', '.join(product.materials.diamond_type)}",
            f"Story: {product.story}",
            f"Care: {product.care_instructions}",
            f"Occasions: {', '.join(product.occasions)}",
        ]
        if product.certification:
            parts.append(
                f"Certification: {product.certification.grading_lab} "
                f"#{product.certification.certificate_number}, "
                f"{product.certification.carat_weight_certified}ct, "
                f"{product.certification.clarity}/{product.certification.color}"
            )
        return "\n".join(parts)

    def embed_product(self, product: Product) -> None:
        """Embed a single product (upsert). ChromaDB auto-embeds the document."""
        text = self._product_to_text(product)

        self._collection.upsert(
            ids=[product.sku],
            documents=[text],
            metadatas=[{
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
            }],
        )
        logger.info("Embedded product: %s", product.sku)

    def query(self, question: str, n_results: int = 3) -> list[dict]:
        """RAG query — find most relevant products for a question."""
        results = self._collection.query(
            query_texts=[question],
            n_results=n_results,
        )

        products = []
        if results["documents"]:
            for doc, meta, distance in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                products.append({
                    "document": doc,
                    "metadata": meta,
                    "similarity": 1 - distance,
                })
        return products

    def embed_shopify_product(self, shopify_product: dict) -> None:
        """Embed a product directly from Shopify API data (no Product schema needed)."""
        import re

        product_id = str(shopify_product.get("id", ""))
        title = shopify_product.get("title", "")
        if not product_id or not title:
            return

        parts = [f"Product: {title}"]
        if shopify_product.get("product_type"):
            parts.append(f"Category: {shopify_product['product_type']}")
        if shopify_product.get("body_html"):
            body = re.sub(r"<[^>]+>", "", shopify_product["body_html"])
            parts.append(f"Description: {body[:500]}")
        if shopify_product.get("tags"):
            parts.append(f"Tags: {shopify_product['tags']}")
        if shopify_product.get("vendor"):
            parts.append(f"Brand: {shopify_product['vendor']}")

        for variant in shopify_product.get("variants", [])[:5]:
            variant_parts = []
            if variant.get("title") and variant["title"] != "Default Title":
                variant_parts.append(variant["title"])
            if variant.get("price"):
                variant_parts.append(f"${variant['price']}")
            if variant_parts:
                parts.append(f"Variant: {', '.join(variant_parts)}")

        text = "\n".join(parts)

        self._collection.upsert(
            ids=[f"shopify-{product_id}"],
            documents=[text],
            metadatas=[{
                "shopify_product_id": product_id,
                "name": title,
                "category": shopify_product.get("product_type", ""),
            }],
        )
        logger.info("Embedded Shopify product: %s (%s)", title, product_id)

    def product_count(self) -> int:
        return self._collection.count()
