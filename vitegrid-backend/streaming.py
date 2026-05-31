"""Server-Sent Events (SSE) Streaming for Document Reconstruction"""

import asyncio
import json
from typing import AsyncGenerator


async def sse_formatter(generator: AsyncGenerator[dict[str, str], None]) -> AsyncGenerator[str, None]:
    """Formats SSE event dicts as text according to SSE specification."""
    async for message in generator:
        yield f"event: {message['event']}\ndata: {message['data']}\n\n"


# Demo event generator for testing
async def demo_iteration_generator() -> AsyncGenerator[dict[str, str], None]:
    """Simulated optimization loop for demo purposes."""
    yield {
        "event": "parsing_complete",
        "data": json.dumps({"status": "ready", "elements_count": 42}),
    }

    for iteration in range(1, 4):
        divergence = max(0.01, 12.4 * (0.45 ** (iteration - 1)))
        yield {
            "event": "evaluation_loop",
            "data": json.dumps(
                {
                    "iteration": iteration,
                    "divergence_percentage": round(divergence, 2),
                    "candidate_render_b64": "data:image/png;base64,iVBOR...",
                    "diff_mask_b64": "data:image/png;base64,m098b...",
                }
            ),
        }

    yield {
        "event": "reconstruction_verified",
        "data": json.dumps(
            {
                "match_quality": "96.5%",
                "total_iterations": 3,
                "status": "converged",
            }
        ),
    }
