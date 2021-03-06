""" A question on cstheory stackexchange [1] got me curious about MCMC for spanning trees

Here is a model to explore the issue

[1] http://cstheory.stackexchange.com/questions/3913/how-can-i-randomly-generate-bounded-height-spanning-trees
"""

import numpy as np
import pymc as pm
import networkx as nx
import random
import views

def my_grid_graph(shape):
    """ Create an nxn grid graph, with uniformly random edge weights,
    and a position dict G.pos
    """

    G = nx.grid_graph(list(shape))
    for u,v in G.edges():
        G[u][v]['weight'] = random.random()

    G.pos = {}
    for v in G:
        G.pos[v] = [v[0], -v[1]]

    return G

def dual_grid_edge(u, v):
    """ Helper function to map an edge in a lattice to corresponding
    edge in dual lattice (it's just a rotation)
    """
    mx = .5 * (u[0] + v[0])
    my = .5 * (u[1] + v[1])
    dx = .5 * (u[0] - v[0])
    dy = .5 * (u[1] - v[1])
    return ((mx+dy, my+dx), (mx-dy, my-dx))

def dual_grid(G, T):
    """ Make a maze from the dual of G minus the dual of T

    Assumes that G is the base graph is a grid with integer labels
    Note that T doesn't have to be a tree
    """
    D = nx.Graph()

    # add dual complement edges
    for v in T.nodes():
        for u in G[v]:
            if not T.has_edge(u,v):
                D.add_edge(*dual_grid_edge(u,v))
    return D

def my_path_graph(path):
    """ Create a graph by connecting vertices on path
    """
    G = nx.Graph()
    G.add_path(path)
    return G

def image_grid_graph(fname, colors=set([(0,0,0,255)])):
    """ Create a subgraph H of an nxn grid graph by including all
    edges between pixels of specified color.  H.base_graph is the full grid graph
    (generated by my_grid_graph)
    """
    from PIL import Image
    im = Image.open(fname)
    im.putalpha(255)
    pix = im.load()

    G = my_grid_graph(im.size)
    for u in G.nodes():
        G.node[u]['color'] = np.array(pix[u])/256.

    H = nx.Graph()
    for u, v in G.edges():
        if pix[u] in colors and pix[v] in colors:
            H.add_edge(u,v)
    H.base_graph = G

    return H

def BDST(G, root=(0,0), k=5, beta=1.):
    """ Create a PyMC Stochastic for a Bounded Depth Spanning Tree on
    base graph G

    Parameters
    ----------
    G : nx.Graph, base graph to span
    k : int, depth bound parameter
    beta : float, "inverse-temperature parameter" for depth bound
    """

    T = nx.minimum_spanning_tree(G)
    T.base_graph = G
    T.root = root
    T.k = k

    @pm.stoch(dtype=nx.Graph)
    def bdst(value=T, root=root, k=k, beta=beta):
        path_len = np.array(list(nx.shortest_path_length(value, root).values()))
        return -beta * np.sum(path_len > k)

    return bdst

def LDST(G, d=3, beta=1.):
    """ Create a PyMC Stochastic for a random lower degree Spanning Tree on
    base graph G

    Parameters
    ----------
    G : nx.Graph, base graph to span
    d : int, degree bound parameter
    beta : float, "inverse-temperature parameter" for depth bound
    """

    T = nx.minimum_spanning_tree(G)
    T.base_graph = G
    T.d = d

    @pm.stoch(dtype=nx.Graph)
    def ldst(value=T, beta=beta):
        return -beta * np.sum(np.array(list(T.degree().values())) >= d)

    return ldst

class STMetropolis(pm.Metropolis):
    """ A PyMC Step Method that walks on spanning trees by adding a
    uniformly random edge not in the tree, removing a uniformly random
    edge from the cycle created, and keeping it with the appropriate
    Metropolis probability (no Hastings factor necessary, because the
    chain is reversible, right?)

    Parameters
    ----------
    stochastic : nx.Graph that is a tree and has a base_graph which it
                 spans
    """
    def __init__(self, stochastic):
        # Initialize superclass
        pm.Metropolis.__init__(self, stochastic, scale=1., verbose=0, tally=False)

    def propose(self):
        """ Add an edge and remove an edge from the cycle that it creates"""
        T = self.stochastic.value

        T.u_new, T.v_new = T.edges()[0]
        while T.has_edge(T.u_new, T.v_new):
            T.u_new, T.v_new = random.choice(T.base_graph.edges())

        T.path = nx.shortest_path(T, T.u_new, T.v_new)
        i = random.randrange(len(T.path)-1)
        T.u_old, T.v_old = T.path[i], T.path[i+1]
        
        T.remove_edge(T.u_old, T.v_old)
        T.add_edge(T.u_new, T.v_new)
        self.stochastic.value = T

    def reject(self):
        """ Restore the graph to its state before more recent edge swap"""
        T = self.stochastic.value
        T.add_edge(T.u_old, T.v_old)
        T.remove_edge(T.u_new, T.v_new)
        self.stochastic.value = T

def anneal_ldst(n=11, phases=10, iters=1000):
    """ MCMC/simulated annealing to generate a random low-degree
    spanning tree on a grid graph

    Parameters
    ----------
    n : int, size of grid
    phases : int, optional, number of cooling phases
    iters : int, optional, number of MCMC steps per phase
    
    Returns
    -------
    T : nx.Graph, spanning tree with T.base_graph, with few degree 3 vertices
    """
    beta = pm.Uninformative('beta', value=1.)
    ldst = LDST(my_grid_graph([n,n]), beta=beta)

    mod_mc = pm.MCMC([beta, ldst])
    mod_mc.use_step_method(STMetropolis, ldst)
    mod_mc.use_step_method(pm.NoStepper, beta)

    for i in range(phases):
        print('phase %d' % (i+1),)
        beta.value = i*5
        mod_mc.sample(iters, burn=iters-1)
        print('frac of deg 2 vtx = %.2f' % np.mean(np.array(ldst.value.degree().values()) == 2))
    return ldst.value

def anneal_bdst(n=11, depth=10, phases=10, iters=1000):
    """ MCMC/simulated annealing to generate a random bounded-depth spanning tree
    Parameters
    ----------
    n : int, size of grid
    depth : int, optional, target bound on depth

    Returns
    -------
    T : nx.Graph, spanning tree with T.base_graph, possibly with degree bound satisfied
    """

    beta = pm.Uninformative('beta', value=1.)

    G = nx.grid_graph([n, n])
    root = ((n-1)/2, (n-1)/2)
    bdst = BDST(G, root, depth, beta)

    @pm.deterministic
    def max_depth(T=bdst, root=root):
        shortest_path_length = nx.shortest_path_length(T, root)
        T.max_depth = max(shortest_path_length.values())
        return T.max_depth

    mod_mc = pm.MCMC([beta, bdst, max_depth])
    mod_mc.use_step_method(STMetropolis, bdst)
    mod_mc.use_step_method(pm.NoStepper, beta)

    for i in range(phases):
        beta.value = i*5
        mod_mc.sample(iters, thin=max(1, iters/100))
        print('cur depth', max_depth.value)
        print('pct of trace with max_depth <= depth', np.mean(mod_mc.trace(max_depth)[:] <= depth))
    return bdst.value

