from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class OpenCLUnavailable(RuntimeError):
    """OpenCL was requested but could not be used on this node."""


def execute_opencl(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import numpy as np
        import pyopencl as cl
    except ImportError as error:
        raise OpenCLUnavailable("OpenCL requires installed pyopencl, NumPy, and a working vendor runtime") from error

    try:
        devices = [device for platform in cl.get_platforms() for device in platform.get_devices()]
        if not devices:
            raise OpenCLUnavailable("No OpenCL devices were enumerated")
        device = devices[0]
        context = cl.Context([device])
        queue = cl.CommandQueue(context)
        program = cl.Program(
            context,
            """
            __kernel void square(__global const float *input, __global float *output) {
                output[0] = input[0] * input[0];
            }
            __kernel void copy_values(__global const float *input, __global float *output) {
                size_t index = get_global_id(0);
                output[index] = input[index];
            }
            """,
        ).build()
        flags = cl.mem_flags
        operation = payload.get("operation")
        if operation == "square":
            value = payload.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("square requires a numeric value")
            source = np.array([value], dtype=np.float32)
            output = np.empty_like(source)
            source_buffer = cl.Buffer(context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=source)
            output_buffer = cl.Buffer(context, flags.WRITE_ONLY, output.nbytes)
            program.square(queue, (1,), None, source_buffer, output_buffer)
            cl.enqueue_copy(queue, output, output_buffer).wait()
            return {"value": output.item()}
        if operation == "sum":
            values = payload.get("values")
            if not isinstance(values, list) or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                raise ValueError("sum requires a list of numeric values")
            source = np.asarray(values, dtype=np.float32)
            output = np.empty_like(source)
            source_buffer = cl.Buffer(context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=source)
            output_buffer = cl.Buffer(context, flags.WRITE_ONLY, output.nbytes)
            program.copy_values(queue, source.shape, None, source_buffer, output_buffer)
            cl.enqueue_copy(queue, output, output_buffer).wait()
            return {"value": output.sum().item()}
        raise ValueError(f"Unsupported OpenCL operation: {operation}")
    except OpenCLUnavailable:
        raise
    except Exception as error:
        raise OpenCLUnavailable(f"OpenCL execution failed: {error}") from error