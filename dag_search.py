'''
Operations for combining computational graphs
'''
import numpy as np
import itertools
import config
import warnings
from scipy.optimize import minimize
import comp_graph
from tqdm import tqdm
import pickle
import multiprocessing
import sklearn

########################
# Loss Function + Optimizing constants
########################
class DAG_Loss_fkt(object):
    '''
    Abstract class for Loss function
    '''
    def __init__(self):
        pass
        
    def __call__(self, X:np.ndarray, cgraph:comp_graph.CompGraph, c:np.ndarray) -> np.ndarray:
        '''
        Lossfkt(X, graph, consts)

        @Params:
            X... input for DAG (N x m)
            cgraph... computational Graph
            c... array of constants (2D)

        @Returns:
            Loss function for different constants
        '''
        pass

class MSE_loss_fkt(DAG_Loss_fkt):
    def __init__(self, outp:np.ndarray):
        '''
        Loss function for finding DAG for regression task.

        @Params:
            outp... output that DAG should match (N x n)
        '''
        self.outp = outp
        
    def __call__(self, X:np.ndarray, cgraph:comp_graph.CompGraph, c:np.ndarray) -> np.ndarray:
        '''
        Lossfkt(X, graph, consts)

        @Params:
            X... input for DAG (N x m)
            cgraph... computational Graph
            c... array of constants (2D)

        @Returns:
            MSE of graph output and desired output for different constants
        '''
        if len(c.shape) == 2:
            r = c.shape[0]
            vec = True
        else:
            r = 1
            c = c.reshape(1, -1)
            vec = False

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            
            pred = cgraph.evaluate(X, c = c)
            losses = np.mean((pred.reshape(r, -1) - self.outp.flatten())**2, axis=-1)
            
            # must not be nan or inf
            invalid = np.zeros(r).astype(bool)
            invalid = invalid | np.isnan(losses)
            invalid = invalid | np.isinf(losses)
            
        # consider not using inf, since optimizers struggle with this
        losses[invalid] = 1000
        losses[losses > 1000] = 1000

        if not vec:
            return losses[0]
        else:
            return losses


def get_consts_grid(cgraph:comp_graph.CompGraph, X:np.ndarray, loss_fkt:DAG_Loss_fkt, c_init:np.ndarray = 0, interval_size:float = 2.0, n_steps:int = 51, return_arg:bool = False) -> tuple:
    '''
    Given a computational graph, optimizes for constants using grid search.

    @Params:
        cgraph... computational graph
        X... input for DAG
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        c_start... if given, start point for optimization
        max_it... maximum number of retries
        c_init... initial constants
        interval_size... size of search interval around c_init
    @Returns:
        constants that have lowest loss, loss
    '''
    k = cgraph.n_consts
    if k == 0:
        consts = np.array([])
        loss = loss_fkt(X, cgraph, consts)
        return consts, loss

    if not (type(c_init) is np.ndarray):
        c_init = c_init*np.ones(k)

    l = interval_size/2
    values = np.linspace(-l, l, n_steps)
    tmp = np.meshgrid(*[values]*k)
    consts = np.column_stack([x.flatten() for x in tmp])
    consts = consts + np.stack([c_init]*len(consts))

    losses = loss_fkt(X, cgraph, consts)

    best_idx = np.argmin(losses)
    return consts[best_idx], losses[best_idx]

def get_consts_grid_zoom(cgraph:comp_graph.CompGraph, X:np.ndarray, loss_fkt:DAG_Loss_fkt, interval_lower:float = -1, interval_upper:float = 1, n_steps:int = 21, n_zooms:int = 2) -> tuple:
    '''
    Given a computational graph, optimizes for constants using grid search with zooming.

    @Params:
        cgraph... computational graph
        X... input for DAG
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        c_start... if given, start point for optimization
        max_it... maximum number of retries
        interval_lower... minimum value for initial constants
        interval_upper... maximum value for initial constants

    @Returns:
        constants that have lowest loss, loss
    '''
    
    k = cgraph.n_consts
    interval_size = interval_upper - interval_lower
    c = (interval_upper + interval_lower)/2*np.ones(k)
    stepsize = interval_size/(n_steps - 1)
    for zoom in range(n_zooms):
        c, loss = get_consts_grid(cgraph, X, loss_fkt, c_init=c, interval_size = interval_size, n_steps=n_steps)
        interval_size = 2*stepsize
        stepsize = interval_size/(n_steps - 1)

    return c, loss

def get_consts_opt(cgraph:comp_graph.CompGraph, X:np.ndarray, loss_fkt:DAG_Loss_fkt, c_start:np.ndarray = None, max_it:int = 5, interval_lower:float = -1, interval_upper:float = 1) -> tuple:
    '''
    Given a computational graph, optimizes for constants using scipy.

    @Params:
        cgraph... computational graph
        X... input for DAG
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        c_start... if given, start point for optimization
        max_it... maximum number of retries
        interval_lower... minimum value for initial constants
        interval_upper... maximum value for initial constants

    @Returns:
        constants that have lowest loss, loss
    '''

    n_constants = cgraph.n_consts
    
    options = {'maxiter' : 20}
    def opt_func(c):
        return loss_fkt(X, cgraph, np.reshape(c, (1, -1)))[0]
    
    if n_constants > 0:
        success = False
        it = 0
        best_c = np.zeros(n_constants)

        if c_start is not None:
            best_c = c_start
            it = max_it - 1

        best_loss = opt_func(best_c)
        while (not success) and (it < max_it):
            it += 1
            if c_start is not None:
                x0 = c_start
            else:
                x0 = np.random.rand(n_constants)*(interval_upper - interval_lower) + interval_lower
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = minimize(fun = opt_func, x0 = x0, method = 'BFGS', options = options)
            success = res['success'] or (res['fun'] < best_loss)
        if success:
            c = res['x']
        else:
            c = best_c
    else:
        c = np.array([])
        
    return c, opt_func(c)

def get_consts_pool(cgraph:comp_graph.CompGraph, X:np.ndarray, loss_fkt:DAG_Loss_fkt, pool:list = config.CONST_POOL) -> tuple:
    '''
    Given a computational graph, optimizes for constants using a fixed pool of constants.

    @Params:
        cgraph... computational graph
        X... input for DAG
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        pool... list of constants

    @Returns:
        constants that have lowest loss, loss
    '''

    k = cgraph.n_consts

    if k > 0:
        #c_combs = itertools.permutations(pool, r = k)
        c_combs = np.stack([np.array(c) for c in itertools.combinations(pool, r = k)])
        losses = loss_fkt(X, cgraph, c_combs)

        best_idx = np.argmin(losses)
        best_loss = losses[best_idx]
        best_c = c_combs[best_idx]
    
        return best_c, best_loss
    return np.array([]), loss_fkt(X, cgraph, np.array([]))

########################
# DAG creation
########################

def get_pre_order(order:list, node:int, inp_nodes:list, inter_nodes:list, outp_nodes:list) -> tuple:
    '''
    Given a DAG creation order, returns the pre order of the subtree with a given node as root.
    Only used internally by get_build_orders.
    @Params:
        order... list of parents for each node (2 successive entries = 1 node)
        node... node in order list. This node will be root
        inp_nodes... list of nodes that are input nodes
        inter_nodes... list of nodes that are intermediate nodes
        out_nodes... list of nodes that are output nodes

    @Returns:
        preorder as tuple
    '''

    if node in outp_nodes:
        idx = 2*len(inter_nodes) + 2*(node-len(inp_nodes))
    else:
        idx = 2*(node - len(inp_nodes) - len(outp_nodes))
    idx_l = idx
    idx_r = idx + 1
    
    v_l = order[idx_l]
    v_r = order[idx_r]
    
    if (v_l in inp_nodes):
        return (node, v_l, v_r)
    elif (v_r in inp_nodes) or (v_r < 0):
        return (node,) + get_pre_order(order, v_l, inp_nodes, inter_nodes, outp_nodes) + (v_r,)
    else:
        return (node, ) +get_pre_order(order, v_l, inp_nodes, inter_nodes, outp_nodes) + get_pre_order(order, v_r, inp_nodes, inter_nodes, outp_nodes)

def build_dag(build_order:list, node_ops:list, m:int, n:int, k:int) -> comp_graph.CompGraph:
    '''
    Given a build order, builds a computational DAG.

    @Params:
        build_order... list of tuples (node, parent_nodes)
        node_ops... list of operations for each node
        m... number of input nodes
        n... number of output nodes
        k... number of constant nodes

    @Returns:
        Computational DAG
    '''

    node_dict = {}
    for i in range(m):
        node_dict[i] = ([], 'inp')
    for i in range(k):
        node_dict[i + m] = ([], 'const')

    for op, (i, parents) in zip(node_ops, build_order):
        node_dict[i] = (list(parents), op)
    return comp_graph.CompGraph(m = m, n = n, k = k, node_dict = node_dict)

def get_build_orders(m:int, n:int, k:int, n_calc_nodes:int, max_orders:int = 10000, verbose:int = 0) -> list:
    '''
    Creates empty DAG scaffolds (no operations yet).

    @Params:
        m... number of input nodes
        n... number of output nodes
        k... number of constant nodes
        n_calc_nodes... number of intermediate nodes
        max_orders... maximum number of possible DAG orders to search trough (lower = exhaustive, higher = sampling)
        verbose... 0 - no print, 1 - status message, 2 - progress bar

    @Returns:
        list build orders (can be used by build_dag).
        build order = list of tuples (node, parent_nodes)
    '''

    if verbose > 0:
        print('Creating evaluation orders')
    l = n_calc_nodes
    inp_nodes = [i for i in range(m + k)]
    outp_nodes = [i + m + k for i in range(n)]
    inter_nodes = [i + m + k + n for i in range(l)]

    # collect possible predecessors
    predecs = {}
    for i in inp_nodes:
        predecs[i] = []

    for i in outp_nodes:
        predecs[i] = inp_nodes + inter_nodes

    for i in inter_nodes:
        predecs[i] = inp_nodes + [j for j in inter_nodes if j < i]

    # create sample space for edges
    sample_space_edges = []
    for i in inter_nodes + outp_nodes:
        sample_space_edges.append(predecs[i])
        sample_space_edges.append(predecs[i] + [-1])
    #total_its = np.prod([len(s) for s in sample_space_edges]) # potential overflow!
    if np.sum([np.log(len(s)) for s in sample_space_edges]) > np.log(max_orders):
        # just sample random orders
        possible_edges = []
        for _ in range(max_orders):
            order = []
            for tmp in sample_space_edges:
                order.append(np.random.choice(tmp))
            possible_edges.append(order)
    else:
        possible_edges = itertools.product(*sample_space_edges)
    valid_set = set()
    build_orders = []

    if verbose == 2:
        total_its = np.prod([len(s) for s in sample_space_edges])
        pbar = tqdm(possible_edges, total = total_its)
    else: 
        pbar = possible_edges

    for order in pbar:
        # order -> ID
        valid = True
        for i in range(l + n):
            if order[2*i] < order[2*i + 1]:
                valid = False
                break
        if valid:
            order_ID = ()
            # pre orders of outputs
            for i in range(n):
                order_ID = order_ID + get_pre_order(order, m + k + i, inp_nodes, inter_nodes, outp_nodes)
            

            # rename intermediate nodes after the order in which they appear ( = 1 naming)
            ren_dict = {}
            counter = 0
            for i in order:
                if (i in inter_nodes) and (i not in ren_dict):
                    ren_dict[i] = inter_nodes[counter]
                    counter += 1
            tmp_ID = tuple([ren_dict[node] if node in ren_dict else node for node in order_ID])
            is_new = tmp_ID not in valid_set
            
        
            
            if is_new:
                valid_set.add(tmp_ID)
                
                # build (node, parents) order for intermediate + output nodes
                tmp = sorted([i for i in set(order_ID) if i in inter_nodes])
                ren_dict = {node : inter_nodes[i] for i, node in enumerate(tmp)}
                build_order = []
                for i in sorted(tmp + outp_nodes):
                    if i in ren_dict:
                        # intermediate node
                        i1 = 2*(i - (m + k + n))
                        i2 = 2*(i - (m + k + n)) + 1
                    elif i in outp_nodes:
                        # output node
                        i1 = 2*l + 2*(i - (m + k))
                        i2 = 2*l + 2*(i - (m + k)) + 1
                    p1 = order[i1]
                    p2 = order[i2]
                    preds = []
                    if p1 in ren_dict:
                        preds.append(ren_dict[p1])
                    else:
                        preds.append(p1)
                    if p2 in ren_dict:
                        preds.append(ren_dict[p2])
                    elif p2 >= 0:
                        preds.append(p2)

                    if i in ren_dict:
                        build_order.append((ren_dict[i], tuple(preds)))
                    else:
                        build_order.append((i, tuple(preds)))
                build_orders.append(tuple(build_order))
                
    return build_orders

def evaluate_cgraph(cgraph:comp_graph.CompGraph, X:np.ndarray, loss_fkt:callable, opt_mode:str = 'grid_zoom', loss_thresh:float = None) -> tuple:
    '''
    Dummy function. Optimizes for constants.

    @Params:
        cgraph... computational DAG with constant input nodes
        X... input for DAG
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        opt_mode... one of {pool, opt, grid, grid_opt, grid_zoom}
        loss_thresh... only set in multiprocessing context - to communicate between processes

    @Returns:
        tuple of consts = array of optimized constants, loss = float of loss
    '''
    evaluate = True
    if loss_thresh is not None:
        # we are in parallel mode
        global stop_var
        evaluate = not bool(stop_var)

    if evaluate:

        assert opt_mode in ['pool', 'opt', 'grid', 'grid_opt', 'grid_zoom'], 'Mode has to be one of {pool, opt, grid, grid_opt}'

        if opt_mode == 'pool':
            consts, loss = get_consts_pool(cgraph, X, loss_fkt)
        elif opt_mode == 'opt':
            consts, loss = get_consts_opt(cgraph, X, loss_fkt)
        elif opt_mode == 'grid':
            consts, loss = get_consts_grid(cgraph, X, loss_fkt)
        elif opt_mode == 'grid_zoom':
            consts, loss = get_consts_grid_zoom(cgraph, X, loss_fkt)
        elif opt_mode == 'grid_opt':
            consts, loss = get_consts_grid(cgraph, X, loss_fkt)
            consts, loss = get_consts_opt(cgraph, X, loss_fkt, c_start=consts)

        if loss_thresh is not None:
            if loss <= loss_thresh:
                stop_var = True
        return consts, loss
    else:
        return np.array([]), np.inf
        
def evaluate_build_order(order:list, m:int, n:int, k:int, X:np.ndarray, loss_fkt:callable, opt_mode:str = 'grid', loss_thresh:float = None) -> tuple:
    '''
    Given a build order (output of get_build_orders), tests all possible assignments of operators.

    @Params:
        order... list of tuples (node, parent_nodes)
        m... number of input nodes
        n... number of output nodes
        k... number of constant nodes
        X... input for DAGs
        loss_fkt... function f where f(X, graph, const) indicates how good the DAG is
        opt_mode... one of {pool, opt, grid, grid_opt}
        loss_thresh... only set in multiprocessing context - to communicate between processes
        
    @Returns:
        tuple:
            constants... list of optimized constants
            losses... list of losses for DAGs
            ops... list of ops that were tried
    '''

    
    bin_ops = [op for op in config.NODE_ARITY if config.NODE_ARITY[op] == 2]
    un_ops = [op for op in config.NODE_ARITY if config.NODE_ARITY[op] == 1]

    outp_nodes = [m + k + i for i in range(n)]
    op_spaces = []
    for node, parents in order:
        if len(parents) == 2:
            op_spaces.append(bin_ops)
        else:
            if node in outp_nodes:
                op_spaces.append(un_ops)
            else:
                op_spaces.append([op for op in un_ops if op != '='])
            

    ret_consts = []
    ret_losses = []
    ret_ops = []
    for ops in itertools.product(*op_spaces):
        cgraph = build_dag(order, ops, m, n, k)
        consts, loss = evaluate_cgraph(cgraph, X, loss_fkt, opt_mode, loss_thresh)
        ret_consts.append(consts)
        ret_losses.append(loss)
        ret_ops.append(ops)

    return ret_consts, ret_losses, ret_ops

def sample_graph(m:int, n:int, k:int, n_calc_nodes:int) -> comp_graph.CompGraph:
    '''
    Samples a computational DAG.

    @Params:
        m... number of input nodes
        n... number of output nodes
        k... number of constant nodes
        n_calc_nodes... number of intermediate nodes

    @Returns:
        computational DAG
    '''


    # 1. Sample build order
    l = n_calc_nodes
    inp_nodes = [i for i in range(m + k)]
    outp_nodes = [i + m + k for i in range(n)]
    inter_nodes = [i + m + k + n for i in range(l)]

    bin_ops = [op for op in config.NODE_ARITY if config.NODE_ARITY[op] == 2]
    un_ops = [op for op in config.NODE_ARITY if config.NODE_ARITY[op] == 1]

    predecs = {}
    for i in inp_nodes:
        predecs[i] = []

    for i in outp_nodes:
        predecs[i] = inp_nodes + inter_nodes
        
    for i in inter_nodes:
        predecs[i] = inp_nodes + [j for j in inter_nodes if j < i]

    # sample order from predecessors
    order = []
    for i in inter_nodes + outp_nodes:
        l1 = predecs[i]
        order.append(np.random.choice(l1))

        l2 = [j for j in (predecs[i] + [-1]) if j < order[-1]]
        order.append(np.random.choice(l2))

    # order -> ID
    dep_entries = [l*2 + 2*i for i in range(n)] + [l*2 + 2*i + 1 for i in range(n)] # order indicies that are dependend
    tmp = set([order[i] for i in dep_entries if order[i] not in inp_nodes and order[i] >= 0]) # node indices that remain
    while len(tmp) > 0:
        tmp_deps = []
        for idx in tmp:
            tmp_deps.append(2*(idx - (m + k + n)))
            tmp_deps.append(2*(idx - (m + k + n)) + 1)
        tmp_deps = list(set(tmp_deps))
        dep_entries = dep_entries + tmp_deps
        tmp = set([order[i] for i in tmp_deps if order[i] not in inp_nodes and order[i] >= 0])
    dep_entries = sorted(dep_entries)
        
    ren_dict = {}
    order_ID = []
    counter = m + k
    for i in range(m + k, m + k + n + l):
        if i in outp_nodes:
            i1 = 2*l + 2*(i - (m + k))
            i2 = 2*l + 2*(i - (m + k)) + 1
        else:
            i1 = 2*(i - (m + k + n))
            i2 = 2*(i - (m + k + n)) + 1
        preds = []
        if i1 in dep_entries:
            preds.append(order[i1])
        if i2 in dep_entries and order[i2] >= 0:
            preds.append(order[i2])

        if len(preds) > 0:
            ren_dict[i] = counter
            order_ID.append((i, tuple(preds)))
            counter += 1
    new_order_ID = []
    for i, preds in order_ID:
        new_preds = [j if j not in ren_dict else ren_dict[j] for j in preds]
        new_order_ID.append((ren_dict[i], tuple(new_preds)))
    order = tuple(new_order_ID)
        
    # 2. sample operations on build order
    node_ops = []
    for _, parents in order:
        if len(parents) == 2:
            node_ops.append(np.random.choice(bin_ops))
        else:
            node_ops.append(np.random.choice(un_ops))

    # 3. create cgraph
    return build_dag(order, node_ops, m, n, k)

########################
# Search Methods
########################

def init_process(early_stop):
    global stop_var
    stop_var = early_stop 

def is_pickleable(x:object) -> bool:
    '''
    Used for multiprocessing. Loss function must be pickleable.

    @Params:
        x... an object

    @Returns:
        True if object can be pickled, False otherwise
    '''

    try:
        pickle.dumps(x)
        return True
    except (pickle.PicklingError, AttributeError):
        return False

def exhaustive_search(X:np.ndarray, n_outps: int, loss_fkt: callable, k: int, n_calc_nodes:int = 1, n_processes:int = 1, topk:int = 5, verbose:int = 0, opt_mode:str = 'grid', max_orders:int = 10000, max_size:int = np.inf, stop_thresh:float = -1.0, unique_loss:bool = True, **params) -> dict:
    '''
    Exhaustive search for a DAG.

    @Params:
        X... input for DAG, something that is accepted by loss fkt
        n_outps... number of outputs for DAG
        loss_fkt... function: X, cgraph, consts -> float
        k... number of constants
        n_calc_nodes... how many intermediate nodes at most?
        n_processes... number of processes for evaluation
        topk... we return top k found graphs
        verbose... print modus 0 = no print, 1 = status messages, 2 = progress bars
        opt_mode... method for optimizing constants, one of {pool, opt, grid, grid_opt}
        max_orders... will at most evaluate this many chosen orders
        max_size... will only return at most this many graphs (sorted by loss)
        stop_thresh... if loss is lower than this, will stop evaluation (only for single process)
        unique_loss... only take graph into topk if it has a totally new loss
    @Returns:
        dictionary with:
            graphs -> list of computational DAGs
            consts -> list of constants
            losses -> list of losses

    '''

    n_processes = max(min(n_processes, multiprocessing.cpu_count()), 1)
    ctx = multiprocessing.get_context('spawn')

    if n_processes > 1:
        error_msg = 'Loss function must be serializable with pickle for > 1 processes.\n'
        error_msg += 'See dag_search.MSE_loss_fkt for an example.\n'
        error_msg += 'If this worked before, consider reloading your loss funktion.'
        assert is_pickleable(loss_fkt), error_msg

    m = X.shape[1]
    n = n_outps

    # collect computational graphs (no operations on nodes yet)
    orders = get_build_orders(m, n, k, n_calc_nodes, max_orders = max_orders, verbose=verbose)

    if verbose > 0:
        print(f'Total orders: {len(orders)}')
        print('Evaluating orders')


    top_losses = []
    top_consts = []
    top_ops = []
    top_orders = []
    loss_thresh = np.inf

    early_stop = False
    if n_processes == 1:
        # sequential
        losses = []
        if verbose == 2:
            pbar = tqdm(orders)
        else:
            pbar = orders
        for order in pbar:
            consts, losses, ops = evaluate_build_order(order, m, n, k, X, loss_fkt, opt_mode = opt_mode)
            for c, loss, op in zip(consts, losses, ops):
                
                if loss <= loss_thresh:
                    if unique_loss:
                        valid = loss not in top_losses
                    else:
                        valid = True

                    if valid:
                        if len(top_losses) >= topk:
                            repl_idx = np.argmax(top_losses)
                            top_consts[repl_idx] = c
                            top_losses[repl_idx] = loss
                            top_ops[repl_idx] = op
                            top_orders[repl_idx] = order
                        else:
                            top_consts.append(c)
                            top_losses.append(loss)
                            top_ops.append(op)
                            top_orders.append(order)
                        
                        loss_thresh = np.max(top_losses)
                        if verbose == 2:
                            pbar.set_postfix({'best_loss' : np.min(top_losses)})
                if loss < stop_thresh:
                    early_stop = True
                    break
            if early_stop:
                break
    else:
        args = [[order, m, n, k, X, loss_fkt, opt_mode, stop_thresh] for order in orders]
        if verbose == 2:
            pbar = tqdm(args, total = len(args))
        else:
            pbar = args

        with ctx.Pool(processes=n_processes, initializer=init_process, initargs=(early_stop,)) as pool:
            pool_results = pool.starmap(evaluate_build_order, pbar)
        for i, (consts, losses, ops) in enumerate(pool_results):
            for c, loss, op in zip(consts, losses, ops):
                if loss <= loss_thresh:
                    if unique_loss:
                        valid = loss not in top_losses
                    else:
                        valid = True

                    if valid:
                        if len(top_losses) >= topk:
                            repl_idx = np.argmax(top_losses)
                            top_consts[repl_idx] = c
                            top_losses[repl_idx] = loss
                            top_ops[repl_idx] = op
                            top_orders[repl_idx] = orders[i]
                        else:
                            top_consts.append(c)
                            top_losses.append(loss)
                            top_ops.append(op)
                            top_orders.append(orders[i])
                        
                        loss_thresh = np.max(top_losses)
                        if verbose == 2:
                            pbar.set_postfix({'best_loss' : np.min(top_losses)})

    sort_idx = np.argsort(top_losses)
    top_losses = [top_losses[i] for i in sort_idx]
    top_consts = [top_consts[i] for i in sort_idx]
    top_orders = [top_orders[i] for i in sort_idx]
    top_ops = [top_ops[i] for i in sort_idx]
    top_graphs = []
    for order, ops in zip(top_orders, top_ops):
        cgraph = build_dag(order, ops, m, n, k)
        top_graphs.append(cgraph.copy())

    ret = {
        'graphs' : top_graphs,
        'consts' : top_consts,
        'losses' : top_losses}

    return ret

def sample_search(X:np.ndarray, n_outps: int, loss_fkt: callable, k: int, n_calc_nodes:int = 1, n_processes:int = 1, topk:int = 5, verbose:int = 0, opt_mode:str = 'grid', n_samples:int = int(1e4), stop_thresh:float = -1.0, unique_loss:bool = True, **params) -> dict:
    '''
    Sampling search for a DAG.

    @Params:
        X... input for DAG, something that is accepted by loss fkt
        n_outps... number of outputs for DAG
        loss_fkt... function: X, cgraph, consts -> float
        k... number of constants
        n_calc_nodes... how many intermediate nodes at most?
        n_processes... number of processes for evaluation
        topk... we return top k found graphs
        verbose... print modus 0 = no print, 1 = status messages, 2 = progress bars
        opt_mode... method for optimizing constants, one of {pool, opt, grid, grid_opt}
        n_samples... number of random graphs to check
        stop_thresh... if loss is lower than this, will stop evaluation (only for single process)
        unique_loss... only take graph into topk if it has a totally new loss
    @Returns:
        dictionary with:
            graphs -> list of computational DAGs
            consts -> list of constants
            losses -> list of losses
    '''
    n_processes = max(min(n_processes, multiprocessing.cpu_count()), 1)
    ctx = multiprocessing.get_context('spawn')

    if n_processes > 1:
        error_msg = 'Loss function must be serializable with pickle for > 1 processes.\n'
        error_msg += 'See dag_search.MSE_loss_fkt for an example.\n'
        error_msg += 'If this worked before, consider reloading your loss funktion.'
        assert is_pickleable(loss_fkt), error_msg

    m = X.shape[1]
    n = n_outps

    if verbose > 0:
        print('Generating graphs')
    if verbose == 2:
        pbar = tqdm(range(n_samples))
    else:
        pbar = range(n_samples)

    cgraphs = []
    for _ in pbar:
        cgraph = sample_graph(m, n, k, n_calc_nodes)
        cgraphs.append(cgraph.copy())

    if verbose > 0:
        print('Evaluating graphs')

    top_losses = []
    top_consts = []
    top_graphs = []
    loss_thresh = np.inf

    if n_processes == 1:
        # sequential
        if verbose == 2:
            pbar = tqdm(cgraphs)
        else:
            pbar = cgraphs
        for cgraph in pbar:
            c, loss = evaluate_cgraph(cgraph, X, loss_fkt, opt_mode)
            if loss <= loss_thresh:
                if unique_loss:
                    valid = loss not in top_losses
                else:
                    valid = True


                if valid:
                    if len(top_losses) >= topk:
                        repl_idx = np.argmax(top_losses)
                        top_consts[repl_idx] = c
                        top_losses[repl_idx] = loss
                        top_graphs[repl_idx] = cgraph.copy()
                    else:
                        top_consts.append(c)
                        top_losses.append(loss)
                        top_graphs.append(cgraph.copy())
                    
                    loss_thresh = np.max(top_losses)
                    if verbose == 2:
                        pbar.set_postfix({'best_loss' : np.min(top_losses)})

            if loss <= stop_thresh:
                break
    else:

        early_stop = False
        args = [[cgraph, X, loss_fkt, opt_mode, stop_thresh] for cgraph in cgraphs]
        if verbose == 2:
            pbar = tqdm(args, total = len(args))
        else:
            pbar = args
        with ctx.Pool(processes=n_processes, initializer=init_process, initargs=(early_stop,)) as pool:
            pool_results = pool.starmap(evaluate_cgraph, pbar)


        for i, (c, loss) in enumerate(pool_results):
            if loss <= loss_thresh:
                if unique_loss:
                    valid = loss not in top_losses
                else:
                    valid = True

                if valid:
                    if len(top_losses) >= topk:
                        repl_idx = np.argmax(top_losses)
                        top_consts[repl_idx] = c
                        top_losses[repl_idx] = loss
                        top_graphs[repl_idx] = cgraphs[i].copy()
                    else:
                        top_consts.append(c)
                        top_losses.append(loss)
                        top_graphs.append(cgraphs[i].copy())
                    
                    loss_thresh = np.max(top_losses)

    sort_idx = np.argsort(top_losses)
    top_graphs = [top_graphs[i] for i in sort_idx]
    top_consts = [top_consts[i] for i in sort_idx]
    top_losses = [top_losses[i] for i in sort_idx]
    
    ret = {
        'graphs' : top_graphs,
        'consts' : top_consts,
        'losses' : top_losses}

    return ret


########################
# Sklearn Interface
########################


class SDS(sklearn.base.BaseEstimator, sklearn.base.RegressorMixin):
    '''
    SDS: Symbolic DAG-Search

    Sklearn interface for exhaustive search.
    '''

    def __init__(self, k:int = 1, n_calc_nodes:int = 4, max_orders:int = int(1e5), random_state:int = None):

        self.k = k
        self.n_calc_nodes = n_calc_nodes
        self.max_orders = max_orders

        self.cgraph = None
        self.consts = None
        self.random_state = random_state

    def fit(self, X:np.ndarray, y:np.ndarray, processes:int = 1, verbose:int = 1):
        assert len(y.shape) == 1, f'y must be 1-dimensional (current shape: {y.shape})'

        if self.random_state is not None:
            np.random.seed(self.random_state)

        y_part = y.reshape(-1, 1)
        m = X.shape[1]
        n = 1
        loss_fkt = MSE_loss_fkt(y_part)
        params = {
            'X' : X,
            'n_outps' : n,
            'loss_fkt' : loss_fkt,
            'k' : self.k,
            'n_calc_nodes' : self.n_calc_nodes,
            'n_processes' : processes,
            'topk' : 1,
            'opt_mode' : 'grid_zoom',
            'verbose' : verbose,
            'max_orders' : self.max_orders, 
            'stop_thresh' : 1e-4
        }
        res = exhaustive_search(**params)
        if verbose > 0:
            print(f'Found graph with loss {res["losses"][0]}')
        self.cgraph = res['graphs'][0]
        self.consts = res['consts'][0]

    def predict(self, X):
        assert self.cgraph is not None, 'No graph found yet. Call .fit first!'
        pred = self.cgraph.evaluate(X, c = self.consts)
        return pred[:, 0]

    def model(self):
        assert self.cgraph is not None, 'No graph found yet. Call .fit first!'
        exprs = self.cgraph.evaluate_symbolic(c = self.consts)
        return exprs[0]

    def complexity(self):
        assert self.cgraph is not None, 'No graph found yet. Call .fit first!'
        return self.cgraph.n_operations()