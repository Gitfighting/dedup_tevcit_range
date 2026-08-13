#!/usr/bin/env python3
"""OCP stub: satisfies `from skyplane.obj_store.object_store_interface import ObjectStoreInterface`
for LOCAL ablation without installing full skyplane. Do not edit author helper.py."""
from __future__ import annotations


class ObjectStoreInterface:
    def __init__(self, region: str, bucket: str):
        self.region = region
        self.bucket = bucket

    @classmethod
    def create(cls, region: str, bucket: str) -> "ObjectStoreInterface":
        return cls(region, bucket)

    def bucket_exists(self) -> bool:
        return True

    def create_bucket(self) -> None:
        return None

    def list_objects(self, *args, **kwargs):
        return []
