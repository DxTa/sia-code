"""Performance benchmarks for sia-code operations.

Measures latency, throughput, and resource efficiency.
"""

import time
import pytest
import subprocess

from .base_e2e_test import BaseE2ETest


class TestPerformanceBenchmarks(BaseE2ETest):
    """Measure latency, throughput, and resource usage."""

    def test_search_latency_benchmark(self, indexed_repo):
        """Benchmark search latency across multiple queries."""
        queries = [
            "command",
            "how to create a command",
            "parse arguments and validate",
            "error handling",
            "async operations with timeout",
        ]

        latencies = []

        for query in queries:
            # Warm-up run
            self.run_cli(["search", query, "-k", "5"], indexed_repo)

            # Measured run
            start = time.perf_counter()
            result = self.run_cli(["search", query, "-k", "10"], indexed_repo)
            elapsed = time.perf_counter() - start

            if result.returncode == 0:
                latencies.append(elapsed)

        # Calculate percentiles
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = (
            latencies[int(len(latencies) * 0.95)]
            if len(latencies) > 1
            else (latencies[0] if latencies else 0)
        )
        avg = sum(latencies) / len(latencies) if latencies else 0

        print("\n=== Search Latency Benchmark ===")
        print(f"Queries: {len(latencies)}")
        print(f"Average: {avg * 1000:.0f}ms")
        print(f"P50:     {p50 * 1000:.0f}ms")
        print(f"P95:     {p95 * 1000:.0f}ms")
        print(f"Min:     {min(latencies) * 1000:.0f}ms" if latencies else "N/A")
        print(f"Max:     {max(latencies) * 1000:.0f}ms" if latencies else "N/A")
        print()

        assert len(latencies) > 0, "Expected at least one successful search latency sample"
        # Relaxed thresholds for CI/local environments
        assert p50 < 5.0, f"P50 latency {p50:.2f}s exceeds 5s target"
        assert p95 < 10.0, f"P95 latency {p95:.2f}s exceeds 10s target"

    def test_index_throughput(self, initialized_repo):
        """Measure indexing throughput (lines per second)."""
        # Count lines of code in the repository
        try:
            loc_result = subprocess.run(
                [
                    "find",
                    ".",
                    "-type",
                    "f",
                    "(",
                    "-name",
                    "*.py",
                    "-o",
                    "-name",
                    "*.ts",
                    "-o",
                    "-name",
                    "*.js",
                    ")",
                    "-exec",
                    "wc",
                    "-l",
                    "{}",
                    "+",
                ],
                cwd=initialized_repo,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if loc_result.returncode == 0 and loc_result.stdout:
                # Parse total from last line
                lines = loc_result.stdout.strip().split("\n")
                if lines:
                    total_lines = int(lines[-1].strip().split()[0])
                else:
                    total_lines = 0
            else:
                total_lines = 0

        except (subprocess.TimeoutExpired, ValueError, IndexError):
            total_lines = 0

        # Time indexing
        start = time.perf_counter()
        result = self.run_cli(["index", "."], initialized_repo, timeout=600)
        elapsed = time.perf_counter() - start

        throughput = total_lines / elapsed if elapsed > 0 and total_lines > 0 else 0

        print("\n=== Index Throughput Benchmark ===")
        print(f"Total lines: {total_lines:,}")
        print(f"Index time:  {elapsed:.1f}s")
        print(f"Throughput:  {throughput:.0f} lines/sec")
        print()

        assert result.returncode == 0, "Indexing should succeed"

        # Very relaxed threshold - just ensure indexing completes
        if total_lines > 0:
            assert throughput > 10, f"Throughput {throughput:.0f} lines/sec unexpectedly low"

    def test_index_size_efficiency(self, indexed_repo):
        """Measure index size relative to source code size."""
        # Calculate source code size
        source_size = 0
        code_extensions = {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs"}

        for file_path in indexed_repo.rglob("*"):
            if file_path.is_file() and file_path.suffix in code_extensions:
                try:
                    source_size += file_path.stat().st_size
                except OSError:
                    pass

        # Get index size
        index_path = indexed_repo / ".sia-code" / "index.db"
        index_size = index_path.stat().st_size if index_path.exists() else 0

        ratio = index_size / source_size if source_size > 0 else 0

        print("\n=== Index Size Efficiency ===")
        print(f"Source size: {source_size / 1024:.1f} KB")
        print(f"Index size:  {index_size / 1024:.1f} KB")
        print(f"Ratio:       {ratio:.2f}x")
        print()

        # Relaxed threshold - index can be larger than source with embeddings
        assert ratio < 20.0, f"Index ratio {ratio:.2f}x exceeds 20x target"

    def test_search_result_count_consistency(self, indexed_repo):
        """Verify search returns consistent result counts."""
        query = "function"

        # Run same search multiple times
        counts = []
        for _ in range(3):
            results = self.search_json(query, indexed_repo, regex=True, limit=10)
            counts.append(len(results.get("results", [])))

        print("\n=== Search Consistency ===")
        print(f"Query: {query}")
        print(f"Result counts: {counts}")
        print()

        # Results should be consistent across runs
        assert len(set(counts)) <= 2, f"Result counts vary too much: {counts}"

    def test_concurrent_search_performance(self, indexed_repo):
        """Measure performance degradation with concurrent searches."""
        import concurrent.futures

        queries = ["command", "option", "argument", "help", "group"]

        # Sequential baseline
        start_seq = time.perf_counter()
        for query in queries:
            self.run_cli(["search", query, "-k", "5"], indexed_repo)
        elapsed_seq = time.perf_counter() - start_seq

        # Concurrent execution
        start_concurrent = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(self.run_cli, ["search", q, "-k", "5"], indexed_repo)
                for q in queries
            ]
            concurrent.futures.wait(futures)
        elapsed_concurrent = time.perf_counter() - start_concurrent

        speedup = elapsed_seq / elapsed_concurrent if elapsed_concurrent > 0 else 0

        print("\n=== Concurrent Search Performance ===")
        print(f"Sequential:  {elapsed_seq:.2f}s")
        print(f"Concurrent:  {elapsed_concurrent:.2f}s")
        print(f"Speedup:     {speedup:.2f}x")
        print()

        # Concurrent should be faster (some speedup expected)
        assert speedup > 0.8, f"Concurrent execution slower than sequential: {speedup:.2f}x"


class TestMemoryUsage(BaseE2ETest):
    """Memory usage benchmarks (requires psutil)."""

    @pytest.mark.skip(reason="Requires psutil package")
    def test_index_memory_footprint(self, initialized_repo):
        """Measure peak memory usage during indexing."""
        try:
            import psutil
            import os
        except ImportError:
            pytest.skip("psutil not installed")

        process = psutil.Process(os.getpid())

        # Measure memory before
        mem_before = process.memory_info().rss / 1024 / 1024  # MB

        # Run indexing
        result = self.run_cli(["index", "."], initialized_repo, timeout=600)

        # Measure memory after
        mem_after = process.memory_info().rss / 1024 / 1024  # MB
        mem_increase = mem_after - mem_before

        print("\n=== Memory Usage ===")
        print(f"Before: {mem_before:.1f} MB")
        print(f"After:  {mem_after:.1f} MB")
        print(f"Increase: {mem_increase:.1f} MB")
        print()

        assert result.returncode == 0
        # Memory increase should be reasonable (< 500MB for small repos)
        assert mem_increase < 500, f"Memory increase {mem_increase:.1f}MB exceeds 500MB"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
