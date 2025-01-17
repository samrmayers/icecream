import math
import numpy as np
import copy
import logging
from typing import Callable, Dict, List, Tuple, Union

# Key for the number of units served in the current turn
CURRENT_TURN_SERVED = "current_turn_served"


class ScoopCandidate:
    def __init__(self, loc: (int, int)):
        """

        :param loc: Tuple (x,y) location of the scoop
        :param flavor_counts: Array with the number of scoops for each flavor
        """
        self._x, self._y = loc
        self._flavors = []
        # Bookkeeping size of the scoop. Includes second-level units
        self._size = 0
        # Actual number of units at the top level
        self._actual_size = 0
        self._unknown_flavor_count = 0

    def add_flavor(self, flavor: int, first_level=True):
        size_diff = (flavor + 1) - len(self._flavors)
        if size_diff > 0:
            # Extend array
            self._flavors.extend([0] * size_diff)
        self._flavors[flavor] += 1
        self._size += 1
        if first_level:
            self._actual_size += 1

    def add_unknown(self):
        self._unknown_flavor_count += 1
        self._size += 1

    def get_flavor_count(self, flavor: int):
        if flavor < 0 or flavor >= len(self._flavors):
            return 0
        return self._flavors[flavor]

    def __lt__(self, other):
        return self.size < other.size

    @property
    def size(self):
        return self._size

    @property
    def actual_size(self):
        return self._actual_size

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def loc(self):
        return self._x, self._y

    def flavors(self) -> Dict[int, int]:
        result = {}
        for (flavor, count) in enumerate(self._flavors):
            if count > 0:
                result[flavor] = count

        if self._unknown_flavor_count > 0:
            result['unknown'] = self._unknown_flavor_count

        return result

    def score(self, flavor_preference: List[int]):
        """
        Scores the candidate cube
        :param flavor_preference: List of flavors where the leftmost flavor is most preferred
        :return:
        """

        score = 0
        for ix, flavor in enumerate(flavor_preference):
            score += (len(flavor_preference) - ix) * self.get_flavor_count(flavor)
        score += (self._unknown_flavor_count * len(flavor_preference) / 2)
        return score


class Player:
    def __init__(self, flavor_preference: List[int], rng: np.random.Generator, logger: logging.Logger) -> None:
        """Initialise the player with given preference.

        Args:
            flavor_preference (List[int]): flavor preference, most flavored
                flavor is first element in the list and last element is least
                preferred flavor

            rng (np.random.Generator): numpy random number
                generator, use this for same player behavior across run

            logger (logging.Logger): logger use this like logger.info("message")
        """
        self.flavor_preference = flavor_preference
        self.rng = rng
        self.logger = logger
        self.state = {
            'current_served': None,
            # Number of scoops served to ourselves in the current turn
            'current_turn_served': 0
        }

    @staticmethod
    def scoop_value(flavor_preference, top_layer, curr_level, x, y):
        """Helper function: returns the value the player gets for a scoop at index x,y"""
        d = max(curr_level[x, y], curr_level[x+1, y], curr_level[x, y+1], curr_level[x+1, y+1])
        try:
            if d >= 0:
                units = 0
                flav_total = 0
                if curr_level[x+1, y] == d:
                    flav_total += len(flavor_preference) - flavor_preference.index(top_layer[x,y]) + 1
                    units += 1
                if curr_level[x+1, y] == d:
                    flav_total += len(flavor_preference) - flavor_preference.index(top_layer[x+1,y]) + 1
                    units += 1
                if curr_level[x, y+1] == d:
                    flav_total += len(flavor_preference) - flavor_preference.index(top_layer[x,y+1]) + 1
                    units += 1
                if curr_level[x+1, y+1] == d:
                    flav_total += len(flavor_preference) - flavor_preference.index(top_layer[x+1,y+1]) + 1
                    units += 1
                return (flav_total, (x, y), units)
        except ValueError:
            # No knowledge of player's preference for some value
            pass
        return (0, (x,y), 0)

    @staticmethod
    def score_available_scoops(flavor_preference, top_layer, curr_level):
        p_queue_1 = []
        p_queue_2 = []
        p_queue_3 = []
        p_queue_4 = []
        # Subtract one from length since 2x2 "spoon" must remain in container
        for x in range(0, top_layer.shape[0]-1):
            for y in range(0, top_layer.shape[1]-1):
                scoop = Player.scoop_value(flavor_preference, top_layer, curr_level, x, y)
                if scoop[2] == 1:
                    p_queue_1.append(scoop)
                elif scoop[2] == 2:
                    p_queue_2.append(scoop)
                elif scoop[2] == 3:
                    p_queue_3.append(scoop)
                elif scoop[2] == 4:
                    p_queue_4.append(scoop)
        # TODO (etm): If we care, we can use an actual heap / priority queue
        p_queue_1.sort()
        p_queue_2.sort()
        p_queue_3.sort()
        p_queue_4.sort()
        return p_queue_4, p_queue_1, p_queue_2, p_queue_3

    @staticmethod
    def no_overlap(x1, y1, x2, y2):
        if abs(x1 - x2) <= 1 and abs(y1 - y2) <= 1:
            return False
        return True

    def serve(self, top_layer: np.ndarray, curr_level: np.ndarray, player_idx: int,
          get_flavors: Callable[[], List[int]],
          get_player_count: Callable[[], int], get_served: Callable[[], List[Dict[int, int]]],
          get_turns_received: Callable[[], List[int]]) -> Dict[str, Union[Tuple[int], int]]:
        """Request what to scoop or whom to pass in the given step of the turn.
        In each turn the simulator calls this serve function multiple times for
        each step for a single player, until the player has scooped 24 units of
        ice-cream or asked to pass to next player or made an invalid request.
        If you have scooped 24 units of ice-cream in a turn then you get one
        last step in that turn where you can specify to pass to a player.

        Args:
            top_layer (np.ndarray): Numpy 2d array of size (24, 15) containing
                flavor at each cell location
            
            curr_level (np.ndarray): Numpy 2d array of size (24, 15) containing
                current level at each cell location from 8 to 0, where 8 is highest
                level at start and 0 means no icecream left at this level
            
            player_idx (int): index of your player, 0-indexed

            get_flavors (Callable[[], List[int]]): method which returns a list of all
                possible flavors

            get_player_count (Callable[[], int]): method which returns number of total players

            get_served (Callable[[], List[Dict[int, int]]]): method which returns a list of dictionaries
                corresponding to each player, each dictionary at index i tells how
                units of a flavor are present in the bowl of the player with index
                i. E.g. lets say the fourth element is {1: 0, 2: 8...} means the
                corresponding player with index 4 has 0 units of flavor 1 and 8
                units of flavor

            get_turns_received (Callable[[], List[int]]):
                method which returns a list of integers corresponding to each
                player, each element at index i tells how many turns a player with
                index i has played so far.

        Returns:
            Dict[str, Union[Tuple[int],int]]: Return a dictionary specifying
            what action to take in the next step.

            2 possible return values
            {"action": "scoop",  "values" : (i,j)}
            stating to scoop the 4 cells with index (i,j), (i+1,j), (i,j+1), (i+1,j+1)

            {"action": "pass", "values" : i} pass to next player with index i
        """
        # if there is still more ice-cream to take, make a scoop
        if self.state['current_turn_served'] < 24:
            action = "scoop"
            _, scoop = self.choose_best_scoop_combination(top_layer, curr_level).pop()
            self.state['current_turn_served'] += scoop.actual_size
            values = scoop.loc
        else:
            self.state['current_turn_served'] = 0
            next_player = Player.best_player_to_pass_to(player_idx, get_player_count(), top_layer, curr_level, get_served, get_flavors, get_turns_received())
            action = "pass"
            values = next_player

        # update current served
        self.state['current_served'] = get_served()

        return {"action": action, "values": values}

    @staticmethod
    def best_player_to_pass_to(self_ix, n_players, top_layer, curr_level, get_served, get_flavors, turns_received):
        # list of all players except ourself and those who have had more turns than the current min
        players = []
        min_received = np.amin(turns_received)
        for i in range(n_players):
            if i != self_ix and turns_received[i] == min_received:
                players.append(i)

        max_player = players[0]
        max_score = 0
        for player in players:
            p_score = 0
            player_pref = Player.guess_player_pref_from_bowl(player, get_served(), get_flavors())
            # TODO (etm):
            #   This is a crude approximation since some scoops will contain chunks of
            #   other scoops. We need a better way to update the game state
            p_queue = Player.score_available_scoops(player_pref, top_layer, curr_level)[0]
            for _ in range(24):
                if len(p_queue) == 0:
                    break
                score, _, _ = p_queue.pop()
                p_score += score
            if p_score > max_score:
                max_score = p_score
                max_player = player

        return max_player

    @staticmethod
    def guess_player_pref_from_bowl(player, current_served, flavors):
        """
        Sort the contents of player's current_served bowl and generate a preference ranking accordingly.
        :param player: target player.
        :param current_served: player's bowl.
        :param flavors: all flavors.
        :return: a list of flavor rankings. Preferred flavors are ranked toward the front.
        """
        bowl = current_served[player]
        flavors_in_bowl, amounts_in_bowl = [], []
        for key in bowl.keys():
            if bowl[key] > 0:
                flavors_in_bowl.append(key)
                amounts_in_bowl.append(bowl[key])
        amount_indices = np.argsort(amounts_in_bowl)[::-1]
        pref_ranking = [flavors_in_bowl[i] for i in amount_indices]

        # TODO what if some flavor(s) didn't appear in the bowl? Do we pad the ranking list at the end?
        pref_ranking += [0] * (len(flavors) - len(pref_ranking))

        return pref_ranking

    def guess_player_dislikes_from_choice(self, player, added_flavors, top_layer_before_serving):
        """
        Guess the flavors that player probably dislikes based on the top_layer situation before they serve themselves,
        and what flavors they end up choosing. Can only be used when we pass the container directly to player.
        :param player: the player to be guessed on.
        :param added_flavors: output of diff_served().
        :param top_layer_before_serving: visible flavors on the top layer when we pass the container to player.
        :return: a list of flavors that the player probably doesn't like. Currently the strategy is to choose flavors
                 that are on top_layer but the player doesn't end up choosing.
        TODO: this function needs to be called one turn after we pass the container to player, which is impossible.
              Currently it's not being used but we may use the idea later.
        """
        if len(added_flavors[player]) == 0:
            print("WARNING: No added flavor info for this player!")
            return None
        dislikes = []
        top_layer_summed = self.sum_top_layer(top_layer_before_serving)
        top_layer_flavors = top_layer_summed.keys()
        for flavor in top_layer_flavors:
            if int(flavor) not in added_flavors[player]:
                dislikes.append(flavor)

        return dislikes

    @staticmethod
    def is_valid_next_player(turns_received, next_player):
        """
        Checks if next_player is a valid choice for passing the container to for this turn.
        :param turns_received: output of get_turns_received().
        :param next_player: player in question.
        :return: True if next_player is a valid choice.
        """
        if np.amin(turns_received) < turns_received[next_player]:
            return False
        return True

    @staticmethod
    def diff_served(new_served, current_served=None):
        """
        Compare new_served against current_served and output changes in the container.
        :param new_served: new container situation.
        :param current_served: current container situation. None if new_served is from the first turn.
        :return: changed_flavors[player_i] contains a list of flavors that player_i has newly added to
                 their bowl since current_served.
        TODO: Take the amount into consideration?
        """
        changed_flavors = []

        for p_id in range(len(new_served)):
            changed_flavors.append([])
            for flavor in new_served[p_id].keys():
                if current_served:
                    if new_served[p_id][flavor] > current_served[p_id][flavor]:
                        changed_flavors[p_id].append(flavor)
                else:
                    if new_served[p_id][flavor] > 0:
                        changed_flavors[p_id].append(flavor)

        return changed_flavors

    @staticmethod
    def sum_top_layer(top_layer):
        """
        Summarize top_layer information by flavor.
        :param top_layer: visible flavors.
        :return: top_layer_summed['flavor_i'] is the amount of flavor_i currently visible on top_layer.
        TODO: come up with other ways to convert the top layer flavor info into useful information.
        """
        top_layer_summed = {}
        for i in range(np.shape(top_layer)[0]):
            for j in range(np.shape(top_layer)[1]):
                if str(top_layer[i][j]) not in top_layer_summed:
                    top_layer_summed[str(top_layer[i][j])] = 0
                top_layer_summed[str(top_layer[i][j])] += 1

        return top_layer_summed

    @staticmethod
    def evaluate_partial_scoop(top_layer: np.ndarray, curr_level: np.ndarray):
        pass

    @staticmethod
    def iterate_coordinates(top_layer: np.ndarray):
        """ Generator yielding the upper left (x,y) coordinates of all possible 4x4 scoops"""
        for x in range(0, top_layer.shape[0]-1):
            for y in range(0, top_layer.shape[1]-1):
                yield x, y

    @staticmethod
    def scoop_unit_coordinates(loc: (int, int)) -> List[Tuple[int, int]]:
        """Coordinates for all units that could be involved in a 2x2 scoop"""
        x, y = loc
        return [(x, y), (x+1, y), (x, y+1), (x+1, y+1)]

    @staticmethod
    def iterate_scoops(top_layer: np.ndarray, curr_level: np.ndarray, single_level_only=False):
        """Generator yielding all possible single and double level scoops"""
        for scoop_coords in Player.iterate_coordinates(top_layer):
            top_level = max(map(lambda x: curr_level[x[0], x[1]], Player.scoop_unit_coordinates(scoop_coords)))
            single_level_scoop = ScoopCandidate(scoop_coords)
            second_level_present = False
            if top_level == -1:
                continue
            for x, y in Player.scoop_unit_coordinates(scoop_coords):
                if curr_level[x, y] == top_level:
                    single_level_scoop.add_flavor(top_layer[x, y])
                elif curr_level[x, y] > -1:
                    second_level_present = True
            if single_level_scoop.size > 0:
                yield single_level_scoop

            # Get the two-level scoop, if one exists
            if not second_level_present or single_level_only:
                continue

            second_level_scoop = ScoopCandidate(scoop_coords)
            for x, y in Player.scoop_unit_coordinates(scoop_coords):
                on_top = curr_level[x, y] == top_level
                if on_top:
                    second_level_scoop.add_unknown()
                if curr_level[x, y] >= top_level - 1:
                    second_level_scoop.add_flavor(top_layer[x, y], on_top)
            if second_level_scoop.size > 0:
                yield second_level_scoop

    def rank_scoops(self, top_layer, curr_level):
        queues = {}
        for scoop in self.iterate_scoops(top_layer, curr_level):
            q = queues.get(scoop.size)
            if q is None:
                queues[scoop.size] = []
            queues[scoop.size].append((scoop.score(self.flavor_preference), scoop))

        for q in queues.values():
            q.sort()

        return queues

    def choose_best_scoop_combination(self, top_layer, curr_level):
        total_needed = 24 - self.state[CURRENT_TURN_SERVED]
        if total_needed == 0:
            return None

        queues = self.rank_scoops(top_layer, curr_level)
        partial_needed = total_needed % 4
        if partial_needed == 0:
            needed = 4
        else:
            needed = partial_needed
        needed = min(needed, total_needed)

        scoop_choices = []
        # This loop is necessary because it's possible to get singe-unit columns
        # tall enough that looking 2 deep is not able to satisfy the number of needed
        # units. For example, if there's a tall 1x1 column and you need 4 units.
        # To account for this edge case, we decrease the needed scoops by 1 on each
        # loop repetition until some choices are returned.
        # You can trigger a test case for this issue with rng seed 2003
        while len(scoop_choices) == 0 and needed > 0:
            scoop_choices.extend(Player.build_scoop_recursive([], set(), needed, queues.copy()))
            if needed + 4 <= total_needed:
                scoop_choices.extend(Player.build_scoop_recursive([], set(), needed + 4, queues.copy()))
            needed -= 1

        def sort_fn(item):
            score, size, _ = item
            return score / size
        scoop_choices.sort(key=sort_fn)

        # Time to unpack some tuples
        _, _, scoop = scoop_choices.pop()
        return scoop

    @staticmethod
    def build_scoop_recursive(acc, exclude, units_needed, scoop_queues):
        """
        Returns permutations of scoops adding to `units_needed`
        Since it returns permutations, some choices will be duplicated

        :param acc: Accumulator list
        :param exclude: Set of coordinates to exclude (already included in some scoop)
        :param units_needed: Remaining units needed
        :param scoop_queues: Remaining scoops that can be used. Object is modified, so pass a copy if needed
        :return: [(score, `acc`)] where `acc` is sorted in reverse by avg unit value
        such that the highest valued individual choice is at index 0
        """
        # NB: there's a lot of shallow copying necessary to make this work.. would be
        # better in a functional language. Maybe there's a more imperative way to do this...

        if units_needed == 0:
            score = 0
            size = 0
            for sz, scoop in acc:
                score += sz
                size += scoop.size

            def sort_fn(item):
                # score, scoop
                sc, sp = item
                return sc / sp.size

            # Sort in reverse order by per-unit score
            acc.sort(key=sort_fn, reverse=True)
            return [(score, size, acc)]

        result = []
        for size in range(1, units_needed + 1):
            q = scoop_queues.get(size)
            if q is None:
                continue
            # Create a shallow copy of q
            # This is necessary so that we don't modify if for subsequent use by the caller.
            q = q[:]

            candidate = None
            # TODO: The calculation of overlapping coordinates is not level aware
            # Loop until we find a scoop that doesn't overlap a previous one, or run out
            while len(q) > 0:
                tmp_score, tmp_scoop = q.pop()
                if len(exclude & set(Player.scoop_unit_coordinates(tmp_scoop.loc))) == 0:
                    # Push this back on so it can be used in subsequent iterations
                    # It will be popped from a copy below before we recurse deeper
                    candidate = (tmp_score, tmp_scoop)
                    q.append(candidate)
                    break

            # Anything we popped off can't be used in subsequent iterations either,
            # so assign q back. Remember, `scoop_queues` should have been passed as
            # a shallow copy, so modifying this dict won't modify the caller's.
            scoop_queues[size] = q
            if candidate is None:
                continue

            # Shallow copy of the dictionary
            new_queues = scoop_queues.copy()
            # Shallow copy of the queue we're about to modify (q)
            new_queues[size] = new_queues[size][:]
            # Pop off the scoop we just used at this level of the recursion
            new_queues[size].pop()

            # Set up state for next recursive call
            new_acc = acc.copy()
            new_acc.append(candidate)

            new_exclude = exclude | set(Player.scoop_unit_coordinates(candidate[1].loc))

            result.extend(Player.build_scoop_recursive(new_acc, new_exclude, units_needed - size, new_queues))

        return result


# Run these tests with pytest
def test_scoop_iterator():
    """Tests basic scoop iterator function"""
    top_layer = np.array([[2, 2], [1, 1]])
    curr_level = np.array([[2, 2], [1, 1]])
    scoops = list(Player.iterate_scoops(top_layer, curr_level))
    assert len(scoops) == 2
    first = scoops[0]
    second = scoops[1]
    assert first.size == 2
    assert second.size == 6

    first_flavors = first.flavors()
    assert first_flavors.get(2) == 2

    second_flavors = second.flavors()
    assert second_flavors.get(2) == 2
    assert second_flavors.get(1) == 2
    assert second_flavors.get("unknown") == 2


def test_scoop_generation_and_scoring():
    """Tests scoop generation and scoring"""
    # Check scoring function with a test grid
    top_layer = np.array([[1, 2, 3, 4], [1, 2, 3, 4]])
    curr_level = np.array([[2, 2, 2, 3], [2, 1, 1, 2]])

    player = Player([4, 3, 2, 1], np.random.default_rng(2021), logging.Logger("default"))
    queues = player.rank_scoops(top_layer, curr_level)
    assert list(sorted(queues.keys())) == [1, 2, 3, 4, 6, 7]

    q1 = queues.get(1)
    score, scoop = q1[0]
    assert score == 4
    assert scoop.size == 1
    assert scoop.flavors().get(4) == 1

    q2 = queues.get(2)
    score, scoop = q2[0]
    assert score == 5
    assert scoop.size == 2
    assert scoop.flavors().get(3) == 1
    assert scoop.flavors().get(2) == 1

    q3 = queues.get(3)
    score, scoop = q3[0]
    assert score == 4
    assert scoop.size == 3
    assert scoop.flavors().get(2) == 1
    assert scoop.flavors().get(1) == 2

    q4 = queues.get(4)
    score, scoop = q4[0]
    assert score == 3 + 4 + 4 + 2  # last is unknown
    assert scoop.size == 4
    assert scoop.flavors().get(4) == 2
    assert scoop.flavors().get(3) == 1
    assert scoop.flavors().get('unknown') == 1

    q6 = queues.get(6)
    score, scoop = q6[0]
    assert score == 10 + 4  # last 4 is unknowns
    assert scoop.size == 6
    assert scoop.flavors().get(3) == 2
    assert scoop.flavors().get(2) == 2
    assert scoop.flavors().get('unknown') == 2

    q7 = queues.get(7)
    score, scoop = q7[0]
    assert score == 6 + 6  # last 6 is unknowns
    assert scoop.flavors().get(1) == 2
    assert scoop.flavors().get(2) == 2
    assert scoop.flavors().get('unknown') == 3

    _, chosen_scoop = player.choose_best_scoop_combination(top_layer, curr_level)[0]
    sc = chosen_scoop.score(player.flavor_preference)
    assert sc == 13
    assert chosen_scoop.actual_size == 1
