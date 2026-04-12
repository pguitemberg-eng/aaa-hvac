"""
rag/rag_engine.py
ChromaDB RAG engine for HVAC knowledge retrieval.
"""

import os
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

_rag_engine = None


class RAGEngine:
    def __init__(self):
        db_path = os.getenv("CHROMA_DB_PATH", "./rag/chroma_db")
        collection_name = os.getenv("CHROMA_COLLECTION_NAME", "hvac_knowledge")
        self.client = chromadb.PersistentClient(path=db_path)
        self.ef = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef,
        )

    def retrieve(self, query: str, n_results: int = 3) -> list:
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(n_results, self.collection.count() or 1),
            )
            docs = results.get("documents", [[]])[0]
            return docs
        except Exception as e:
            print(f"[RAG] Retrieval error: {e}")
            return []

    def format_context(self, docs: list) -> str:
        if not docs:
            return "No specific HVAC knowledge retrieved. Use general HVAC expertise."
        return "\n\n".join(f"[{i+1}] {doc}" for i, doc in enumerate(docs))

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        try:
            self.collection.add(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata or {}],
            )
            print(f"[RAG] Added document: {doc_id}")
        except Exception as e:
            print(f"[RAG] Add error: {e}")


def get_rag_engine() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine