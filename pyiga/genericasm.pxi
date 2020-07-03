# file generated by generate-assemblers.py

################################################################################
# 2D Assemblers
################################################################################

cdef class BaseAssembler2D:
    cdef readonly int arity
    cdef int nqp
    cdef size_t[2] S0_ndofs
    cdef ssize_t[:,::1] S0_meshsupp0
    cdef double[:, :, ::1] S0_C0
    cdef ssize_t[:,::1] S0_meshsupp1
    cdef double[:, :, ::1] S0_C1
    cdef size_t[2] S1_ndofs
    cdef ssize_t[:,::1] S1_meshsupp0
    cdef double[:, :, ::1] S1_C0
    cdef ssize_t[:,::1] S1_meshsupp1
    cdef double[:, :, ::1] S1_C1
    cdef readonly tuple kvs
    cdef object _geo
    cdef tuple gaussgrid

    cdef double entry_impl(self, size_t[2] i, size_t[2] j) nogil:
        return -9999.99  # Not implemented

    cpdef double entry1(self, size_t i):
        """Compute an entry of the vector to be assembled."""
        if self.arity != 1:
            return 0.0
        cdef size_t[2] I
        with nogil:
            from_seq2(i, self.S0_ndofs, I)
            return self.entry_impl(I, <size_t*>0)

    cpdef double entry(self, size_t i, size_t j):
        """Compute an entry of the matrix."""
        if self.arity != 2:
            return 0.0
        cdef size_t[2] I, J
        with nogil:
            from_seq2(i, self.S1_ndofs, I)
            from_seq2(j, self.S0_ndofs, J)
            return self.entry_impl(I, J)

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef void multi_entries_chunk(self, size_t[:,::1] idx_arr, double[::1] out) nogil:
        if self.arity != 2:
            return
        cdef size_t[2] I, J
        cdef size_t k

        for k in range(idx_arr.shape[0]):
            from_seq2(idx_arr[k,0], self.S1_ndofs, I)
            from_seq2(idx_arr[k,1], self.S0_ndofs, J)
            out[k] = self.entry_impl(I, J)

    def multi_entries1(self, indices):
        """Compute all entries given by `indices`.

        Args:
            indices: a sequence or `ndarray` of vector indices to compute
        """
        if self.arity != 1:
            return None
        cdef size_t[::1] idx_arr
        if isinstance(indices, np.ndarray):
            idx_arr = np.asarray(indices, order='C', dtype=np.uintp)
        else:   # possibly given as iterator
            idx_arr = np.array(list(indices), dtype=np.uintp)

        cdef double[::1] result = np.empty(idx_arr.shape[0])
        cdef int i
        for i in range(idx_arr.shape[0]):
            result[i] = self.entry1(idx_arr[i])
        return result

    def multi_entries(self, indices):
        """Compute all entries given by `indices`.

        Args:
            indices: a sequence of `(i,j)` pairs or an `ndarray`
            of size `N x 2`.
        """
        if self.arity == 1:
            return self.multi_entries1(indices)
        elif self.arity != 2:
            return None
        cdef size_t[:,::1] idx_arr
        if isinstance(indices, np.ndarray):
            idx_arr = np.asarray(indices, order='C', dtype=np.uintp)
        else:   # possibly given as iterator
            idx_arr = np.array(list(indices), dtype=np.uintp)

        cdef double[::1] result = np.empty(idx_arr.shape[0])

        num_threads = pyiga.get_max_threads()
        if num_threads <= 1:
            self.multi_entries_chunk(idx_arr, result)
        else:
            thread_pool = get_thread_pool()

            def asm_chunk(idxchunk, out):
                cdef size_t[:, ::1] idxchunk_ = idxchunk
                cdef double[::1] out_ = out
                with nogil:
                    self.multi_entries_chunk(idxchunk_, out_)

            results = thread_pool.map(asm_chunk,
                        chunk_tasks(idx_arr, num_threads),
                        chunk_tasks(result, num_threads))
            list(results)   # wait for threads to finish
        return result

    @cython.boundscheck(False)
    @cython.wraparound(False)
    def assemble_vector(self):
        if self.arity != 1:
            return None
        result = np.empty(tuple(self.S0_ndofs), order='C')
        cdef double[:, ::1] _result = result
        cdef double* out = &_result[ 0, 0 ]

        cdef size_t[2] I, zero
        zero[0] = zero[1] = 0
        I[0] = I[1] = 0
        with nogil:
            while True:
               out[0] = self.entry_impl(I, <size_t*>0)
               out += 1
               if not next_lexicographic2(I, zero, self.S0_ndofs):
                   break
        return result

    def entry_func_ptr(self):
        return pycapsule.PyCapsule_New(<void*>_entry_func_2d, "entryfunc", NULL)


@cython.boundscheck(False)
@cython.wraparound(False)
def generic_assemble_core_2d(BaseAssembler2D asm, bidx, bint symmetric=False):
    if asm.arity != 2:
        return None
    cdef unsigned[:, ::1] bidx0, bidx1
    cdef long mu0, mu1, MU0, MU1
    cdef double[:, ::1] entries

    bidx0, bidx1 = bidx
    MU0, MU1 = bidx0.shape[0], bidx1.shape[0]

    cdef size_t[::1] transp0, transp1
    if symmetric:
        transp0 = get_transpose_idx_for_bidx(bidx0)
        transp1 = get_transpose_idx_for_bidx(bidx1)
    else:
        transp0 = transp1 = None

    entries = np.zeros((MU0, MU1))

    cdef int num_threads = pyiga.get_max_threads()

    for mu0 in prange(MU0, num_threads=num_threads, nogil=True):
        _asm_core_2d_kernel(asm, symmetric,
            bidx0, bidx1,
            transp0, transp1,
            entries,
            mu0)
    return entries

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
cdef void _asm_core_2d_kernel(
    BaseAssembler2D asm,
    bint symmetric,
    unsigned[:, ::1] bidx0, unsigned[:, ::1] bidx1,
    size_t[::1] transp0, size_t[::1] transp1,
    double[:, ::1] entries,
    long _mu0
) nogil:
    cdef size_t[2] i, j
    cdef int diag0, diag1
    cdef double entry
    cdef long mu0, mu1, MU0, MU1

    mu0 = _mu0
    MU0, MU1 = bidx0.shape[0], bidx1.shape[0]

    i[0] = bidx0[mu0, 0]
    j[0] = bidx0[mu0, 1]

    if symmetric:
        diag0 = <int>j[0] - <int>i[0]
        if diag0 > 0:       # block is above diagonal?
            return

    for mu1 in range(MU1):
        i[1] = bidx1[mu1, 0]
        j[1] = bidx1[mu1, 1]

        if symmetric:
            diag1 = <int>j[1] - <int>i[1]
            if diag0 == 0 and diag1 > 0:
                continue

        entry = asm.entry_impl(i, j)
        entries[mu0, mu1] = entry

        if symmetric:
            if diag0 != 0 or diag1 != 0:     # are we off the diagonal?
                entries[ transp0[mu0], transp1[mu1] ] = entry   # then also write into the transposed entry


# helper function for fast low-rank assembler
cdef double _entry_func_2d(size_t i, size_t j, void * data):
    return (<BaseAssembler2D>data).entry(i, j)



cdef class BaseVectorAssembler2D:
    cdef readonly int arity
    cdef int nqp
    cdef size_t[2] S0_ndofs
    cdef ssize_t[:,::1] S0_meshsupp0
    cdef double[:, :, ::1] S0_C0
    cdef ssize_t[:,::1] S0_meshsupp1
    cdef double[:, :, ::1] S0_C1
    cdef size_t[2] S1_ndofs
    cdef ssize_t[:,::1] S1_meshsupp0
    cdef double[:, :, ::1] S1_C0
    cdef ssize_t[:,::1] S1_meshsupp1
    cdef double[:, :, ::1] S1_C1
    cdef size_t[2] numcomp  # number of vector components for trial and test functions
    cdef readonly tuple kvs
    cdef object _geo
    cdef tuple gaussgrid

    def num_components(self):
        return self.numcomp[0], self.numcomp[1]

    @cython.boundscheck(False)
    @cython.wraparound(False)
    @cython.cdivision(True)
    cdef inline void from_seq(self, size_t i, size_t[3] out) nogil:
        out[2] = i % self.numcomp[0]
        i /= self.numcomp[0]
        out[1] = i % self.S0_ndofs[1]
        i /= self.S0_ndofs[1]
        out[0] = i

    cdef void entry_impl(self, size_t[2] i, size_t[2] j, double result[]) nogil:
        pass

    @cython.boundscheck(False)
    @cython.wraparound(False)
    def assemble_vector(self):
        if self.arity != 1:
            return None
        result = np.zeros(tuple(self.S0_ndofs) + (self.numcomp[0],), order='C')
        cdef double[:, :, ::1] _result = result
        cdef double* out = &_result[ 0, 0, 0 ]

        cdef size_t[2] I, zero
        zero[0] = zero[1] = 0
        I[0] = I[1] = 0
        with nogil:
            while True:
               self.entry_impl(I, <size_t*>0, out)
               out += self.numcomp[0]
               if not next_lexicographic2(I, zero, self.S0_ndofs):
                   break
        return result


@cython.boundscheck(False)
@cython.wraparound(False)
def generic_assemble_core_vec_2d(BaseVectorAssembler2D asm, bidx, bint symmetric=False):
    if asm.arity != 2:
        return None
    cdef unsigned[:, ::1] bidx0, bidx1
    cdef long mu0, mu1, MU0, MU1
    cdef double[:, :, ::1] entries
    cdef size_t[2] numcomp

    bidx0, bidx1 = bidx
    MU0, MU1 = bidx0.shape[0], bidx1.shape[0]

    cdef size_t[::1] transp0, transp1
    if symmetric:
        transp0 = get_transpose_idx_for_bidx(bidx0)
        transp1 = get_transpose_idx_for_bidx(bidx1)
    else:
        transp0 = transp1 = None

    numcomp[:] = asm.num_components()
    entries = np.zeros((MU0, MU1, numcomp[0]*numcomp[1]))

    cdef int num_threads = pyiga.get_max_threads()

    for mu0 in prange(MU0, num_threads=num_threads, nogil=True):
        _asm_core_vec_2d_kernel(asm, symmetric,
            bidx0, bidx1,
            transp0, transp1,
            numcomp,
            entries,
            mu0)
    return entries

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
cdef void _asm_core_vec_2d_kernel(
    BaseVectorAssembler2D asm,
    bint symmetric,
    unsigned[:, ::1] bidx0, unsigned[:, ::1] bidx1,
    size_t[::1] transp0, size_t[::1] transp1,
    size_t[2] numcomp,
    double[:, :, ::1] entries,
    long _mu0
) nogil:
    cdef size_t[2] i, j
    cdef int diag0, diag1
    cdef long mu0, mu1, MU0, MU1
    cdef int row, col

    mu0 = _mu0
    MU0, MU1 = bidx0.shape[0], bidx1.shape[0]

    i[0] = bidx0[mu0, 0]
    j[0] = bidx0[mu0, 1]

    if symmetric:
        diag0 = <int>j[0] - <int>i[0]
        if diag0 > 0:       # block is above diagonal?
            return

    for mu1 in range(MU1):
        i[1] = bidx1[mu1, 0]
        j[1] = bidx1[mu1, 1]

        if symmetric:
            diag1 = <int>j[1] - <int>i[1]
            if diag0 == 0 and diag1 > 0:
                continue

        asm.entry_impl(i, j, &entries[ mu0, mu1, 0 ])

        if symmetric:
            if diag0 != 0 or diag1 != 0:     # are we off the diagonal?
                for row in range(numcomp[1]):
                    for col in range(numcomp[0]):
                        entries[transp0[mu0], transp1[mu1], col*numcomp[0] + row] = entries[mu0, mu1, row*numcomp[0] + col]

################################################################################
# 3D Assemblers
################################################################################

cdef class BaseAssembler3D:
    cdef readonly int arity
    cdef int nqp
    cdef size_t[3] S0_ndofs
    cdef ssize_t[:,::1] S0_meshsupp0
    cdef double[:, :, ::1] S0_C0
    cdef ssize_t[:,::1] S0_meshsupp1
    cdef double[:, :, ::1] S0_C1
    cdef ssize_t[:,::1] S0_meshsupp2
    cdef double[:, :, ::1] S0_C2
    cdef size_t[3] S1_ndofs
    cdef ssize_t[:,::1] S1_meshsupp0
    cdef double[:, :, ::1] S1_C0
    cdef ssize_t[:,::1] S1_meshsupp1
    cdef double[:, :, ::1] S1_C1
    cdef ssize_t[:,::1] S1_meshsupp2
    cdef double[:, :, ::1] S1_C2
    cdef readonly tuple kvs
    cdef object _geo
    cdef tuple gaussgrid

    cdef double entry_impl(self, size_t[3] i, size_t[3] j) nogil:
        return -9999.99  # Not implemented

    cpdef double entry1(self, size_t i):
        """Compute an entry of the vector to be assembled."""
        if self.arity != 1:
            return 0.0
        cdef size_t[3] I
        with nogil:
            from_seq3(i, self.S0_ndofs, I)
            return self.entry_impl(I, <size_t*>0)

    cpdef double entry(self, size_t i, size_t j):
        """Compute an entry of the matrix."""
        if self.arity != 2:
            return 0.0
        cdef size_t[3] I, J
        with nogil:
            from_seq3(i, self.S1_ndofs, I)
            from_seq3(j, self.S0_ndofs, J)
            return self.entry_impl(I, J)

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef void multi_entries_chunk(self, size_t[:,::1] idx_arr, double[::1] out) nogil:
        if self.arity != 2:
            return
        cdef size_t[3] I, J
        cdef size_t k

        for k in range(idx_arr.shape[0]):
            from_seq3(idx_arr[k,0], self.S1_ndofs, I)
            from_seq3(idx_arr[k,1], self.S0_ndofs, J)
            out[k] = self.entry_impl(I, J)

    def multi_entries1(self, indices):
        """Compute all entries given by `indices`.

        Args:
            indices: a sequence or `ndarray` of vector indices to compute
        """
        if self.arity != 1:
            return None
        cdef size_t[::1] idx_arr
        if isinstance(indices, np.ndarray):
            idx_arr = np.asarray(indices, order='C', dtype=np.uintp)
        else:   # possibly given as iterator
            idx_arr = np.array(list(indices), dtype=np.uintp)

        cdef double[::1] result = np.empty(idx_arr.shape[0])
        cdef int i
        for i in range(idx_arr.shape[0]):
            result[i] = self.entry1(idx_arr[i])
        return result

    def multi_entries(self, indices):
        """Compute all entries given by `indices`.

        Args:
            indices: a sequence of `(i,j)` pairs or an `ndarray`
            of size `N x 2`.
        """
        if self.arity == 1:
            return self.multi_entries1(indices)
        elif self.arity != 2:
            return None
        cdef size_t[:,::1] idx_arr
        if isinstance(indices, np.ndarray):
            idx_arr = np.asarray(indices, order='C', dtype=np.uintp)
        else:   # possibly given as iterator
            idx_arr = np.array(list(indices), dtype=np.uintp)

        cdef double[::1] result = np.empty(idx_arr.shape[0])

        num_threads = pyiga.get_max_threads()
        if num_threads <= 1:
            self.multi_entries_chunk(idx_arr, result)
        else:
            thread_pool = get_thread_pool()

            def asm_chunk(idxchunk, out):
                cdef size_t[:, ::1] idxchunk_ = idxchunk
                cdef double[::1] out_ = out
                with nogil:
                    self.multi_entries_chunk(idxchunk_, out_)

            results = thread_pool.map(asm_chunk,
                        chunk_tasks(idx_arr, num_threads),
                        chunk_tasks(result, num_threads))
            list(results)   # wait for threads to finish
        return result

    @cython.boundscheck(False)
    @cython.wraparound(False)
    def assemble_vector(self):
        if self.arity != 1:
            return None
        result = np.empty(tuple(self.S0_ndofs), order='C')
        cdef double[:, :, ::1] _result = result
        cdef double* out = &_result[ 0, 0, 0 ]

        cdef size_t[3] I, zero
        zero[0] = zero[1] = zero[2] = 0
        I[0] = I[1] = I[2] = 0
        with nogil:
            while True:
               out[0] = self.entry_impl(I, <size_t*>0)
               out += 1
               if not next_lexicographic3(I, zero, self.S0_ndofs):
                   break
        return result

    def entry_func_ptr(self):
        return pycapsule.PyCapsule_New(<void*>_entry_func_3d, "entryfunc", NULL)


@cython.boundscheck(False)
@cython.wraparound(False)
def generic_assemble_core_3d(BaseAssembler3D asm, bidx, bint symmetric=False):
    if asm.arity != 2:
        return None
    cdef unsigned[:, ::1] bidx0, bidx1, bidx2
    cdef long mu0, mu1, mu2, MU0, MU1, MU2
    cdef double[:, :, ::1] entries

    bidx0, bidx1, bidx2 = bidx
    MU0, MU1, MU2 = bidx0.shape[0], bidx1.shape[0], bidx2.shape[0]

    cdef size_t[::1] transp0, transp1, transp2
    if symmetric:
        transp0 = get_transpose_idx_for_bidx(bidx0)
        transp1 = get_transpose_idx_for_bidx(bidx1)
        transp2 = get_transpose_idx_for_bidx(bidx2)
    else:
        transp0 = transp1 = transp2 = None

    entries = np.zeros((MU0, MU1, MU2))

    cdef int num_threads = pyiga.get_max_threads()

    for mu0 in prange(MU0, num_threads=num_threads, nogil=True):
        _asm_core_3d_kernel(asm, symmetric,
            bidx0, bidx1, bidx2,
            transp0, transp1, transp2,
            entries,
            mu0)
    return entries

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
cdef void _asm_core_3d_kernel(
    BaseAssembler3D asm,
    bint symmetric,
    unsigned[:, ::1] bidx0, unsigned[:, ::1] bidx1, unsigned[:, ::1] bidx2,
    size_t[::1] transp0, size_t[::1] transp1, size_t[::1] transp2,
    double[:, :, ::1] entries,
    long _mu0
) nogil:
    cdef size_t[3] i, j
    cdef int diag0, diag1, diag2
    cdef double entry
    cdef long mu0, mu1, mu2, MU0, MU1, MU2

    mu0 = _mu0
    MU0, MU1, MU2 = bidx0.shape[0], bidx1.shape[0], bidx2.shape[0]

    i[0] = bidx0[mu0, 0]
    j[0] = bidx0[mu0, 1]

    if symmetric:
        diag0 = <int>j[0] - <int>i[0]
        if diag0 > 0:       # block is above diagonal?
            return

    for mu1 in range(MU1):
        i[1] = bidx1[mu1, 0]
        j[1] = bidx1[mu1, 1]

        if symmetric:
            diag1 = <int>j[1] - <int>i[1]
            if diag0 == 0 and diag1 > 0:
                continue

        for mu2 in range(MU2):
            i[2] = bidx2[mu2, 0]
            j[2] = bidx2[mu2, 1]

            if symmetric:
                diag2 = <int>j[2] - <int>i[2]
                if diag0 == 0 and diag1 == 0 and diag2 > 0:
                    continue

            entry = asm.entry_impl(i, j)
            entries[mu0, mu1, mu2] = entry

            if symmetric:
                if diag0 != 0 or diag1 != 0 or diag2 != 0:     # are we off the diagonal?
                    entries[ transp0[mu0], transp1[mu1], transp2[mu2] ] = entry   # then also write into the transposed entry


# helper function for fast low-rank assembler
cdef double _entry_func_3d(size_t i, size_t j, void * data):
    return (<BaseAssembler3D>data).entry(i, j)



cdef class BaseVectorAssembler3D:
    cdef readonly int arity
    cdef int nqp
    cdef size_t[3] S0_ndofs
    cdef ssize_t[:,::1] S0_meshsupp0
    cdef double[:, :, ::1] S0_C0
    cdef ssize_t[:,::1] S0_meshsupp1
    cdef double[:, :, ::1] S0_C1
    cdef ssize_t[:,::1] S0_meshsupp2
    cdef double[:, :, ::1] S0_C2
    cdef size_t[3] S1_ndofs
    cdef ssize_t[:,::1] S1_meshsupp0
    cdef double[:, :, ::1] S1_C0
    cdef ssize_t[:,::1] S1_meshsupp1
    cdef double[:, :, ::1] S1_C1
    cdef ssize_t[:,::1] S1_meshsupp2
    cdef double[:, :, ::1] S1_C2
    cdef size_t[2] numcomp  # number of vector components for trial and test functions
    cdef readonly tuple kvs
    cdef object _geo
    cdef tuple gaussgrid

    def num_components(self):
        return self.numcomp[0], self.numcomp[1]

    @cython.boundscheck(False)
    @cython.wraparound(False)
    @cython.cdivision(True)
    cdef inline void from_seq(self, size_t i, size_t[4] out) nogil:
        out[3] = i % self.numcomp[0]
        i /= self.numcomp[0]
        out[2] = i % self.S0_ndofs[2]
        i /= self.S0_ndofs[2]
        out[1] = i % self.S0_ndofs[1]
        i /= self.S0_ndofs[1]
        out[0] = i

    cdef void entry_impl(self, size_t[3] i, size_t[3] j, double result[]) nogil:
        pass

    @cython.boundscheck(False)
    @cython.wraparound(False)
    def assemble_vector(self):
        if self.arity != 1:
            return None
        result = np.zeros(tuple(self.S0_ndofs) + (self.numcomp[0],), order='C')
        cdef double[:, :, :, ::1] _result = result
        cdef double* out = &_result[ 0, 0, 0, 0 ]

        cdef size_t[3] I, zero
        zero[0] = zero[1] = zero[2] = 0
        I[0] = I[1] = I[2] = 0
        with nogil:
            while True:
               self.entry_impl(I, <size_t*>0, out)
               out += self.numcomp[0]
               if not next_lexicographic3(I, zero, self.S0_ndofs):
                   break
        return result


@cython.boundscheck(False)
@cython.wraparound(False)
def generic_assemble_core_vec_3d(BaseVectorAssembler3D asm, bidx, bint symmetric=False):
    if asm.arity != 2:
        return None
    cdef unsigned[:, ::1] bidx0, bidx1, bidx2
    cdef long mu0, mu1, mu2, MU0, MU1, MU2
    cdef double[:, :, :, ::1] entries
    cdef size_t[2] numcomp

    bidx0, bidx1, bidx2 = bidx
    MU0, MU1, MU2 = bidx0.shape[0], bidx1.shape[0], bidx2.shape[0]

    cdef size_t[::1] transp0, transp1, transp2
    if symmetric:
        transp0 = get_transpose_idx_for_bidx(bidx0)
        transp1 = get_transpose_idx_for_bidx(bidx1)
        transp2 = get_transpose_idx_for_bidx(bidx2)
    else:
        transp0 = transp1 = transp2 = None

    numcomp[:] = asm.num_components()
    entries = np.zeros((MU0, MU1, MU2, numcomp[0]*numcomp[1]))

    cdef int num_threads = pyiga.get_max_threads()

    for mu0 in prange(MU0, num_threads=num_threads, nogil=True):
        _asm_core_vec_3d_kernel(asm, symmetric,
            bidx0, bidx1, bidx2,
            transp0, transp1, transp2,
            numcomp,
            entries,
            mu0)
    return entries

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
cdef void _asm_core_vec_3d_kernel(
    BaseVectorAssembler3D asm,
    bint symmetric,
    unsigned[:, ::1] bidx0, unsigned[:, ::1] bidx1, unsigned[:, ::1] bidx2,
    size_t[::1] transp0, size_t[::1] transp1, size_t[::1] transp2,
    size_t[2] numcomp,
    double[:, :, :, ::1] entries,
    long _mu0
) nogil:
    cdef size_t[3] i, j
    cdef int diag0, diag1, diag2
    cdef long mu0, mu1, mu2, MU0, MU1, MU2
    cdef int row, col

    mu0 = _mu0
    MU0, MU1, MU2 = bidx0.shape[0], bidx1.shape[0], bidx2.shape[0]

    i[0] = bidx0[mu0, 0]
    j[0] = bidx0[mu0, 1]

    if symmetric:
        diag0 = <int>j[0] - <int>i[0]
        if diag0 > 0:       # block is above diagonal?
            return

    for mu1 in range(MU1):
        i[1] = bidx1[mu1, 0]
        j[1] = bidx1[mu1, 1]

        if symmetric:
            diag1 = <int>j[1] - <int>i[1]
            if diag0 == 0 and diag1 > 0:
                continue

        for mu2 in range(MU2):
            i[2] = bidx2[mu2, 0]
            j[2] = bidx2[mu2, 1]

            if symmetric:
                diag2 = <int>j[2] - <int>i[2]
                if diag0 == 0 and diag1 == 0 and diag2 > 0:
                    continue

            asm.entry_impl(i, j, &entries[ mu0, mu1, mu2, 0 ])

            if symmetric:
                if diag0 != 0 or diag1 != 0 or diag2 != 0:     # are we off the diagonal?
                    for row in range(numcomp[1]):
                        for col in range(numcomp[0]):
                            entries[transp0[mu0], transp1[mu1], transp2[mu2], col*numcomp[0] + row] = entries[mu0, mu1, mu2, row*numcomp[0] + col]
