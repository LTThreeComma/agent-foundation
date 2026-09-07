"""Benchmark-only checksummed codecs; not a proposed persistence implementation."""

from __future__ import annotations

import lz4.frame
import zstandard

CODECS = ("raw", "zstd-1", "zstd-3", "lz4-0")


def encode(body: bytes, codec: str) -> bytes:
    if codec == "raw":
        return body
    if codec in ("zstd-1", "zstd-3"):
        return zstandard.ZstdCompressor(level=int(codec[-1]), write_checksum=True, write_content_size=True).compress(
            body
        )
    if codec == "lz4-0":
        return lz4.frame.compress(
            body,
            compression_level=0,
            block_size=lz4.frame.BLOCKSIZE_MAX64KB,
            block_linked=True,
            content_checksum=True,
            store_size=True,
        )
    raise ValueError(codec)


def decode(body: bytes, codec: str, expected_size: int) -> bytes:
    if codec == "raw":
        result = body
    elif codec in ("zstd-1", "zstd-3"):
        if zstandard.frame_content_size(body) != expected_size:
            raise ValueError("unexpected decoded size")
        result = zstandard.ZstdDecompressor().decompress(body, max_output_size=expected_size, allow_extra_data=False)
    elif codec == "lz4-0":
        if lz4.frame.get_frame_info(body)["content_size"] != expected_size:
            raise ValueError("unexpected decoded size")
        result, consumed = lz4.frame.decompress(body, return_bytes_read=True)
        if consumed != len(body):
            raise ValueError("trailing bytes")
    else:
        raise ValueError(codec)
    if len(result) != expected_size:
        raise ValueError("unexpected decoded size")
    return result
