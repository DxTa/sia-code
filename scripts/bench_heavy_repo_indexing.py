from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sia_code.cli import create_backend
from sia_code.config import Config
from sia_code.indexer.coordinator import IndexingCoordinator
from sia_code.storage.multi_repo import (
    build_repo_config,
    estimate_chunks,
    estimate_indexable_files,
    estimate_semantic_vectors,
    recommend_repo_timeout_seconds,
)


def bench_repo(repo_name: str) -> dict:
    repo = Path.home() / 'dev' / 'ai.platform' / repo_name
    out = Path.home() / 'dev' / 'ai.platform' / '.sia-code' / 'bench' / repo_name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    cfg = build_repo_config(Config(), repo.name)
    cfg.save(out / 'config.json')
    files = estimate_indexable_files(repo, cfg)
    chunks = estimate_chunks(repo, cfg)
    vectors = estimate_semantic_vectors(repo, cfg)
    timeout = recommend_repo_timeout_seconds(files, vectors)

    backend = create_backend(out, cfg, suppress_stdout_notices=True)
    backend.create_index()
    coord = IndexingCoordinator(cfg, backend)
    t0 = time.time()
    stats = coord.index_directory(repo)
    elapsed = time.time() - t0
    backend.close()
    return {
        'repo': repo_name,
        'model': cfg.embedding.model,
        'dims': cfg.embedding.dimensions,
        'granularity': cfg.embedding.granularity,
        'max_vectors_per_file': cfg.embedding.max_vectors_per_file,
        'files': files,
        'chunks': chunks,
        'vectors': vectors,
        'timeout': timeout,
        'elapsed_s': round(elapsed, 2),
        'stats': stats,
    }


if __name__ == '__main__':
    repos = sys.argv[1:] or [
        'ai.platform.forks.ai-toolkit',
        'ai.platform.annotation-suite.cvat',
    ]
    for repo_name in repos:
        print(json.dumps(bench_repo(repo_name)), flush=True)
