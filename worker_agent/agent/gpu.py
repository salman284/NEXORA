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
            __kernel void matmul(
                const int rows_a,
                const int cols_a,
                const int cols_b,
                __global const float *A,
                __global const float *B,
                __global float *C
            ) {
                int row = get_global_id(0);
                int col = get_global_id(1);
                if (row < rows_a && col < cols_b) {
                    float sum = 0.0f;
                    for (int k = 0; k < cols_a; k++) {
                        sum += A[row * cols_a + k] * B[k * cols_b + col];
                    }
                    C[row * cols_b + col] = sum;
                }
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
        if operation == "matmul":
            matrix_a = payload.get("matrix_a")
            matrix_b = payload.get("matrix_b")
            if not isinstance(matrix_a, list) or not isinstance(matrix_b, list):
                raise ValueError("matmul requires matrix_a and matrix_b lists")
            if not matrix_a or not matrix_b or not isinstance(matrix_a[0], list) or not isinstance(matrix_b[0], list):
                raise ValueError("matmul matrices must be non-empty 2D lists")
            a_arr = np.array(matrix_a, dtype=np.float32)
            b_arr = np.array(matrix_b, dtype=np.float32)
            if a_arr.shape[1] != b_arr.shape[0]:
                raise ValueError(f"Incompatible matrix dimensions: {a_arr.shape} and {b_arr.shape}")
            rows_a, cols_a = a_arr.shape
            rows_b, cols_b = b_arr.shape
            c_arr = np.empty((rows_a, cols_b), dtype=np.float32)
            a_buf = cl.Buffer(context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=a_arr)
            b_buf = cl.Buffer(context, flags.READ_ONLY | flags.COPY_HOST_PTR, hostbuf=b_arr)
            c_buf = cl.Buffer(context, flags.WRITE_ONLY, c_arr.nbytes)
            program.matmul(queue, (rows_a, cols_b), None, np.int32(rows_a), np.int32(cols_a), np.int32(cols_b), a_buf, b_buf, c_buf)
            cl.enqueue_copy(queue, c_arr, c_buf).wait()
            return {"matrix": c_arr.tolist()}
        raise ValueError(f"Unsupported OpenCL operation: {operation}")
    except OpenCLUnavailable:
        raise
    except Exception as error:
        raise OpenCLUnavailable(f"OpenCL execution failed: {error}") from error