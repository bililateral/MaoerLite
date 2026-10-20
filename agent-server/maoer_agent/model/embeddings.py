"""Local BGE embedding adapter; no knowledge or task storage dependencies."""
import asyncio
from pathlib import Path
from typing import Protocol


class Embeddings(Protocol):
    namespace: str
    async def documents(self, texts): ...
    async def query(self, text): ...


class LocalEmbeddings:
    namespace = 'bge-small-zh-v1.5:'

    def __init__(self, model_dir):
        self.model_dir = str(model_dir)
        self.encoder = None
        self.lock = asyncio.Lock()

    def encoder_sync(self):
        if self.encoder is None:
            from fastembed import TextEmbedding
            # Use the pinned artifact directly: Hub cache metadata contains host-specific paths.
            snapshot = Path(self.model_dir) / 'models--Qdrant--bge-small-zh-v1.5' / 'snapshots' / '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
            self.encoder = TextEmbedding('BAAI/bge-small-zh-v1.5', cache_dir=self.model_dir, threads=4,
                                         local_files_only=True, specific_model_path=str(snapshot))
        return self.encoder

    async def documents(self, texts):
        async with self.lock:
            return await asyncio.to_thread(lambda: [v.astype('float32').tobytes()
                for v in self.encoder_sync().embed(texts, batch_size=32)])

    async def query(self, text):
        async with self.lock:
            return await asyncio.to_thread(lambda: next(iter(self.encoder_sync().query_embed(text))))
