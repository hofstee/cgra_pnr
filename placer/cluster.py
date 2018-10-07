from __future__ import division, print_function
import random
import math
import numpy as np
from .analytical import GlobalPlacer

from placer import Annealer
from placer.util import Box
from .util import compute_centroids, collapse_netlist,\
    compute_hpwl, manhattan_distance,\
    analyze_lanes


class SAClusterPlacer(Annealer):
    def __init__(self, clusters, netlists, fixed_pos, board_meta,
                 fold_reg=True, seed=0, debug=True):
        self.clusters = clusters
        self.fixed_pos = fixed_pos.copy()

        board_info = board_meta[-1]
        self.board_layout = board_meta[0]
        self.clb_type = board_info["clb_type"]
        self.clb_margin = board_info["margin"]
        self.height = board_info["height"]
        self.width = board_info["width"]
        self.board_type = board_info["arch_type"]
        self.center_of_board = (self.width / 2, self.height / 2)

        self.debug = debug

        self.overlap_factor = 1.0 / 4
        # energy control
        self.overlap_energy = 30
        self.legal_penalty = {"m": 30, "d": 200}
        if fold_reg:
            self.legal_ignore = {"r"}
        else:
            self.legal_ignore = set()

        rand = random.Random()
        rand.seed(seed)

        self.fold_reg = fold_reg

        self.block_lanes = analyze_lanes(self.clb_margin,
                                         self.board_layout)
        self.boxes = {}
        for cluster_id in clusters:
            self.boxes[cluster_id] = Box()

        self.netlists, self.intra_cluster_count = \
            collapse_netlist(clusters, netlists, fixed_pos)

        self.global_placer = GlobalPlacer(clusters, self.netlists, fixed_pos,
                                          board_meta,
                                          block_lanes=self.block_lanes,
                                          fold_reg=fold_reg)
        placement = self.global_placer.place()

        energy = self.__init_energy(placement)
        state = {"placement": placement, "energy": energy}
        self.init_energy = energy

        Annealer.__init__(self, initial_state=state, rand=rand)

        self.changes = 0
        self.has_changed = False

        # speed up move
        self.cluster_boxes = []
        for c_id in placement:
            self.cluster_boxes.append(placement[c_id])
        self.cluster_boxes.sort(key=lambda x: x.c_id)

        self.cluster_index = self.__build_box_netlist_index()
        self.moves = set()

        # low temperature annealing
        self.Tmax = 10
        self.Tmin = 0.1
        self.steps = 3000 * len(self.clusters)

    def __build_box_netlist_index(self):
        index = {}
        for net_id in self.netlists:
            for cluster_id in self.netlists[net_id]:
                c_id = int(cluster_id[1:])
                if c_id not in index:
                    index[c_id] = set()
                index[c_id].add(net_id)
        return index

    @staticmethod
    def __compute_overlap(a, b):
        dx = min(a.xmax, b.xmax) - max(a.xmin, b.xmin)
        dy = min(a.ymax, b.ymax) - max(a.ymin, b.ymin)
        if (dx >= 0) and (dy >= 0):
            return dx * dy
        else:
            return 0

    def __is_legal(self, box):
        # check two things
        # first, it's within the boundaries
        if box.xmin < self.clb_margin:
            return False
        if box.ymin < self.clb_margin:
            return False
        if box.xmax >= self.width - self.clb_margin:
            return False
        if box.ymax >= self.height - self.clb_margin:
            return False

        return True

    def __is_swap_legal(self, box1, box2):
        boxes = {box1, box2}
        for box in boxes:
            if not self.__is_legal(box):
                return False

        return True

    def __update_box(self, box, compute_special=True):
        # notice that this one doesn't check the legality
        x = box.xmin
        height = box.ymax - box.ymin
        required_width = int(math.ceil(box.total_clb_size / height))
        width = 0
        current_x = x
        while width < required_width:
            current_x += 1
            if current_x >= len(self.block_lanes):
                width += 1
            elif self.block_lanes[current_x] == self.clb_type:
                width += 1
        box.xmax = current_x
        if compute_special:
            # compute how many special blocks the cluster needed
            cluster = self.clusters[box.c_id]
            special_blocks = {}
            for blk_id in cluster:
                blk_type = blk_id[0]
                if blk_type != self.clb_type:
                    if blk_type not in special_blocks:
                        special_blocks[blk_type] = 0
                    special_blocks[blk_type] += 1
            box.special_blocks = special_blocks

    @staticmethod
    def compute_center(placement):
        result = {}
        for cluster_id in placement:
            box = placement[cluster_id]
            x = (box.xmax + box.xmin) / 2.0
            y = (box.ymax + box.ymin) / 2.0
            result[cluster_id] = (x, y)
        return result

    def move(self):
        self.moves = set()
        placement = self.state["placement"]

        if self.debug:
            reference_energy = self.__init_energy(placement)
            assert reference_energy == self.state["energy"]

        # we have three options here
        # 1 jump (only if steps are low)
        # 2, move
        # 3, swap
        # 4, change shape
        box = self.random.sample(self.cluster_boxes, 1)[0]
        new_box = Box.copy_box(box)

        dx = self.random.randrange(-1, 1 + 1)
        dy = self.random.randrange(-1, 1 + 1)

        new_box.xmin = box.xmin + dx
        new_box.ymin = box.ymin + dy
        new_box.ymax = box.ymax + dy

        self.__update_box(new_box, compute_special=False)
        # to see if it's legal
        if self.__is_legal(new_box):
            self.moves.add(new_box)

    def __compute_special_blocks(self, box):
        result = {}
        for blk_type in box.special_blocks:
            result[blk_type] = 0
            xmin = box.xmin
            xmax = box.xmax
            # lanes to compute
            lanes = set()
            for x in range(xmin, xmax + 1):
                if self.block_lanes[x] == blk_type:
                    lanes.add(x)
            # compute how many blocks are there
            for x in lanes:
                for y in range(box.ymin, box.ymax + 1):
                    if self.board_layout[y][x] == blk_type:
                        result[blk_type] += 1
        return result

    def __init_energy(self, placement):
        blk_pos = self.fixed_pos.copy()

        centers = self.compute_center(placement)
        for node_id in centers:
            c_id = "x" + str(node_id)
            blk_pos[c_id] = centers[node_id]
        hpwl = compute_hpwl(self.netlists, blk_pos)

        # add overlap
        overlap_area = self.__compute_total_overlap(placement)

        overlap_energy = overlap_area * self.overlap_energy
        hpwl += overlap_energy

        # add legalize energy
        legalize_energy = self.__compute_legal_energy(placement)
        hpwl += legalize_energy

        return hpwl

    def __compute_total_overlap(self, placement):
        overlap_area = 0
        for c_id in placement:
            box1 = placement[c_id]
            for c_id_next in placement:
                if c_id == c_id_next:
                    continue
                box2 = placement[c_id_next]
                overlap_area += self.__compute_overlap(box1, box2)
        return overlap_area

    def __compute_legal_energy(self, placement):
        legalize_energy = 0
        for c_id in placement:
            box = placement[c_id]
            blk_count = self.__compute_special_blocks(box)
            for blk_type in blk_count:
                if blk_type in self.legal_ignore:
                    continue
                remaining = blk_count[blk_type] - box.special_blocks[blk_type]
                if remaining < 0:
                    legalize_energy += abs(remaining) * \
                                       self.legal_penalty[blk_type]
        return legalize_energy

    def energy(self):
        """we use HPWL as the cost function"""
        if len(self.moves) == 0:
            return self.state["energy"]
        placement = self.state["placement"]
        energy = self.state["energy"]
        changed_nets = {}
        assert len(self.moves) == 1
        box = self.moves.pop()

        # first, compute the new HWPL
        changed_net_id = self.cluster_index[box.c_id]

        for net_id in changed_net_id:
            changed_nets[net_id] = self.netlists[net_id]

        blk_pos = self.fixed_pos.copy()
        centers = self.compute_center(placement)
        for node_id in centers:
            c_id = "x" + str(node_id)
            blk_pos[c_id] = centers[node_id]
        old_hpwl = compute_hpwl(changed_nets, blk_pos)

        old_overlap = 0
        for c_id_next in placement:
            if box.c_id == c_id_next:
                continue
            box2 = placement[c_id_next]
            old_overlap += self.__compute_overlap(box, box2)
        for c_id in placement:
            box1 = placement[c_id]
            if c_id == box.c_id:
                continue
            old_overlap += self.__compute_overlap(box1, box)

        old_legalize_energy = self.__compute_legal_energy({box.c_id: box})

        # compute the new energy
        # some implementation details:
        # 1. we temporarily override the placement and then restore it
        # 2. only compute the old/new energy for changed boxes

        new_placement = {box.c_id: box}
        placement[box.c_id] = box

        centers = self.compute_center(new_placement)

        node_id = box.c_id
        c_id = "x" + str(node_id)
        blk_pos[c_id] = centers[node_id]
        new_hpwl = compute_hpwl(changed_nets, blk_pos)
        # new_hpwl = compute_hpwl(self.netlists, blk_pos)

        new_overlap = 0
        c_id = box.c_id
        for c_id_next in placement:
            if c_id == c_id_next:
                continue
            box2 = placement[c_id_next]
            new_overlap += self.__compute_overlap(box, box2)
        for c_id in placement:
            box1 = placement[c_id]
            if c_id == box.c_id:
                continue
            new_overlap += self.__compute_overlap(box1, box)

        new_legalize_energy = 0

        blk_count = self.__compute_special_blocks(box)
        for blk_type in blk_count:
            if blk_type in self.legal_ignore:
                continue
            remaining = blk_count[blk_type] - box.special_blocks[blk_type]
            if remaining < 0:
                new_legalize_energy += abs(remaining) * \
                                       self.legal_penalty[blk_type]
        # new_legalize_energy = self.__compute_legal_energy(placement)
        # restore
        placement[c_id] = box

        hpwl_diff = new_hpwl - old_hpwl
        energy += hpwl_diff
        energy += (new_overlap - old_overlap) * self.overlap_energy
        energy += new_legalize_energy - old_legalize_energy

        return energy

    def commit_changes(self):
        for box in self.moves:
            self.state["placement"][box.c_id] = box
        if len(self.moves) > 0:
            self.changes += 1
            self.has_changed = True
        else:
            self.has_changed = False
        self.moves = set()

    def __is_cell_legal(self, pos, blk_type):
        x, y = pos
        if x < self.clb_margin or y < self.clb_margin:
            return False
        if x > self.width - self.clb_margin or \
           y > self.height - self.clb_margin:
            return False
        return self.board_layout[y][x] == blk_type

    def __get_exterior_set(self, cluster_id, current_cells, board,
                           max_dist=4, search_all=False):
        """board is a boolean map showing everything been taken, which doesn't
           care about overlap
        """
        # put it on the actual board so that we can do a brute-force search
        # so we need to offset with pos
        box = self.state["placement"][cluster_id]

        result = set()
        if search_all:
            x_min, x_max = self.clb_margin, len(board[0]) - self.clb_margin
            y_min, y_max = self.clb_margin, len(board) - self.clb_margin
        else:
            x_min, x_max = box.xmin - 1, box.xmax + 1
            y_min, y_max = box.ymin - 1, box.ymax + 1
        for y in range(y_min, y_max + 1):
            for x in range(x_min, x_max + 1):
                if (x, y) not in current_cells:
                    # make sure it's its own exterior
                    continue
                p = None
                # allow two manhattan distance jump
                # TODO: optimize this
                for i in range(-max_dist - 1, max_dist + 1):
                    for j in range(-max_dist - 1, max_dist + 1):
                        if abs(i) + abs(j) > max_dist:
                            continue
                        if not self.__is_cell_legal((x + j, y + i),
                                                    self.clb_type):
                            continue
                        if (not board[y + i][x + j]) and board[y][x]:
                            p = (x + j, y + i)
                        if (p is not None) and \
                                self.__is_cell_legal(p, self.clb_type):
                            result.add(p)
        for p in result:
            if board[p[1]][p[0]]:
                raise Exception("unknown error" + str(p))
        return result

    def __get_bboard(self, cluster_cells, check=True):
        bboard = np.zeros((self.height, self.width), dtype=np.bool)
        for cluster_id in cluster_cells:
            for blk_type in cluster_cells[cluster_id]:
                for x, y in cluster_cells[cluster_id][blk_type]:
                    if check:
                        assert(not bboard[y][x])
                    bboard[y][x] = True
        return bboard

    @staticmethod
    def __compute_overlap_cells(a, b):
        dx = min(a.xmax, b.xmax) - max(a.xmin, b.xmin)
        dy = min(a.ymax, b.ymax) - max(a.ymin, b.ymin)
        if (dx >= 0) and (dy >= 0):
            # brute force compute the overlaps
            a_pos, b_pos = set(), set()
            for y in range(a.ymin, a.ymax + 1):
                for x in range(a.xmin, a.xmax + 1):
                    a_pos.add((x, y))
            for y in range(b.ymin, b.ymax + 1):
                for x in range(b.xmin, b.xmax + 1):
                    b_pos.add((x, y))
            result = a_pos.intersection(b_pos)
            return result
        else:
            return set()

    def realize(self):
        # the idea is to pull every cell positions to the center of the board
        used_special_blocks_pos = set()
        cluster_cells = {}
        placement = self.state["placement"]
        improvement = (self.init_energy - self.state["energy"]) /\
            self.init_energy * 100
        print("Total moves:", self.changes, "improvement:",
              "{:.2f}".format(improvement))
        # first assign special blocks
        for c_id in self.clusters:
            cluster = self.clusters[c_id]
            box = placement[c_id]
            cluster_special_blocks = \
                self.assign_special_blocks(cluster, box,
                                           used_special_blocks_pos)
            cluster_cells[c_id] = cluster_special_blocks

        overlaps = {}
        bboard = self.__get_bboard(cluster_cells, False)
        for cluster_id1 in cluster_cells:
            box1 = placement[cluster_id1]
            overlaps[cluster_id1] = set()
            for cluster_id2 in cluster_cells:
                if cluster_id1 == cluster_id2:
                    continue
                box2 = placement[cluster_id2]
                overlaps[cluster_id1].update(self.__compute_overlap_cells(box1,
                                                                          box2))

        # resolve overlapping from the most overlapped region
        cluster_ids = list(overlaps.keys())
        cluster_ids.sort(key=lambda entry: len(overlaps[entry]) /
                         float(placement[entry].total_clb_size),
                         reverse=True)

        for c_id in cluster_ids:
            # assign non-overlap cells
            box = placement[c_id]
            cluster_overlap_cells = overlaps[c_id]
            cluster_cells[c_id][self.clb_type] = set()
            for y in range(box.ymin, box.ymax + 1):
                for x in range(box.xmin, box.xmax + 1):
                    pos = (x, y)
                    if bboard[y][x] or pos in cluster_overlap_cells or \
                            self.board_layout[y][x] != self.clb_type:
                        continue
                    cluster_cells[c_id][self.clb_type].add(pos)
                    bboard[y][x] = True
            self.de_overlap(cluster_cells[c_id][self.clb_type],
                            bboard, c_id)

        # return centroids as well
        centroids = compute_centroids(cluster_cells, b_type=self.clb_type)

        return cluster_cells, centroids

    def assign_special_blocks(self, cluster, box, used_spots):
        special_blks = {}
        cells = {}
        for blk_id in cluster:
            blk_type = blk_id[0]
            if blk_type != self.clb_type and blk_type != "r" and \
                    blk_type != "i":
                if blk_type not in special_blks:
                    special_blks[blk_type] = 0
                special_blks[blk_type] += 1

        pos_x, pos_y = box.xmin, box.ymin
        width, height = box.xmax - box.xmin, box.ymax - box.ymin
        centroid = pos_x + width / 2.0, pos_y + height / 2.0
        for x in range(pos_x, pos_x + width):
            for y in range(pos_y, pos_y + width):
                blk_type = self.board_layout[y][x]
                pos = (x, y)
                if blk_type in special_blks and pos not in used_spots:
                    # we found one
                    if blk_type not in cells:
                        cells[blk_type] = set()
                    cells[blk_type].add(pos)
                    used_spots.add(pos)
                    if special_blks[blk_type] > 0:
                        special_blks[blk_type] -= 1

        # here is the difficult part. if we still have blocks left to assign,
        # we need to do an brute force search
        available_pos = {}
        for blk_type in special_blks:
            available_pos[blk_type] = []
        for y in range(len(self.board_layout)):
            for x in range(len(self.board_layout[y])):
                pos = (x, y)
                blk_type = self.board_layout[y][x]
                if pos not in used_spots and blk_type in special_blks:
                    available_pos[blk_type].append(pos)
        for blk_type in special_blks:
            num_blocks = special_blks[blk_type]
            pos_list = available_pos[blk_type]
            if len(pos_list) < num_blocks:
                raise Exception("Not enough blocks left for type: " + blk_type)
            pos_list.sort(key=lambda p: manhattan_distance(p, centroid))
            for i in range(num_blocks):
                if blk_type not in cells:
                    cells[blk_type] = set()
                cells[blk_type].add(pos_list[i])
                used_spots.add(pos_list[i])

        return cells

    def de_overlap(self, current_cell, bboard, cluster_id):
        effort_count = 0

        needed = len([x for x in self.clusters[cluster_id]
                      if x[0] == self.clb_type])
        old_needed = needed
        cells_have = 0
        for x, y in current_cell:
            if self.board_layout[y][x] == self.clb_type:
                cells_have += 1
        while cells_have < needed and effort_count < 5:
            # boolean board
            ext = self.__get_exterior_set(cluster_id, current_cell, bboard,
                                          max_dist=(effort_count + 1) * 2)
            ext_list = list(ext)
            ext_list.sort(key=lambda p: manhattan_distance(p,
                                                           self.center_of_board
                                                           ))
            for ex in ext_list:
                x, y = ex
                if self.board_layout[y][x] == self.clb_type:
                    current_cell.add(ex)
                    assert not bboard[y][x]
                    bboard[y][x] = True
                    cells_have += 1
                if cells_have > needed:
                    break
            if old_needed == needed:
                effort_count += 1
            else:
                effort_count = 0
            old_needed = needed
        assert (cells_have >= needed)