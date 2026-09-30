import os
import logging
from typing import List, Dict, Any
from google import genai
from google.genai import types
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

from arch_ai.config import Config

logger = logging.getLogger("agent_terminal.rag")

class GeminiEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        self.model_name = "gemini-embedding-001"
        
    def __call__(self, input: Documents) -> Embeddings:
        # Batch embed documents
        embeddings = []
        # The genai models.embed_content accepts lists in some versions, but we'll do it individually to be safe
        for text in input:
            try:
                # Truncate text if it's too long for embedding
                safe_text = text[:8000]
                result = self.client.models.embed_content(
                    model=self.model_name,
                    contents=safe_text
                )
                embeddings.append(result.embeddings[0].values)
            except Exception as e:
                logger.error(f"Embedding error: {e}")
                # Fallback to zero vector if API fails to keep shapes consistent
                embeddings.append([0.0] * 3072)
        return embeddings

class RAGManager:
    """Manages semantic search across the codebase using ChromaDB and Gemini embeddings."""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, persist_dir="~/.agent_terminal/chroma_db"):
        self.persist_dir = os.path.expanduser(persist_dir)
        os.makedirs(self.persist_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.embedding_fn = GeminiEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name="workspace_codebase", 
            embedding_function=self.embedding_fn
        )
        
    def index_file(self, filepath: str) -> str:
        """Indexes a single file into the vector database."""
        try:
            resolved_path = os.path.abspath(os.path.expanduser(filepath))
            if not os.path.exists(resolved_path) or not os.path.isfile(resolved_path):
                return f"Error: File not found {filepath}"
                
            with open(resolved_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Chunking logic (simple 1000 char chunks with overlap)
            chunk_size = 1000
            overlap = 200
            chunks = []
            ids = []
            metadatas = []
            
            if not content.strip():
                return f"Skipped empty file {filepath}"
                
            for i in range(0, len(content), chunk_size - overlap):
                chunk = content[i:i + chunk_size]
                chunks.append(chunk)
                ids.append(f"{resolved_path}_chunk_{i}")
                metadatas.append({"filepath": resolved_path, "offset": i})
                
            # Upsert into chroma
            self.collection.upsert(
                documents=chunks,
                metadatas=metadatas,
                ids=ids
            )
            return f"Successfully indexed {len(chunks)} chunks from {filepath}"
        except Exception as e:
            return f"Error indexing {filepath}: {e}"
            
    def semantic_search(self, query: str, n_results: int = 5) -> str:
        """Searches the indexed codebase for the query."""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            if not results["documents"] or not results["documents"][0]:
                return "No relevant code snippets found. Make sure you have indexed the directory first."
                
            output = [f"Semantic Search Results for: '{query}'\n"]
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i]
                filepath = meta.get("filepath", "Unknown")
                output.append(f"--- File: {filepath} ---")
                output.append(doc)
                output.append("-" * 40)
                
            return "\n".join(output)
        except Exception as e:
            return f"Error searching codebase: {e}"

