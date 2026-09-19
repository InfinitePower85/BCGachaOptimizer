"""
Objective: Given a seed track and a list of units that are desired, whats the maximum we can get within say 100 rolls, whats the price of it, and what rolls to do? 

At each step, you have two possible operations:

1. Roll once and stay on the same track
2. Roll 11 draw, get an uber, and switch tracks 

Inputs: 
Track1 array, Track2 array, Uber track 1, Uber track 2, maximum rolls, units_to_get set 



"""


# hmmm, might have to rework this, because when you go to 11 b, the next one you spin WILL be 11b, not the one after it. 
# so I guess this means you don't skip any? 
# well technically you skip 11a but... 
# also we have to implement track switches... 

def calculate_optimal_track(track1, track2, ubertrack1, ubertrack2, max_rolls, target_units):
    """
    Calculate optimal roll sequence to get the ubers. 
    
    """
    comb_track = [track1, track2]
    comb_utrack = [ubertrack1, ubertrack2]

    def op_1(idx, curr_track):
        """
        Needs to return newidx, newtrack, and characters collected 
        """
        new_characters = []
        new_characters.append(comb_track[curr_track][idx])
        return idx + 1, curr_track, new_characters
        
    def op_2(idx, curr_track):
        """
        The switching track and rolling 10 operation but incrementing idx by 11
        """
        if idx + 9 > max_rolls: return -1, -1, -1 # goes out of bounds. We have to test this tho, might be idx + 10... 
        new_characters = []
        for i in range(10):
            new_characters.append(comb_track[curr_track][idx + i])
        new_characters.append(comb_utrack[idx]) # add the uber lol 
        return idx + 11, 1 ^ curr_track, new_characters
    opt_path = []
    mx = -1 
    def recurse(idx, curr_track, curr_path):
        if idx >= max_rolls: # evaluate this current path 
            global opt_path
            global mx 
            # evaluate path up to here ig 
            res = 0
            for character in set(curr_path):
                if character in target_units:
                    res += 1
            if res > mx:
                opt_path = curr_path
                mx = res
            return 

        newidx, newtrack, newchars = op_1(idx, curr_track)
        if newidx != -1: 
            old_len = len(curr_path)
            curr_path.extend(newchars)
            recurse(newidx, newtrack, curr_path)
            curr_path = curr_path[:old_len]
        
        newidx, newtrack, newchars = op_2(idx, curr_track)
        if newidx != -1: 
            old_len = len(curr_path)
            curr_path.extend(newchars)
            recurse(newidx, newtrack, curr_path)
            curr_path = curr_path[:old_len]


def main():

    pass 

if __name__ == "__main__":
    main()