#
# Utility functions for multi-level block matrices and
# multilevel banded matrices.
#

import numpy as np
import scipy.sparse.linalg

from . import lowrank


################################################################################
# Multi-level banded matrix class
################################################################################

class MLBandedMatrix(scipy.sparse.linalg.LinearOperator):
    """Compact representation of a multi-level banded matrix.

    Many IgA matrices arising from tensor product bases have multi-level
    banded structure, meaning that they are block-structured, each block
    is banded, and the block pattern itself is banded. This allows
    compact storage of all coefficients in a dense matrix or tensor.
    See (Hofreither 2017) for details.

    Args:
        bs (seq): list of block sizes, one per level (dimension)
        bw (seq): list of bandwidths, one per level (dimension)
        data (ndarray): optionally, the data for the matrix can be
            specified. Otherwise, it is initialized to 0.
    """
    def __init__(self, bs, bw, data=None):
        self.bs = tuple(bs)
        self._total_bs = np.array([(b,b) for b in self.bs])
        self.bw = tuple(bw)
        self.L = len(bs)
        assert self.L == len(bw), \
            'Inconsistent dimensions for block sizes and bandwidths'
        self.sparsidx = tuple(compute_banded_sparsity(n, p)
                for (n,p) in zip(self.bs,bw))
        datashape = tuple(len(si) for si in self.sparsidx)
        if data is None:
            data = np.zeros(datashape)
        assert data.shape == datashape, 'Wrong shape of data tensor'
        self.data = np.asarray(data, order='C')
        self.bidx = make_block_indices(self.sparsidx, self._total_bs)
        N = np.prod(self.bs)
        scipy.sparse.linalg.LinearOperator.__init__(self,
                shape=(N,N), dtype=self.data.dtype)

    @property
    def nnz(self):
        """Return the number of nonzeros in a sparse matrix representation."""
        return self.data.size

    def asmatrix(self, format='csr'):
        """Return a sparse matrix representation in the given format."""
        if self.L == 2:
            A = inflate_2d(self.data, self.sparsidx[0], self.sparsidx[1],
                self.bs[0], self.bs[0], self.bs[1], self.bs[1])
        elif self.L == 3:
            A = inflate_3d(self.data, self.sparsidx, self._total_bs)
        else:
            assert False, 'dimension %d not implemented' % self.L
        return A.asformat(format)

    def _matvec(self, x):
        """Compute the matrix-vector product with vector `x`."""
        assert len(x) == self.shape[1], 'Invalid input size'
        if self.L == 2:
            y = np.zeros(len(x))
            ml_matvec_2d(self.data, self.bidx[0], self.bidx[1],
                self.bs[0], self.bs[0], self.bs[1], self.bs[1], x, y)
            return y
        elif self.L == 3:
            y = np.zeros(len(x))
            ml_matvec_3d(self.data, self.bidx, self._total_bs, x, y)
            return y
        else:
            return self.asmatrix().dot(x)


################################################################################
# Reordering and reindexing
################################################################################

def reorder(X, m1, n1):
    """Input X has m1 x n1 blocks of size m2 x n2, i.e.,
    total size m1*m2 x n1*n2.

    Output has m1*n1 rows of length m2*n2, where each row
    is one vectorized block of X.

    This implements the matrix reordering described in
    [Van Loan, Pitsianis 1993] for dense matrices."""
    (M,N) = X.shape
    m2 = M // m1
    n2 = N // n1
    assert M == m1*m2 and N == n1*n2, "Invalid block size"
    Y = np.empty((m1*n1, m2*n2))
    for i in range(m1):
        for j in range(n1):
            B = X[i*m2 : (i+1)*m2, j*n2 : (j+1)*n2]
            Y[i*n1 + j, :] = B.ravel('C')
    return Y

def reindex_from_reordered(i,j, m1,n1,m2,n2):
    """Convert (i,j) from an index into reorder(X, m1, n1) into the
    corresponding index into X (reordered to original).

    Arguments:
        i = row = block index           (0...m1*n1)
        j = column = index within block (0...m2*n2)

    Returns:
        a pair of indices with ranges `(0...m1*m2, 0...n1*n2)`
    """
    bi0, bi1 = i // n1, i % n1      # range: m1, n1
    ii0, ii1 = j // n2, j % n2      # range: m2, n2
    return (bi0*m2 + ii0, bi1*n2 + ii1)

def from_seq(i, dims):
    """Convert sequential (lexicographic) index into multiindex.

    Same as np.unravel_index(i, dims) except for returning a list.
    """
    L = len(dims)
    I = L * [0]
    for k in reversed(range(L)):
        mk = dims[k]
        I[k] = i % mk
        i //= mk
    return I

def to_seq(I, dims):
    """Convert multiindex into sequential (lexicographic) index.

    Same as np.ravel_multiindex(I, dims).
    """
    i = 0
    for k in range(len(dims)):
        i *= dims[k]
        i += I[k]
    return i

def reindex_to_multilevel(i, j, bs):
    """Convert sequential indices (i,j) of a multilevel matrix
    with L levels and block sizes bs into a multiindex with length L.
    """
    bs = np.array(bs, copy=False)   # bs has shape L x 2
    I, J = from_seq(i, bs[:,0]), from_seq(j, bs[:,1])  # multilevel indices
    return tuple(to_seq((I[k],J[k]), bs[k,:]) for k in range(bs.shape[0]))

def reindex_from_multilevel(M, bs):
    """Convert a multiindex M with length L into sequential indices (i,j)
    of a multilevel matrix with L levels and block sizes bs.

    This is the multilevel version of reindex_from_reordered, which does the
    same for the two-level case.

    Arguments:
        M: the multiindex to convert
        bs: the block sizes; ndarray of shape Lx2. Each row gives the sizes
            (rows and columns) of the blocks on the corresponding matrix level.
    """
    bs = np.array(bs, copy=False)  # bs has shape L x 2
    IJ = np.stack((from_seq(M[k], bs[k,:]) for k in range(len(M))), axis=0)
    return tuple(to_seq(IJ[:,m], bs[:,m]) for m in range(2))

def compute_banded_sparsity(n, bw):
    """Returns list of ravelled indices which are nonzero in a square,
    banded matrix of size n and bandwidth bw.

    This is identical to np.flatnonzero(X) of such a banded matrix X.
    """
    I = []
    for j in range(n):
        for i in range(max(0, j-bw), min(n, j+bw+1)):
            I.append(i + j*n)
    return np.array(I, dtype=int)



################################################################################
# Elementwise generators for ML-reordered sparse matrices
################################################################################

def ReorderedMatrixGenerator(multiasm, sparsidx, n1, n2):
    def multientryfunc(indices):
        return multiasm(
            [reindex_from_reordered(sparsidx[0][i], sparsidx[1][j], n1, n1, n2, n2)
                for (i,j) in indices])
    shp = tuple(len(si) for si in sparsidx)
    return lowrank.MatrixGenerator(shp[0], shp[1], multientryfunc=multientryfunc)

def ReorderedTensorGenerator(multiasm, sparsidx, bs):
    block_sizes = np.array([(b,b) for b in bs])
    L = len(sparsidx)
    assert L == block_sizes.shape[0]
    Ms = L * [None]
    def multientryfunc(indices):
        indices = list(indices)
        for n in range(len(indices)):
            for k in range(L):
                Ms[k] = sparsidx[k][indices[n][k]]
            indices[n] = reindex_from_multilevel(Ms, block_sizes)
        return multiasm(indices)
    shp = tuple(len(si) for si in sparsidx)
    return lowrank.TensorGenerator(shp, multientryfunc=multientryfunc)



# import optimized versions as well as some additional functions
from .mlmatrix_cy import *


