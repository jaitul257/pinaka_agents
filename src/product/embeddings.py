"""Product embedding pipeline using Chroma and OpenAI embeddings.

Embeds product data for RAG queries. Pipeline is idempotent — can be
regenerated from source JSON in ~5 minutes if Chroma data is lost.
"""

import json
import logging
from pathlib import Path

import chromadb
from openai import OpenAI

from src.core.settings import settings
from src.product.schema import Product

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pinaka_products"


class ProductEmbeddings:
    """Manage product embeddings in Chroma vector DB."""

    def __init__(self, persist_dir: str = "./chroma_data"):
        self._chroma = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._chroma.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._openai = OpenAI(api_key=settings.openai_api_key)

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

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via OpenAI."""
        response = self._openai.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def embed_product(self, product: Product) -> None:
        """Embed a single product (upsert)."""
        text = self._product_to_text(product)
        embeddings = self._embed([text])

        self._collection.upsert(
            ids=[product.sku],
            embeddings=embeddings,
            documents=[text],
            metadatas=[{
                "sku": product.sku,
                "name": product.name,
                "category": product.category,
            }],
        )
        logger.info("Embedded product: %s", product.sku)

    def embed_all_from_directory(self, data_dir: str = "./data/products") -> int:
        """Load all product JSON files and embed them. Returns count."""
        count = 0
        for path in Path(data_dir).glob("*.json"):
            try:
                with open(path) as f:
                    product = Product(**json.load(f))
                self.embed_product(product)
                count += 1
            except Exception:
                logger.exception("Failed to embed product from %s", path)
        logger.info("Embedded %d products from %s", count, data_dir)
        return count

    def query(self, question: str, n_results: int = 3) -> list[dict]:
        """RAG query — find most relevant products for a question."""
        embeddings = self._embed([question])
        results = self._collection.query(
            query_embeddings=embeddings,
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

    def product_count(self) -> int:
        return self._collection.count()
