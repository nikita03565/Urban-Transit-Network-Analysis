from parser import Parser
import shapely
from shapely.ops import transform
import osmnx as ox
import geopandas as gpd
import pathlib
import os


import networkx as nx
import numpy as np
from shapely.geometry import LineString, Point
import math
from scipy.spatial import cKDTree
import matplotlib.pyplot as plt

import numpy.typing as npt

from scipy.spatial import cKDTree
from shapely.strtree import STRtree

from shapely.geometry import LineString
import math

from pyproj import Proj, Transformer
from shapely import Polygon
from shapely.ops import transform


mercator = "EPSG:3857"
wgs = "EPSG:4326"
mercator_proj = Proj(mercator)
wgs_proj = Proj(wgs)

wgs_to_mercator = Transformer.from_proj(wgs_proj, mercator_proj, always_xy=True)
mercator_to_wgs = Transformer.from_proj(mercator_proj, wgs_proj)


def convert_latlon(coords):
    transformer = wgs_to_mercator
    result = transformer.transform(coords["lon"], coords["lat"])
    return result


def convert_mercator(coords):
    transformer = mercator_to_wgs
    result = transformer.transform(coords["x"], coords["y"])
    return result


def calc_angle_dot_product(a: npt.NDArray, b: npt.NDArray) -> float:
    """
    Calculate the angle between two vectors using the dot product formula.
    """
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    return np.dot(a, b)


def snap_to_edges(edg, r_tree, route_series, buf_tol=20):
    matched_edges = []
    for i in range(len(route_series) - 1):
        pt1 = route_series.iloc[i]
        pt2 = route_series.iloc[i + 1]
        route_part = LineString([pt1, pt2])
        match = snap_point_to_best_edge(edg, r_tree, route_part, buf_tol)
        if match is not None:
            matched_edges.append(match.name)
    return matched_edges


def snap_point_to_best_edge(gr_edges, idxtree, route_part, distance_tol=20):

    edge_idx = idxtree.query(route_part.buffer(distance_tol))
    edge_candidates = gr_edges.iloc[edge_idx].index.to_numpy()
    # Pick the best matching edge
    best_match = None

    for u, v, key in edge_candidates:
        edge = gr_edges.loc[(u, v, key)]
        edge_geom = edge["geometry"]
        edge_geom = LineString(edge_geom)

        # calc angle bween route and edge as angle between two vectors using dot product
        dot_prod = calc_angle_dot_product(
            np.array(route_part.coords[0]) - np.array(route_part.coords[1]),
            np.array(edge_geom.coords[0]) - np.array(edge_geom.coords[1]),
        )
        # find best dot product
        if best_match is None or dot_prod > best_match[0]:
            best_match = (dot_prod, edge)

    if not best_match:
        return None
    if best_match[0] < 0.5:
        return None
    return best_match[1]


def filter_edges(ed, mat_edg):
    matched_edges_arr = np.array(mat_edg)
    _, un_idx = np.unique(matched_edges_arr, axis=0, return_index=True)
    filtered_matched_edges_arr = matched_edges_arr[np.sort(un_idx)]
    filtered_idx = filtered_matched_edges_arr.tolist()
    filtered_edges = ed.loc[filtered_idx]
    return filtered_edges


def build_final_path(gr, f_edges):
    final_path = [f_edges.iloc[0].name[0], f_edges.iloc[0].name[1]]  # start with the first edge
    for u, v, _ in f_edges.index[1:]:
        try:
            # final_path[-1] to u edge does not exist
            if final_path[-1] != u:
                if gr.has_edge(final_path[-1], u):
                    final_path.append(u)
                    final_path.append(v)
                    continue
                segment = nx.shortest_path(gr, source=final_path[-1], target=u, weight="length")
                final_path.extend(segment[1:])
                final_path.append(v)
        except nx.NetworkXNoPath:
            continue
    return final_path
