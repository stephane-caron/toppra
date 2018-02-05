"""This module constains `PathConstraint`, the unifying interface to
constraints on geometric path. It also contains routines for creating
the `PathConstraint`.

"""
import numpy as np
from enum import Enum
from utils import inv_dyn, compute_jacobian_wrench
from scipy.linalg import block_diag
from TOPP import INFTY
import logging
logger = logging.getLogger(__name__)

try:
    from _CythonUtils import _create_velocity_constraint
except ImportError:
    pass


class PathConstraintKind(Enum):
    Canonical = 0
    TypeI = 1
    TypeII = 2


class PathConstraint(object):
    """Discretized path constraint.

    Parameters
    ----------
    name : str, optional
        Name of the constraint.
    ss : array, optional
        Shape (N+1,). Grid points.
    a : array, optional
        Shape (N+1, neq). Coeff; Canonical.
    b : array, optional
        Shape (N+1, neq). Coeff; Canonical.
    c : array, optional
        Shape (N+1, neq). Coeff; Canonical.
    abar : array, optional
        Shape (N+1, neq). Coeff; Type I.
    bbar : array, optional
        Shape (N+1, neq). Coeff; Type I.
    cbar : array, optional
        Shape (N+1, neq). Coeff; Type I.
    l : array, optional
        Shape (N+1, nv). Bounds for `v`.
    h : array, optional
        Shape (N+1, nv). Bounds for `v`.
    G : array, optional
        Shape (N+1, niq, nv). Coeff; Type II.
    lG : array, optional
        Shape (N+1, niq). Bounds; Type II.
    hG : array, optional
        Shape (N+1, niq). Bounds; Type II.

    Attributes
    ----------
    nm : int
        Dimension of non-canonical inequalities.
    nv : int
        Dimension of slack variable.
    neq : int
        Dimension of equality constraint.
    niq : int
        Dimension of non-canonical inequality constraint.
    N : int
        Number of discretization segments.

    Notes
    -----

    In the most general setting, a :class:`PathConstraint` object
    candescribes **one** of the following kind:

    1. *Canonical*:

    .. math:: \mathbf a[i] u    + \mathbf b[i] x    + \mathbf c[i]    \leq 0

    2. Non-canonical *Type I*:

    .. math::
                & \mathbf{abar}[i] u + \mathbf{bbar}[i] x + \mathbf{cbar}[i]  = \mathbf{D}[i] v \\\\
                & \mathbf{l}[i]  \leq          v             \leq \mathbf{h}[i]

    3. Non-canonical *Type II*, in addition to 2.

    .. math::
                \mathbf{lG}(s) \leq     \mathbf{G}(s) v             \leq \mathbf{hG}(s)

    where `u` is the path acceleration, `x` is the squared path velocity
    and `v` is a slack variable whose physical meanings depend on the
    nature of the constraint.

    Depending on the constraint, corresponding keyword arguments are
    to be given as numerical arrays during initialization.

    Note that there is no actual problem in allowing a constraint to
    have both canonical and non-canonical parts.

    Example
    -------

    Create a canonical constraint

    >>> a = np.random.randn(N+1, 5)
    >>> b = np.random.randn(N+1, 5)
    >>> c = np.random.randn(N+1, 5)
    >>> pc = PathConstraint(a=a, b=b, c=c, ss=np.linspace(0, 1, N+1))

    then, create a first-order interpolation

    >>> pc = interpolate_constraint(pc)
    """

    def __repr__(self):
        return "Constraint(nm:{:d}, neq:{:d}, nv:{:d}, niq:{:d})".format(
            self.nm, self.neq, self.nv, self.niq)

    def __lt__(self, pc):
        return self.kind.value < pc.kind.value

    def __init__(self, a=None, b=None, c=None,
                 abar=None, bbar=None, cbar=None,
                 D=None, l=None, h=None,
                 lG=None, G=None, hG=None,
                 name=None, ss=None):
        self.N = ss.shape[0] - 1  # number of intervals
        self.sparse = False  # TODO: this feature is not completely implemented

        # canonical
        if a is None:
            self.a = np.empty((self.N + 1, 0))
            self.b = np.empty((self.N + 1, 0))
            self.c = np.empty((self.N + 1, 0))
        else:
            self.a = a
            self.b = b
            self.c = c
        self._nm = self.a.shape[1]

        # Type I
        if D is None:
            self.abar = np.empty((self.N + 1, 0))
            self.bbar = np.empty((self.N + 1, 0))
            self.cbar = np.empty((self.N + 1, 0))
            self.D = np.empty((self.N + 1, 0, 0))
        else:
            self.abar = abar
            self.bbar = bbar
            self.cbar = cbar
            self.D = D
        self._neq = self.abar.shape[1]
        self._nv = self.D[0].shape[1]

        if l is None:
            self.l = np.empty((self.N + 1, 0))
            self.h = np.empty((self.N + 1, 0))
        else:
            self.l = l
            self.h = h

        # Type II
        if lG is None:
            self.lG = np.empty((self.N + 1, 0))
            self.G = np.empty((self.N + 1, 0, self.nv))
            self.hG = np.empty((self.N + 1, 0))
        else:
            self.lG = lG
            self.G = G
            self.hG = hG
        self._niq = self.lG.shape[1]

        self.name = name
        self._ss = ss

        # Store constraint cat
        if self.nm != 0:
            self._kind = PathConstraintKind.Canonical
        elif self.niq == 0:
            self._kind = PathConstraintKind.TypeI
        else:
            self._kind = PathConstraintKind.TypeII

    @property
    def kind(self):
        """ The kind of path constraint.

        """
        return self._kind

    @property
    def ss(self):
        """ Grid points.
        """
        return self._ss

    @property
    def nm(self):
        """Dimension of canonical constraint.
        """
        return self._nm

    @property
    def neq(self):
        """Dimension of non-canonical equality.

        """
        return self._neq

    @property
    def niq(self):
        """Dimension of non-canonical inequality.

        """
        return self._niq

    @property
    def nv(self):
        """Dimension of the slack variable.
        """
        return self._nv


def interpolate_constraint(pc):
    """Produce a discretized :class:`PathConstraint` by first-order
    interpolation.

    Parameters
    ----------
    pc : :class:`PathConstraint`
        The original, collocated, constraint.

    Returns
    -------
    out : :class:`PathConstraint`
        The interpolated constraint.

    """
    N = pc.N
    Ds = pc.ss[1:] - pc.ss[:N]
    # Canonical part
    a_intp = np.empty((pc.N+1, 2 * pc._nm))
    a_intp[:, 0:pc._nm] = pc.a
    a_intp[N] = np.hstack((pc.a[N], pc.a[N]))
    # Multiply rows of pc.b with entries of Ds
    _ = pc.a[1:] + 2 * (pc.b[1:].T * Ds).T
    a_intp[:N, pc._nm:] = _

    b_intp = np.empty((pc.N+1, 2 * pc._nm))
    b_intp[:, 0:pc._nm] = pc.b
    b_intp[:N, pc._nm:] = pc.b[1:]
    b_intp[N] = np.hstack((pc.b[N], pc.b[N]))

    c_intp = np.empty((pc.N+1, 2 * pc._nm))
    c_intp[:, 0:pc._nm] = pc.c
    c_intp[:N, pc._nm:] = pc.c[1:]
    c_intp[N] = np.hstack((pc.c[N], pc.c[N]))

    # Equality part
    abar_intp = np.empty((pc.N+1, 2 * pc._neq))
    abar_intp[:, 0:pc._neq] = pc.abar
    abar_intp[N] = np.hstack((pc.abar[N], pc.abar[N]))

    bbar_intp = np.empty((pc.N+1, 2 * pc._neq))
    bbar_intp[:, 0:pc._neq] = pc.bbar
    bbar_intp[:N, pc._neq:] = pc.bbar[1:]
    bbar_intp[N] = np.hstack((pc.bbar[N], pc.bbar[N]))

    cbar_intp = np.empty((pc.N+1, 2 * pc._neq))
    cbar_intp[:, 0:pc._neq] = pc.cbar
    cbar_intp[:N, pc._neq:] = pc.cbar[1:]
    cbar_intp[N] = np.hstack((pc.cbar[N], pc.cbar[N]))

    D_intp = np.zeros((pc.N+1, 2 * pc._neq, 2 * pc._nv))
    D_intp[:, 0:pc._neq, 0:pc._nv] = pc.D
    D_intp[:N, pc._neq: 2 * pc._neq, pc._nv: 2 * pc._nv] = pc.D[1:]
    D_intp[N, 0:pc._neq, 0:pc._nv] = pc.D[N]
    D_intp[N, pc._neq: 2 * pc._neq, pc._nv: 2 * pc._nv] = pc.D[N]

    l_intp = np.empty((pc.N+1, 2 * pc._nv))
    l_intp[:, 0:pc._nv] = pc.l
    l_intp[:N, pc._nv:] = pc.l[1:]
    l_intp[N] = np.hstack((pc.l[N], pc.l[N]))

    h_intp = np.empty((pc.N+1, 2 * pc._nv))
    h_intp[:, 0:pc._nv] = pc.h
    h_intp[:N, pc._nv:] = pc.h[1:]
    h_intp[N] = np.hstack((pc.h[N], pc.h[N]))

    _ = pc.abar[1:] + 2 * (pc.bbar[1:].T * Ds).T
    abar_intp[:N, pc._neq:] = _

    # Inequality
    G_intp = np.empty((pc.N+1, 2 * pc.niq, 2 * pc._nv))
    G_intp[:, 0:pc.niq, 0:pc._nv] = pc.G
    G_intp[:N, pc.niq: 2 * pc.niq, pc._nv: 2 * pc._nv] = pc.G[1:]

    lG_intp = np.empty((pc.N+1, 2 * pc.niq))
    lG_intp[:, :pc.niq] = pc.lG
    lG_intp[:N, pc.niq:] = pc.lG[1:]
    hG_intp = np.empty((pc.N+1, 2 * pc.niq))
    hG_intp[:, :pc.niq] = pc.hG
    hG_intp[:N, pc.niq:] = pc.hG[1:]

    G_intp[N, 0:pc.niq, 0:pc._nv] = pc.G[N]
    G_intp[N, pc.niq: 2 * pc.niq, pc._nv: 2 * pc._nv] = pc.G[N]
    lG_intp[N] = np.hstack((pc.lG[N], pc.lG[N]))
    hG_intp[N] = np.hstack((pc.hG[N], pc.hG[N]))

    return PathConstraint(a=a_intp, b=b_intp, c=c_intp,
                          abar=abar_intp, bbar=bbar_intp, cbar=cbar_intp,
                          D=D_intp, l=l_intp, h=h_intp,
                          G=G_intp, lG=lG_intp, hG=hG_intp,
                          name=pc.name, ss=pc.ss)


def create_full_contact_path_constraint(path, ss, robot, stance):
    """Contact stability constraint (Colomb frictional model).

    Parameters
    ---------
    path : :class:`.SplineInterpolator`
    ss : array.
        Shape (N+1, ). Discretization grid points.
    robot : :class:`Pymanoid.Humanoid`
        Used for dynamics computation.
        Torque bounds are taken from the internal OpenRAVE robot.
    stance : :class:`Pymanoid.Stance`
        Used for wrench constraint.

    Returns
    -------
    res : :class:`.PathConstraint`
        Contact stability constraint.

    Note
    ----

    The dynamics equation of a robot is given by:

    .. math:: \mathbf{M}(\mathbf{q}) \ddot{\mathbf{q}}+
            \mathbf{C}(\mathbf{q}, \dot{\mathbf{q}})+
            \mathbf{g}(\mathbf{q}) = \mathbf{\\tau} +
            \sum_i\mathbf{J}_i(\mathbf{q}, \mathbf{p}_i)^T
            \mathbf{w}_i,

    where

    - :math:`\mathbf{q}, \dot{\mathbf{q}}, \ddot{\mathbf{q}}`: robot joint position, velocity and acceleration.
    - :math:`\\tau`: robot joint torque.
    - :math:`\mathbf{w}_i`: the i-th local contact wrench acting on a link on the robot.
    - :math:`\mathbf{p}_i`: the origin of the wrench w_i.
    - :math:`\mathbf{J}_i`: the wrench Jacobian at p_i of the link w_i acts on.

    The slack variable is given by :math:`\mathbf{v} := [\\tau', \mathbf{w}_1', \mathbf{w}_2', ...]'`.

    """
    N = len(ss) - 1
    q = path.eval(ss)
    qs = path.evald(ss)
    qss = path.evaldd(ss)
    torque_bnd = robot.rave.GetDOFTorqueLimits()
    dof = path.dof

    neq = dof
    nv = dof + 6 * len(stance.contacts)
    niq = sum(co.wrench_face.shape[0] for co in stance.contacts)

    abar = np.zeros((N + 1, neq))
    bbar = np.zeros((N + 1, neq))
    cbar = np.zeros((N + 1, neq))
    D = np.zeros((N + 1, neq, nv))
    l = np.zeros((N + 1, nv))
    h = np.zeros((N + 1, nv))
    G = np.zeros((N + 1, niq, nv))
    lG = np.zeros((N + 1, niq))
    hG = np.zeros((N + 1, niq))

    for i in range(N + 1):
        # t1,t2,t3,t4 are coefficients of the Path-Torque formulae
        t1, t3, t4 = inv_dyn(robot.rave, q[i], qs[i], qs[i])
        t2, _, _ = inv_dyn(robot.rave, q[i], qs[i], qss[i])
        abar[i] = t1
        bbar[i] = t2 + t3
        cbar[i] = t4
        D[i, :, :dof] = np.eye(dof)
        r = 0
        for con in stance.contacts:
            J_wrench = compute_jacobian_wrench(robot.rave, con.link, con.p)
            D[i, :, dof + r: dof + r + 6] = J_wrench.T
            r += 6
        l[i, :dof] = - torque_bnd
        h[i, :dof] = + torque_bnd
        l[i, dof:] = - INFTY  # Safety bounds.
        h[i, dof:] = INFTY

        _G_block = block_diag(*[co.wrench_face for co in stance.contacts])
        G[i] = np.hstack((np.zeros((niq, dof)), _G_block))
        lG[i, :] = - INFTY
        hG[i, :] = 0
    return PathConstraint(abar=abar, bbar=bbar, cbar=cbar, D=D,
                          l=l, h=h, lG=lG, G=G,
                          hG=hG, ss=ss, name='FullContactStability')


def create_pymanoid_contact_stability_path_constraint(
        path, ss, robot, contact_set, g):
    """Contact stability constraint in canonical form.

    This is the reduced form of the full contact stability constraint
    described in :func:`create_full_contact_path_constraint`.

    Parameters
    ----------
    path : :class:`.SplineInterpolator`
    ss : array
        Shape (N+1, ). Grid points.
    robot : :class:`Pymanoid.Humanoid`
        Used for dynamics computation.
        Torque bounds are taken from the internal OpenRAVE robot.
    contact_set : :class:`Pymanoid.ContactSet`
        Used for wrench computation.

    Returns
    -------
    res : :class:`PathConstraint`
        The resulting path constraint.
    """
    N = len(ss) - 1
    q = path.eval(ss)
    qs = path.evald(ss)
    qss = path.evaldd(ss)
    pO = np.zeros(3)  # fixed point

    F = contact_set.compute_wrench_face(pO)
    niq = F.shape[0]  # Number of inequalities
    m = robot.mass
    a = np.zeros((N + 1, niq))
    b = np.zeros((N + 1, niq))
    c = np.zeros((N + 1, niq))

    # Let O be a chosen pO, EL equation yields
    #     w^gi + w^c = 0,
    # where w^gi is the gravito-inertial wrench taken at O, w^c is the
    # contact wrench taken at O.
    for i in range(N + 1):
        robot.set_dof_values(q[i])
        J_COM = robot.compute_com_jacobian()
        H_COM = robot.compute_com_hessian()
        J_L = robot.compute_angular_momentum_jacobian(pO)
        H_L = robot.compute_angular_momentum_hessian(pO)
        a_P = m * np.dot(J_COM, qs[i])
        b_P = m * (np.dot(J_COM, qss[i]) +
                   np.dot(qs[i], np.dot(H_COM, qs[i])))
        a_L = np.dot(J_L, qs[i])
        b_L = np.dot(J_L, qss[i]) + np.dot(qs[i], np.dot(H_L, qs[i]))
        pG = robot.com
        a[i] = np.dot(F, np.r_[a_P, a_L])
        b[i] = np.dot(F, np.r_[b_P, b_L])
        c[i] = - np.dot(F, np.r_[m * g, m * np.cross(pG, g)])

    return PathConstraint(a, b, c, name="ContactStability", ss=ss)


def create_rave_re_torque_path_constraint(path, ss, robot, J_lc,
                                          torque_bnd=None):
    """Torque bounds for robots under loop closure constraints.

    Roughly speadking, under loop closure constraints, only virtual
    displacements :math:`d\mathbf{q}` satisfying

    .. math ::
          \mathbf{J}_{loop}(\mathbf{q}) d\mathbf{q} = 0,

    is admissible.

    Parameters
    ----------
    path : :class:`.SplineInterpolator`
    ss : array
        Shape (N+1, ). Grid points.
    robot : :class:`openravepy.Robot`
        Used for dynamics computation.
    J_lc : func
        A mapping q -> an (d, dof) ndarray.

    Returns
    -------
    res : :class:`PathConstraint`
        The resulting path constraint.

    """
    N = len(ss) - 1
    q = path.eval(ss)
    qs = path.evald(ss)
    qss = path.evaldd(ss)
    dof = path.dof

    if torque_bnd is None:
        torque_bnd = robot.GetDOFTorqueLimits()
    a = np.zeros((N + 1, dof))
    b = np.zeros((N + 1, dof))
    c = np.zeros((N + 1, dof))
    D = np.zeros((N + 1, dof, dof))
    l = -torque_bnd * np.ones((N + 1, dof))
    h = torque_bnd * np.ones((N + 1, dof))

    for i in range(N + 1):
        qi = q[i]
        qsi = qs[i]
        qssi = qss[i]
        # Column of N span the null space of J_lc(q)
        J_lp = J_lc(qi)
        u, s, v = np.linalg.svd(J_lp)
        # Collect column index
        s_full = np.zeros(dof)
        s_full[:s.shape[0]] = s
        # Form null matrix
        N = v[s_full < 1e-5].T
        D[i][:N.shape[1]] = N.T

        # t1,t2,t3,t4 are coefficients of the Path-Torque formulae
        t1, t3, t4 = inv_dyn(robot, qi, qsi, qsi)
        t2, _, _ = inv_dyn(robot, qi, qsi, qssi)

        a[i] = np.dot(D[i], t1)
        b[i] = np.dot(D[i], t2 + t3)
        c[i] = np.dot(D[i], t4)

    return PathConstraint(abar=a, bbar=b, cbar=c, D=D, l=l, h=h,
                          name="RedundantTorqueBounds", ss=ss)


def create_rave_torque_path_constraint(path, ss, robot):
    """Torque bounds for an OpenRAVE robot.

    Path-Torque constraint has the form

            sdd * M(q) qs(s)
          + sd ^ 2 [M(q) qss(s) + qs(s)^T C(q) qs(s)]
          + g(q(s)) = tau

            taumin <= tau <= taumax

    As canonical constraint.
            sdd * Ai + sd^2 Bi + Ci <= 0

    Parameters
    ----------
    path : Interpolator
        Represents the underlying geometric path.
    ss : ndarray
        Discretization gridpoints.
    robot : OpenRAVE.Robot
        Robot model to provide dynamics matrices

    Returns
    -------
    out : PathConstraint
        The equivalent path constraint.
    """
    N = len(ss) - 1
    q = path.eval(ss)
    qs = path.evald(ss)
    qss = path.evaldd(ss)
    dof = path.dof

    tau_bnd = robot.GetDOFTorqueLimits()
    a = np.zeros((N + 1, 2 * dof))
    b = np.zeros((N + 1, 2 * dof))
    c = np.zeros((N + 1, 2 * dof))
    for i in range(N + 1):
        qi = q[i]
        qsi = qs[i]
        qssi = qss[i]

        # t1,t2,t3,t4 are coefficients of the Path-Torque formulae
        t1, t3, t4 = inv_dyn(robot, qi, qsi, qsi)
        t2, _, _ = inv_dyn(robot, qi, qsi, qssi)

        a[i, :dof] = t1
        a[i, dof:] = -t1
        b[i, :dof] = t2 + t3
        b[i, dof:] = -t2 - t3
        c[i, :dof] = t4 - tau_bnd
        c[i, dof:] = -t4 - tau_bnd

    logger.info("Torque bounds for OpenRAVE robot generated.")
    return PathConstraint(a, b, c, name="TorqueBounds", ss=ss)


def create_velocity_path_constraint(path, ss, vlim):
    """ Return joint velocities bound.

    Velocity constraint has the form:
                0 * ui +  1 * xi - sdmax^2 <= 0
                0      + -1      + sdmin^2 <= 0

    Parameters
    ----------
    path : Interpolator
    vlim : ndarray, shaped (dof, 2)
        Joint velocity limits.
    ss : ndarray, shaped (N+1,)
        Discretization gridpoints.

    Returns
    -------
    pc : PathConstraint
    """
    qs = path.evald(ss)
    # Return resulti from cython version
    a, b, c = _create_velocity_constraint(qs, vlim)
    return PathConstraint(a, b, c, name="Velocity", ss=ss)


def create_acceleration_path_constraint(path, ss, alim):
    """ Joint accelerations bound.

    Acceleration constraint form:

                qs(si) ui + qss(si) sdi ^ 2 - qdmax <= 0
               -qs(si) ui - qss(si) sdi ^ 2 + qdmin <= 0

    Parameters
    ----------
    path : Interpolator
    alim : ndarray, shaped (dof, 2)
        Joint acceleration limits.
    ss : ndarray, shaped (N+1,)
        Discretization gridpoints.

    Returns
    -------
    pc : PathConstraint
        

    """
    N = len(ss) - 1
    qs = path.evald(ss)
    qss = path.evaldd(ss)

    alim = np.array(alim)
    dof = path.dof  # dof

    if dof != 1:  # Non-scalar
        a = np.hstack((qs, -qs))
        b = np.hstack((qss, -qss))
        c = np.zeros((N + 1, 2 * dof))
        c[:, :dof] = -alim[:, 1]
        c[:, dof:] = alim[:, 0]
    else:
        a = np.vstack((qs, -qs)).T
        b = np.vstack((qss, -qss)).T
        c = np.zeros((N + 1, 2))
        c[:, 0] = -alim[:, 1]
        c[:, 1] = alim[:, 0]

    return PathConstraint(a, b, c, name="Acceleration", ss=ss)


