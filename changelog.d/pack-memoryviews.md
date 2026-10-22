Fix the stdlib `unpack_array` fallback to count memoryview bytes instead of its
first dimension. Contiguous typed, multidimensional, and scalar views now decode
consistently with NumPy. Document that NumPy view writability follows the buffer.
