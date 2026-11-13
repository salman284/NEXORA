from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from pydantic import BaseModel, Field, model_validator


SUPPORTED_OPERATIONS = {"identity", "square", "sum", "matmul"}


class WorkloadManifest(BaseModel):
    schema_version: str = Field(pattern=r"^1$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    runtime: str = Field(pattern=r"^builtin$")
    backend: str = Field(default="cpu", pattern=r"^(cpu|opencl)$")
    execution_mode: str = Field(pattern=r"^task_parallel$")
    operation: str = Field(pattern=r"^(identity|square|sum|matmul)$")
    aggregation: str = Field(pattern=r"^collect$")
    output_format: str = Field(default="json", pattern=r"^json$")
    required_gpu_memory_bytes: int = Field(default=0, ge=0)
    required_cpu_threads: int = Field(default=1, ge=1, le=4096)
    required_ram_bytes: int = Field(default=0, ge=0)
    dependencies: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def reject_dependencies(self) -> "WorkloadManifest":
        if self.dependencies:
            raise ValueError("Dependency installation is not supported by the built-in adapter")
        return self


def validate_archive(data: bytes, filename: str) -> None:
    if not filename.lower().endswith(".zip"):
        raise ValueError("Only ZIP workload packages are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP package contains a corrupt member")
            for member in archive.infolist():
                path = member.filename.replace("\\", "/")
                if path.startswith("/") or ".." in path.split("/"):
                    raise ValueError("ZIP package contains an unsafe path")
    except zipfile.BadZipFile as error:
        raise ValueError("Uploaded package is not a valid ZIP archive") from error


def parse_manifest(raw_manifest: str) -> WorkloadManifest:
    try:
        value: Any = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        raise ValueError("Manifest must be valid JSON") from error
    return WorkloadManifest.model_validate(value)