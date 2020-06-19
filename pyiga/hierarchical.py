# -*- coding: utf-8 -*-
"""This module contains support for dealing with hierarchical spline spaces and
truncated hierarchical B-splines (THB-splines).

The main user-facing class is :class:`HSpace`, which describes a hierarchical
spline space and supports HB- and THB-spline representations. The remaining
members of the module are utility functions and classes.

The implementation is loosely based on the approach described in [GV2018]_ and
the corresponding implementation in [GeoPDEs]_.

.. [GV2018] `Garau, Vazquez: "Algorithms for the implementation of adaptive
    isogeometric methods using hierarchical B-splines", 2018.
    <https://doi.org/10.1016/j.apnum.2017.08.006>`_
.. [GeoPDEs] http://rafavzqz.github.io/geopdes/

--------------
Module members
--------------
"""
import numpy as np
import scipy.sparse
import itertools

from . import bspline, utils, assemble

def _make_unique(L):
    """Return a list which contains the entries of `L` with consecutive duplicates removed."""
    n = len(L)
    if n == 0:
        return []
    else:
        U = [L[0]]
        last = U[0]
        for x in L[1:]:
            if x != last:
                U.append(x)
            last = x
        return U

def _compute_supported_functions(kv, meshsupp):
    """Compute an array containing for each cell the index of the first and
    one beyond the last function supported in it.
    """
    n = kv.numspans
    sf = np.zeros((n,2), dtype=meshsupp.dtype)
    sf[:,0] = kv.numdofs
    for j in range(meshsupp.shape[0]):
        for k in range(meshsupp[j,0], meshsupp[j,1]):
            sf[k,0] = min(sf[k,0], j)
            sf[k,1] = max(sf[k,1], j)
    sf[:,1] += 1
    return sf

class TPMesh:
    """A tensor product mesh described by a sequence of knot vectors."""
    def __init__(self, kvs):
        self.kvs = tuple(kvs)
        self.dim = len(kvs)
        self.numspans = [kv.numspans for kv in kvs]
        self.numel = np.prod(self.numspans)
        self.numdofs = [kv.numdofs for kv in kvs]
        self.numbf = np.prod(self.numdofs)
        self.meshsupp = tuple(kv.mesh_support_idx_all() for kv in self.kvs)
        self.suppfunc = tuple(_compute_supported_functions(kv,ms) for (kv,ms) in zip(self.kvs, self.meshsupp))

    def refine(self):
        return TPMesh([kv.refine() for kv in self.kvs])

    def cells(self):
        """Return a list of all cells in this mesh."""
        return list(itertools.product(
            *(range(n) for n in self.numspans)))

    def cell_extents(self, c):
        """Return the extents (as a tuple of min/max pairs) of the cell `c`."""
        return tuple((kv.mesh[cd], kv.mesh[cd+1]) for (kv,cd) in zip(self.kvs, c))

    def functions(self):
        """Return a list of all basis functions defined on this mesh."""
        return list(itertools.product(
            *(range(n) for n in self.numdofs)))

    def _support_1d(self, dim, j):
        ms = self.meshsupp[dim]
        return range(ms[j,0], ms[j,1])

    def _supported_in_1d(self, dim, k):
        sf = self.suppfunc[dim]
        return range(sf[k,0], sf[k,1])

    def support(self, indices):
        """Return the list of cells where any of the given functions does not vanish."""
        supp = []
        for jj in indices:
            supp.extend(itertools.product(
                *(self._support_1d(d, j) for (d,j) in enumerate(jj))))
        supp.sort()
        return _make_unique(supp)

    def supported_in(self, cells):
        """Return the list of functions whose support intersects the given cells."""
        funcs = []
        for kk in cells:
            funcs.extend(itertools.product(
                *(self._supported_in_1d(d, k) for (d,k) in enumerate(kk))))
        funcs.sort()
        return _make_unique(funcs)

    def neighbors(self, indices):
        """Return all functions which have nontrivial support intersection with the given ones."""
        return self.supported_in(self.support(indices))

class HMesh:
    """A hierarchical mesh built on a sequence of uniformly refined tensor product meshes.

    This class is an implementation detail and should not be used in user-facing code.
    """
    def __init__(self, mesh):
        self.dim = mesh.dim
        self.meshes = [mesh]
        self.active = [set(mesh.cells())]
        self.deactivated = [set()]
        self.P = []

    def add_level(self):
        self.meshes.append(self.meshes[-1].refine())
        self.active.append(set())
        self.deactivated.append(set())
        self.P.append(tuple(
            bspline.prolongation(k0, k1).tocsc() for (k0,k1)
            in zip(self.meshes[-2].kvs, self.meshes[-1].kvs)))

    def cell_children(self, lv, cells):
        assert 0 <= lv < len(self.meshes) - 1, 'Invalid level'
        children = []
        for c in cells:
            children.extend(itertools.product(
                *(range(2*ci, 2*(ci + 1)) for ci in c)))
        return children

    def cell_babys(self, lv, cells, targetlv = None):
        if not targetlv:
            targetlv = len(self.meshes) - 1
        assert 0 <= lv < len(self.meshes) - 1, 'Invalid level'
        assert lv < targetlv < len(self.meshes), 'Invalid target level'
        if targetlv - lv == 1:
            return self.cell_children(lv, cells)
        else:
            return self.cell_babys(lv+1, self.cell_children(lv, cells), targetlv)
        #babys = []
        #for c in cells:
        #    babys.extend(itertools.product(
        #        *(range((targetlv - lv)*2*ci, (targetlv - lv)*2*(ci + 1)) for ci in c)))
        #return babys

    def cell_parent(self, lv, cells):
        assert 1 <= lv < len(self.meshes), 'Invalid level'
        parents = []
        for c in cells:
            parents.append(tuple(ci // 2 for ci in c))
        parents.sort()
        return _make_unique(parents)

    def cell_grandparent(self, lv, cells, targetlv = None):
        if not targetlv:
            targetlv = 0
        assert 1 <= lv < len(self.meshes), 'Invalid level'
        assert 0 <= targetlv < lv, 'Invalid target level'
        if lv - targetlv == 1:
            return self.cell_parent(lv, cells)
        else:
            return self.cell_grandparent(lv-1, self.cell_parent(lv, cells), targetlv)
        #grandparents = []
        #for c in cells:
        #    grandparents.append(tuple(ci // (lv - targetlv)*2 for ci in c))
        #grandparents.sort()
        #return _make_unique(grandparents)

    def _TP_to_HMesh_cells_up(self, lv, cells):
        """Returns dictionary of hierarchical cells of levels >=`lv` contributing to the
        `cells` of level `lv`"""
        assert 0 <= lv < len(self.meshes), 'Invalid level'
        out = dict()
        aux_cells = set(cells.copy())
        aux_lv = lv
        while aux_cells:
            out[aux_lv] = aux_cells & self.active[aux_lv]
            aux_cells -= self.active[aux_lv]
            #print("len = ",len(aux_cells))
            try:
                aux_cells = set(self.cell_children(aux_lv, aux_cells))
                aux_lv += 1
            except AssertionError:
                pass
        return out

    def _TP_to_HMesh_cells_down(self, lv, cells):
        """Returns dictionary of hierarchical cells of level <=`lv` contributing to the
        `cells` of level `lv`"""
        assert 0 <= lv < len(self.meshes), 'Invalid level'
        out = dict()
        aux_cells = set(cells.copy())
        aux_lv = lv
        while aux_cells:
            out[aux_lv] = aux_cells & self.active[aux_lv]
            aux_cells -= self.active[aux_lv]
            #print("len = ",len(aux_cells))
            try:
                aux_cells = set(self.cell_parent(aux_lv, aux_cells))
                aux_lv -= 1
            except AssertionError:
                pass
        return out

    def _TP_to_HMesh_cells(self, lv, cells):
        """Returns dictionary of hierarchical cells contributing to the
        `cells` of level `lv`"""
        assert 0 <= lv < len(self.meshes), 'Invalid level: lv = ' + str(lv) + " maxlv = " + str(len(self.meshes))
        #print("out_up_set = ", set(cells) & (self.active[lv] | self.deactivated[lv]))
        #print("out_down_set = ",set(cells) - (self.active[lv] | self.deactivated[lv]))
        out_up = self._TP_to_HMesh_cells_up(lv, set(cells) & (self.active[lv] | self.deactivated[lv]))
        out_down = self._TP_to_HMesh_cells_down(lv, set(cells) - (self.active[lv] | self.deactivated[lv]))
        #print("out_up = ",self._clean_Hmesh_cells(out_up))
        #print("out_down = ",self._clean_Hmesh_cells(out_down))
        #out_down.update(out_up)
        return self._combine_HMesh_cells(out_down, out_up)

    @staticmethod
    def _combine_HMesh_cells(cellsA, cellsB):
        """Takes two dictionarys of (not necessarily active) hierarchical cells `cellsA`,
        `cellsB` and returns their union"""
        out = dict()
        aux = dict()
        for common_level in cellsA.keys() & cellsB.keys():
            tA, tB = type(cellsA[common_level]), type(cellsB[common_level])
            assert tA == tB
            aux[common_level] = tA(set(cellsA[common_level]) | set(cellsB[common_level]))
        out.update(cellsA)
        out.update(cellsB)
        out.update(aux)
        return out

    @staticmethod
    def _clean_Hmesh_cells(cells):
        """Returns `cells` with empty levels removed"""
        return {lv: c for (lv, c) in cells.items() if c}

    def HMesh_cells(self, marked):
        """Returns the smallest dictionary of (active) hierarchical cells containing
        `marked`"""
        out = dict()
        for lv in marked:
            out = self._clean_Hmesh_cells(
                self._combine_HMesh_cells(out,
                    self._TP_to_HMesh_cells(lv, marked[lv])
                )
            )
        return out

    def _function_children_1d(self, lv, dim, j):
        assert 0 <= lv < len(self.meshes) - 1, 'Invalid level'
        return list(self.P[lv][dim].getcol(j).nonzero()[0])

    def _function_parents_1d(self, lv, dim, j):
        assert 0 < lv < len(self.meshes), 'Invalid level'
        return list(self.P[lv-1][dim].getrow(j).nonzero()[1])

    def function_children(self, lv, indices):
        children = []
        for jj in indices:
            children.extend(itertools.product(
                *(self._function_children_1d(lv, d, j) for (d,j) in enumerate(jj))))
        children.sort()
        return _make_unique(children)

    def function_babys(self, lv, indices, targetlv = None):
        if not targetlv:
            targetlv = len(self.meshes) - 1
        assert lv < targetlv < len(self.meshes), 'Invalid target level'
        if targetlv - lv == 1:
            return self.function_children(lv, indices)
        else:
            return self.function_babys(lv+1, self.function_children(lv, indices), targetlv)

    def function_parents(self, lv, indices):
        parents = []
        for jj in indices:
            parents.extend(itertools.product(
                *(self._function_parents_1d(lv, d, j) for (d,j) in enumerate(jj))))
        parents.sort()
        return _make_unique(parents)

    def function_grandparents(self, lv, indices, targetlv = None):
        if not targetlv:
            targetlv = 0
        assert 0 <= targetlv < lv, 'Invalid target level'
        if lv - targetlv == 1:
            return self.function_parents(lv, indices)
        else:
            return self.function_grandparents(lv-1, self.function_parents(lv, indices), targetlv)

    def ensure_levels(self, L):
        """Make sure that the hierarchical space has at least `L` levels."""
        while len(self.meshes) < L:
            self.add_level()

    def refine(self, marked):
        # if necessary, add new fine levels to the data structure
        # NB: if refining on lv 0, we need 2 levels (0 and 1) -- hence the +2
        self.ensure_levels(max(marked.keys()) + 2)

        new_cells = dict()
        for lv in range(len(self.meshes) - 1):
            cells = set(marked.get(lv, []))
            # deactivate refined cells
            self.active[lv] -= cells
            self.deactivated[lv] |= cells
            # add children
            new_cells[lv+1] = self.cell_children(lv, cells)
            self.active[lv+1] |= set(new_cells[lv+1])
        return new_cells

    def get_virtual_mesh(self, level):
        assert 0 <= level < len(self.meshes)
        out = HMesh(self.meshes[0])
        for i in range(level+1):
            out.refine({i: self.deactivated[i]})
        return out


class HSpace:
    """Represents a HB-spline or THB-spline space over an adaptively refined mesh.

    Arguments:
        kvs: a sequence of :class:`.KnotVector` instances, representing
            the tensor product B-spline space on the coarsest level
    """
    def __init__(self, kvs, disparity=np.inf, bdspecs=None):
        tp = TPMesh(kvs)
        hmesh = HMesh(tp)
        assert len(hmesh.meshes) == 1
        self.dim = hmesh.dim
        self.hmesh = hmesh
        self.actfun = [set(hmesh.meshes[0].functions())]
        self.deactfun = [set()]
        self.disparity = disparity
        self.bdspecs = bdspecs
        self._clear_cache()

    def _clear_cache(self):
        self.__index_dirichlet = None
        self.__ravel_global = None
        self.__ravel_dirichlet = None
        self.__smooth_dirichlet = None

    def _add_level(self):
        self.hmesh.add_level()
        self.actfun.append(set())
        self.deactfun.append(set())
        assert len(self.actfun) == len(self.deactfun) == len(self.hmesh.meshes)

    def _ensure_levels(self, L):
        """Make sure we have at least `L` levels."""
        while self.numlevels < L:
            self._add_level()

    @property
    def numlevels(self):
        """The number of levels in this hierarchical space."""
        return len(self.hmesh.meshes)

    @property
    def numdofs(self):
        """The total number of active basis functions in this hierarchical space."""
        return sum(self.numactive)

    @property
    def numactive(self):
        """A tuple containing the number of active basis functions per level."""
        return tuple(len(af) for af in self.actfun)

    def mesh(self, lv):
        """Return the underlying :class:`TPMesh` on the given level."""
        return self.hmesh.meshes[lv]

    def knotvectors(self, lv):
        """Return the tuple of knot vectors for the tensor product space on level `lv`."""
        return self.hmesh.meshes[lv].kvs

    def active_cells(self, lv=None):
        """If `lv` is specified, return the set of active cells on that level.
        Otherwise, return a list containing, for each level, the set of active cells.
        """
        if lv is not None:
            return self.hmesh.active[lv]
        else:
            return [self.active_cells(lv) for lv in range(self.numlevels)]

    def deactivated_cells(self, lv=None):
        """If `lv` is specified, return the set of deactivated cells on that level.
        Otherwise, return a list containing, for each level, the set of deactivated cells.
        """
        if lv is not None:
            return self.hmesh.deactivated[lv]
        else:
            return [self.deactivated_cells(lv) for lv in range(self.numlevels)]

    def cell_extents(self, lv, c):
        """Return the extents (as a tuple of min/max pairs) of the cell `c` on level `lv`."""
        return self.hmesh.meshes[lv].cell_extents(c)

    def _ravel_indices(self, indexsets):
        indices = [sorted(ix) if isinstance(ix, set) else ix for ix in indexsets]
        return tuple(
            (np.ravel_multi_index(np.array(indices[lv]).T, self.mesh(lv).numdofs, order='C')
                if len(indexsets[lv])
                else np.arange(0))
            for lv in range(self.numlevels)
        )

    def active_indices(self):
        """Return a tuple which contains, per level, the raveled (sequential) indices of
        active basis functions.
        """
        self.__ravel_actfun = self._ravel_indices(self.actfun)
        return self.__ravel_actfun

    def deactivated_indices(self):
        """Return a tuple which contains, per level, the raveled (sequential) indices of
        deactivated basis functions.
        """
        self.__ravel_deactfun = self._ravel_indices(self.deactfun)
        return self.__ravel_deactfun

    def _compute_single_axis_single_level_dirichlet_indices(self, lv, bdspec):
        assert 0 <= lv < self.numlevels, 'Invalid level.'
        bdax, bdside = bdspec
        N = tuple(kv.numdofs for kv in self.hmesh.meshes[lv].kvs)
        return set(tuple(iter) for iter in assemble.slice_indices(bdax, 0 if bdside==0 else -1, N, ravel=False))

    def dirichlet_indices(self):
        # Compute tensor product boundary indices (specified by self.bdspecs)
        TPbindices = list()
        for lv in range(self.numlevels):
            aux = set()
            for bdspec in self.bdspecs:
                aux |= self._compute_single_axis_single_level_dirichlet_indices(lv, bdspec)
            TPbindices.append(aux)

        # Compute boundary indices for virtual hierarchy
        out = list()
        out_index = list()
        for lv in range(self.numlevels):
            aux = list()
            for i in range(self.numlevels):
                if i == lv: # deactivated functions added separatly
                    #aux.append((self.actfun[i] | self.deactfun[i]) & TPbindices[i])
                    aux.append(self.actfun[i] & TPbindices[i])
                elif 0 <= i < lv:
                    aux.append(self.actfun[i] & TPbindices[i])
                else:
                    aux.append(set())
            out.append(list(self._ravel_indices(aux)))
            out_index.append(aux)

        # deactivated boundary ravel
        ravel_bddeact = self._ravel_indices(
            [self.deactfun[lv] & TPbindices[lv]
                for lv in range(self.numlevels)])

        # insertion of deactivated functions
        for lv in range(self.numlevels):
            out_index[lv][lv] |= self.deactfun[lv] & TPbindices[lv]
            out[lv][lv] = np.concatenate((out[lv][lv], ravel_bddeact[lv]))

        self.__ravel_dirichlet = tuple(out)
        self.__index_dirichlet = tuple(out_index)

    @property
    def index_dirichlet(self):
        if not self.__index_dirichlet:
            self.dirichlet_indices()
        return self.__index_dirichlet

    @property
    def ravel_actfun(self):
        if not self.__ravel_actfun:
            self.active_indices()
        return self.__ravel_actfun

    @property
    def ravel_deactfun(self):
        if not self.__ravel_deactfun:
            self.deactivated_indices()
        return self.__ravel_deactfun

    @property
    def ravel_actdeactfun(self):
        if not self.__ravel_actdeactfun:
            self.__ravel_actdeactfun = tuple(np.concatenate((iA,iD)) for (iA,iD) in zip(self.ravel_actfun,self.ravel_deactfun))
        return self.__ravel_actdeactfun

    @property
    def ravel_dirichlet(self):
        if not self.__ravel_dirichlet:
            self.dirichlet_indices()
        return self.__ravel_dirichlet

    @property
    def ravel_new(self):
        indices = self.new_indices()
        return [self._ravel_indices(idx) for idx in indices]

    @property
    def ravel_trunc(self):
        indices = self.trunc_indices()
        return [self._ravel_indices(idx) for idx in indices]

    @property
    def ravel_func_supp(self):
        indices = self.func_supp_indices()
        return [self._ravel_indices(idx) for idx in indices]

    @property
    def ravel_cell_supp(self):
        indices = self.cell_supp_indices()
        return [self._ravel_indices(idx) for idx in indices]

    @property
    def ravel_global(self):
        if not self.__ravel_global:
            indices = self.global_indices()
            self.__ravel_global = [self._ravel_indices(idx) for idx in indices]
        return self.__ravel_global

    @property
    def cell_dirichlet(self):
        return self.compute_cells(self.index_dirichlet)

    @property
    def cell_new(self):
        return self.compute_cells(self.new_indices())

    @property
    def cell_trunc(self):
        return self.compute_cells(self.trunc_indices())

    @property
    def cell_func_supp(self):
        return self.compute_cells(self.func_supp_indices())

    @property
    def cell_cell_supp(self):
        return self.compute_cells(self.cell_supp_indices())

    @property
    def cell_global(self):
        return self.compute_cells(self.global_indices())

    @property
    def smooth_dirichlet(self):
        if not self.__smooth_dirichlet:
            self.__smooth_dirichlet = self.indices_to_smooth("dirichlet")
        return self.__smooth_dirichlet

    @property
    def smooth_new(self):
        return self.indices_to_smooth("new")

    @property
    def smooth_trunc(self):
        return self.indices_to_smooth("trunc")

    @property
    def smooth_func_supp(self):
        return self.indices_to_smooth("func_supp")

    @property
    def smooth_cell_supp(self):
        return self.indices_to_smooth("cell_supp")

    def remove_indices(self, listsetA, listsetB):
        for lv in range(self.numlevels):
            listsetA[lv] -= listsetB[lv]

    def new_indices(self):
        """Return a tuple which contains tuples which contain, per level, the raveled
        (sequential) indices of newly added basis functions in the VIRTUAL HIERARCHY per level."""
        return [
                [ ( sorted(self.actfun[i] - self.index_dirichlet[lv][i])
                  + sorted(self.deactfun[i] - self.index_dirichlet[lv][i]))
                    if i == lv
                    else []
                    for i in range(self.numlevels)]
                for lv in range(self.numlevels)]

    def trunc_indices(self):
        """Return a tuple which contains tuples which contain, per level, the raveled
        (sequential) indices of TRUNC basis functions in the VIRTUAL HIERARCHY per level."""
        indices = self.new_indices()        # start with only the newly added indices
        out = list()
        out_index = list()
        aux_dict = dict()
        for lv in range(self.numlevels):
            aux = list()
            for i in range(self.numlevels):
                if i == lv:
                    aux_act = list(self.actfun[lv])
                    aux_dict[lv] = dict(zip(aux_act, aux_act))
                elif lv - self.disparity <= i < lv:
                    aux_indices = list()
                    for j in aux_dict[i]:
                        if isinstance(aux_dict[i][j], tuple):
                            aux_dict[i][j] = {aux_dict[i][j]}
                        aux_dict[i][j] = set(self.hmesh.function_children(lv-1, aux_dict[i][j]))
                        if aux_dict[i][j] & (self.actfun[lv] | self.deactfun[lv]):
                            aux_dict[i][j] -= (self.actfun[lv] | self.deactfun[lv])
                            aux_indices.append(j)
                    indices[lv][i] = sorted(set(aux_indices) - self.index_dirichlet[lv][i])
        return indices

    def func_supp_indices(self):
        """Return a tuple which contains tuples which contain, per level, the raveled
        (sequential) indices of FUNC_SUPP basis functions in the VIRTUAL HIERARCHY per level."""
        indices = self.new_indices()        # start with only the newly added indices
        for lv in range(self.numlevels):
            for i in range(self.numlevels):
                if lv - self.disparity <= i < lv:
                    funcs = set(self.hmesh.function_grandparents(lv, self.actfun[lv], i)) & self.actfun[i]
                    indices[lv][i] = sorted(funcs - self.index_dirichlet[lv][i])

        return indices

    def cell_supp_indices(self):
        """Return a tuple which contains tuples which contain, per level, the raveled
        (sequential) indices of CELL_SUPP basis functions in the VIRTUAL HIERARCHY per level."""
        indices = self.new_indices()        # start with only the newly added indices
        for lv in range(self.numlevels):    # loop over virtual hierarchy levels
            for i in range(self.numlevels):
                if lv - self.disparity <= i < lv:
                    funcs = set(
                            self.hmesh.meshes[i].supported_in(
                                self.hmesh.cell_grandparent(
                                    lv,
                                    self.hmesh.meshes[lv].support(self.actfun[lv]),
                                    i))) & self.actfun[i]
                    indices[lv][i] = sorted(funcs - self.index_dirichlet[lv][i])
        return indices

    def global_indices(self):
        """Return a tuple which contains tuples which contain, per level, the raveled
        (sequential) indices of GLOBAL basis functions in the VIRTUAL HIERARCHY per level."""
        indices = [ [[] for i in range(self.numlevels)] for j in range(self.numlevels) ]
        for lv in range(self.numlevels):
            for i in range(self.numlevels):
                if i == lv:
                    indices[lv][i] = sorted(self.actfun[i]) + sorted(self.deactfun[i])
                elif i < lv:
                    indices[lv][i] = sorted(self.actfun[i])
        return indices

    def indices_to_smooth(self, strategy='func_supp'):
        assert strategy in ("dirichlet", "new", "trunc", "func_supp", "cell_supp", "global"), "Invalid smoothing strategy"
        choosen_indices = eval("self.ravel_" + strategy)
        available_indices = self.ravel_global
        out = list()
        for lv in range(self.numlevels):
            aux = list()
            n_lv = 0
            for l in range(self.numlevels):
                aux += list(n_lv + self._position_index(list(available_indices[lv][l]), choosen_indices[lv][l]))
                n_lv += len(available_indices[lv][l])
            out.append(np.array(aux))
        return out

    @staticmethod
    def _position_index(suplist, sublist):
        """Takes two sorted lists of unique integers (eg. two raveled index lists),
        where `suplist` contains `sublist`. Returns list of position indices of matching
        entries."""
        out = list()
        aux = 0
        for candidate in sublist:
            aux = suplist.index(candidate, aux)
            out.append(aux)
        return np.array(out)

    def list_to_dict(self, listset, lv=None):
        """Converts a list-set representation of hierarchical indices to its dict representation. If `lv` is provided, only levels `lv`-self.disparity, ..., `lv` are processed."""
        if not lv:
            return HMesh._clean_Hmesh_cells({l: listset[l] for l in range(self.numlevels)})
        else:
            return HMesh._clean_Hmesh_cells({l: listset[l] for l in range(max(lv-self.disparity, 0),lv+1)})

    def tuplelist_to_tupledict(self, tuplelistset):
        """Converts a tuple-list-set collection of hierarchical indices to its tuple-dict representation."""
        return tuple(self.list_to_dict(tuplelistset[lv], lv) for lv in range(self.numlevels))

    def compute_cells(self, tuplelistset):
        out = list()
        for (lv, single_index) in enumerate(tuplelistset):
            aux = [set(self.hmesh.meshes[l].support(funcs))
                    for (l, funcs) in enumerate(single_index)]
            out.append(
                self.get_virtual_space(lv).hmesh.HMesh_cells(self.list_to_dict(aux)))
        return tuple(out)

    def function_support(self, lv, jj):
        """Return the support (as a tuple of pairs) of the function on level `lv` with index `jj`."""
        kvs = self.mesh(lv).kvs
        meshsupps = (kv.mesh_support_idx(j) for (kv,j) in zip(kvs, jj))
        return tuple((kv.mesh[lohi[0]], kv.mesh[lohi[1]]) for (kv,lohi) in zip(kvs,meshsupps))

    def _functions_to_deactivate(self, marked):
        mf = dict()
        # for now assuming marked cells, not functions
        for lv in range(len(self.hmesh.meshes)):
            m = marked.get(lv)
            if not m:
                mf[lv] = set()
            else:
                # can only deactivate active functions
                mfuncs = set(self.mesh(lv).supported_in(m)) & self.actfun[lv]
                # A function is deactivated when all the cells of its level within
                # the support are deactivated.
                mf[lv] = set(f for f in mfuncs
                        if not (set(self.mesh(lv).support([f])) & self.hmesh.active[lv]))
        return mf

    def cell_support_extension(self, l, cells, k):
        assert 0 <= k <= l, 'Invalid level.'
        if k == l:
            aux = cells
        else:
            aux = self.hmesh.cell_grandparent(l, cells, k)
        supported_functions = self.hmesh.meshes[k].supported_in(aux)
        return set(self.hmesh.meshes[k].support(supported_functions))

    def function_support_extension(self, l, functions, k):
        assert 0 <= k <= l, 'Invalid level.'
        aux = self.hmesh.meshes[l].support(functions)
        if k == l:
            pass
        else:
            aux = self.hmesh.cell_grandparent(l, aux, k)
        return self.hmesh.meshes[k].supported_in(aux)

    def _cell_neighborhood(self, l, cells, truncate=False):
        if l-self.disparity <0:
            return set()
        else:
            if truncate:
                return self.hmesh.active[l-self.disparity] & set(self.hmesh.cell_parent(l-self.disparity+1,self.cell_support_extension(l, cells, l-self.disparity+1)))
            else:
                return self.hmesh.active[l-self.disparity] & set(self.cell_support_extension(l, cells, l-self.disparity))

    def _mark_recursive(self, l, marked, truncate=False):
        neighbors = self._cell_neighborhood(l, marked.get(l,set()), truncate=truncate)
        if neighbors:
            marked[l-self.disparity] = marked.get(l-self.disparity, set()) | neighbors
            self._mark_recursive(l-self.disparity, marked)

    def refine(self, marked, truncate=False):
        """Refine the given cells; `marked` is a dictionary which has the
        levels as indices and the list of marked cells on that level as values.

        Refinement procedure preserving the mesh-level disparity `self.disparity` following [Bracco, Gianelli, Vazquez, 2018].
        """
        self._ensure_levels(max(marked.keys()) + 2)

        if self.disparity < np.inf:
            marked = marked.copy()
            for l in range(self.numlevels):
                self._mark_recursive(l, marked, truncate=truncate)

        new_cells = self.hmesh.refine(marked)
        mf = self._functions_to_deactivate(marked)

        for lv in range(len(self.hmesh.meshes) - 1):
            mfuncs = mf[lv]
            # deactivate the marked functions
            self.actfun[lv] -= mfuncs
            self.deactfun[lv] |= mfuncs
            # find candidate functions on fine level to be activated
            candidate_funcs = self.mesh(lv+1).supported_in(new_cells[lv+1])
            # ignore functions that are already active
            candidate_funcs = set(candidate_funcs) - self.actfun[lv+1]

            # set of active or deactivated cells on finer level
            fine_cells = self.hmesh.active[lv+1] | self.hmesh.deactivated[lv+1]
            # keep only those functions whose support is contained in those fine cells
            newfuncs = set(f for f in candidate_funcs if
                set(self.mesh(lv+1).support([f])).issubset(fine_cells))
            # activate them on the finer level
            self.actfun[lv+1] |= newfuncs

        self._clear_cache()

    def get_virtual_space(self, level):
        assert 0 <= level < self.numlevels, 'Invalid level.'
        out = HSpace(self.hmesh.meshes[0].kvs)
        for i in range(level): # refine level-1 times
            out.refine({i: self.hmesh.deactivated[i]})
        return out

    def refine_region(self, lv, region_function):
        """Refine all active cells on level `lv` whose cell center satisfies `region_function`.

        `region_function` should be a function of `dim` scalar arguments (e.g., `(x,y)`)
        which returns True if the point is within the refinement region.
        """
        self._ensure_levels(lv + 2)

        def cell_center(c):
            return tuple(0.5*(lo+hi) for (lo,hi) in reversed(self.cell_extents(lv, c)))
        self.refine({
            lv: tuple(c for c in self.active_cells(lv) if region_function(*cell_center(c)))
        })

    def represent_fine(self, truncate=False):
        """Compute a matrix which represents all active HB-spline basis functions on the fine level.

        The returned matrix has size `N_fine × N_act`, where `N_fine` is the
        number of degrees of freedom in the finest tensor product mesh and
        `N_act` = :attr:`numdofs` is the total number of active basis functions
        across all levels.

        If `truncate` is True, the representation of the THB-spline (truncated) basis functions
        is computed instead.

        .. note::
            This method is inherently inefficient since it deals with the full
            fine-level tensor product space.
        """
        # raveled indices for active functions per level
        act_indices = self.active_indices()
        blocks = []

        for lv in reversed(range(self.numlevels)):
            Nj = self.mesh(lv).numbf

            if lv == self.numlevels - 1:
                P = scipy.sparse.eye(Nj)
            else:
                Pj = utils.multi_kron_sparse(self.hmesh.P[lv], format='lil')
                if truncate:
                    Pj[act_indices[lv+1], :] = 0
                P = P.dot(Pj)

            act_to_all = scipy.sparse.eye(Nj, format='csr')[:, act_indices[lv]]

            blocks.append(P.dot(act_to_all))

        blocks.reverse()
        return scipy.sparse.bmat([blocks], format='csr')

    def split_coeffs(self, x):
        """Given a coefficient vector `x` of length :attr:`numdofs`, split it
        into :attr:`numlevels` vectors which contain the contributions from
        each individual level.
        """
        j = 0
        result = []
        for af in self.actfun:
            nk = len(af)
            result.append(x[j : j+nk])
            j += nk
        assert j == x.shape[0], 'Wrong length of input vector'
        return result

    def tp_prolongation(self, lv, kron=False):
        """Return the prolongation operator for the underlying tensor product mesh from level
        `lv` to `lv+1`.

        If `kron` is True, the prolongation is returned as a sparse matrix. Otherwise, the
        prolongation is returned as a tuple of sparse matrices, one per space dimension,
        whose Kronecker product represents the prolongation operator.

        .. note::
            This method, particularly with `kron=True`, is inherently
            inefficient since it deals with the full tensor product spaces, not
            merely the active basis functions.
        """
        Ps = self.hmesh.P[lv]
        return utils.multi_kron_sparse(Ps) if kron else Ps
