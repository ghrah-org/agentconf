"""从固定 models.json 快照重建离线种子；不下载，也不修改用户库。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from agentconf import CapabilityProfile, ModelsDevSource

EXAMPLES = [
    (
        "example-LLM",
        "llm",
        dict(
            input_modalities=["text"],
            output_modalities=["text"],
            max_context_length=1000000,
            max_output_length=128000,
            supports_reasoning=True,
            supports_function_calling=True,
            supports_vision=False,
        ),
    ),
    (
        "example-VLA",
        "llm",
        dict(
            input_modalities=["text", "image"],
            output_modalities=["text"],
            max_context_length=1000000,
            max_output_length=242000,
            supports_reasoning=True,
            supports_function_calling=True,
            supports_vision=True,
        ),
    ),
    (
        "example-embedding",
        "embedding",
        dict(
            input_modalities=["text"],
            output_modalities=["embedding"],
            max_input_length=8192,
            dimensions=1536,
        ),
    ),
    (
        "example-rerank",
        "rerank",
        dict(
            input_modalities=["text"],
            output_modalities=["score"],
            max_input_length=8192,
            top_n=10,
        ),
    ),
    (
        "example-TTS",
        "tts",
        dict(
            input_modalities=["text"],
            output_modalities=["audio"],
            languages=["zh", "en"],
        ),
    ),
    (
        "example-STT",
        "stt",
        dict(
            input_modalities=["audio"],
            output_modalities=["text"],
            languages=["zh", "en"],
        ),
    ),
    (
        "example-imagegen",
        "image_gen",
        dict(
            input_modalities=["text"],
            output_modalities=["image"],
            supported_sizes=["1024x1024"],
        ),
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--snapshot-at", required=True, help="固定快照时间，ISO8601，含时区"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("src/agentconf/data/catalog_seed.json")
    )
    args = parser.parse_args()
    at = datetime.fromisoformat(args.snapshot_at.replace("Z", "+00:00"))
    if at.tzinfo is None:
        parser.error("快照时间必须包含时区")
    snapshot = ModelsDevSource.parse(args.snapshot.read_bytes())
    profiles = [
        CapabilityProfile.model_validate(
            {
                **p.model_dump(),
                "origin": "upstream",
                "source_fetched_at": at,
                "created_at": at,
                "updated_at": at,
            }
        )
        for p in snapshot.profiles
    ]
    profiles.extend(
        CapabilityProfile(
            model_key=key,
            display_name=key,
            model_type=typ,
            origin="example",
            source="example",
            created_at=at,
            updated_at=at,
            **caps,
        )
        for key, typ, caps in EXAMPLES
    )
    seed = dict(
        schema_version=4,
        seed_version=3,
        generated_at=at.isoformat(),
        normalizer_version=snapshot.normalizer_version,
        source_url=snapshot.source_url,
        source_sha256=snapshot.source_sha256,
        profiles=[p.model_dump(mode="json") for p in profiles],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
