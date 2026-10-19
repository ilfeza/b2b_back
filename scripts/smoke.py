"""Read-only, reproducible HTTP smoke/performance probe. Reports measurements, asserts no SLA."""

import argparse
import asyncio
import json
import statistics
import time

import httpx

from marketplace.b2b.integrations.fakes import DEMO_USER


async def run(args):
    if args.requests < 1 or args.concurrency < 1:
        raise ValueError("requests and concurrency must be positive")
    semaphore = asyncio.Semaphore(args.concurrency)
    paths = (
        "/seller/profile",
        "/seller/products",
        "/seller/posts",
        "/seller/dashboard",
        "/seller/delivery",
    )
    elapsed = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + args.prefix,
        headers={"X-User-Id": args.user},
        timeout=30,
    ) as client:

        async def sample(n):
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(paths[n % len(paths)])
                response.raise_for_status()
                if response.json()["error"] is not None:
                    raise RuntimeError("Unexpected error envelope")
                elapsed.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        await asyncio.gather(*(sample(n) for n in range(args.requests)))
        duration = time.perf_counter() - started
    ordered = sorted(elapsed)
    print(
        json.dumps(
            {
                "requests": len(elapsed),
                "concurrency": args.concurrency,
                "durationSeconds": round(duration, 3),
                "p50ms": round(statistics.median(elapsed), 2),
                "p95ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--prefix", default="/api")
    parser.add_argument("--user", default=str(DEMO_USER))
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=5)
    asyncio.run(run(parser.parse_args()))
