"""
path_finder

optimal path finder for battle cats street fighters
for some reason this is very slow, 3209 tests takes too long 

todo: investigate why the table name isn't going through for 11 draws. 
get rid of arrows in the names, do a data cleanse on juridraw and cammydraw. 

also doing 1 more also takes longer and does 4200 trials
maybe its because we're indexing into the dataframes poorly 
"""

import pandas as pd 
from functools import cache

cammy = pd.read_csv("Cammy_Draw.csv")
cammy.name = "cammy"
juri = pd.read_csv("Juri_Draw.csv")
juri.name = "juri"

def convert_list(table):
    numpee = table.to_numpy().tolist()
    table = [[], []]
    for idx, unit, guarenteed in numpee:
        b = "B" in idx 
        table[b].append((idx, unit, guarenteed))
    return table 

cammy = convert_list(cammy)
juri = convert_list(juri)

units = set(['Blanka', 'Juri', 'Luke', 'Ken', 'M. Bison', 'Sagat', 'Dhalsim', 'E. Honda', 'Chun-Li', 'Cammy', 'Sakura', 'Balrog', 'Ryu', 'Vega', 'Guile', 'Zangief', 'Zangief Cat', 'Jamie Cat', 'M. Bison Cat', 'Sagat Cat', 'Vega Cat', 'Balrog Cat', 'Akuma'])
rares = ['Zangief Cat', 'Jamie Cat', 'M. Bison Cat', 'Sagat Cat'] # needed for dupe checking 

@cache
def get_choices(roll_num, track, previous):
    track_no = f"{roll_num}{"B" if track else "A"}"
    cammy_track_unit = cammy[track][roll_num - 1][1]
    juri_track_unit = juri[track][roll_num - 1][1]

    # print("Two options:", cammy_track_unit, juri_track_unit)
    # if "Mer-Cat" in res: print("hello world")
    if cammy_track_unit == juri_track_unit: # not much of a choice 
        if previous == cammy_track_unit and (cammy_track_unit in rares or cammy_track_unit not in units):
            return [(cammy_track_unit, "cammy")], not track # switch tracks because of dupe - might not be the correct thing 
        return [(cammy_track_unit, "cammy")], track # stay on same track 
    
    # thus the two options are different 
    s = []
    if cammy_track_unit in units:
        s.append((cammy_track_unit, "cammy"))

    if juri_track_unit in units:
        s.append((juri_track_unit, "juri"))
    return s, track # else return only interesting units, but stay on track 

# have to check the logic for previous dupe or not. 
@cache
def peek_guarenteed(roll_num, track, table):
    if table: table = cammy 
    else: table = juri
    guarenteed_uber = table[track][roll_num - 1][2]
    return guarenteed_uber

@cache
def eleven(roll_num, track, previous, table):
    if table: table = cammy 
    else: table = juri
    track_no = f"{roll_num}{"B" if track else "A"}"

    guarenteed_uber = table[track][roll_num - 1][2]

    tmp_path = []
    for tmp_roll_num in range(10):
        track_no = f"{roll_num + tmp_roll_num}{"B" if track else "A"}"
        table_unit = table[track][roll_num + tmp_roll_num - 1][1]

        if previous == table_unit and (table_unit in rares or table_unit not in units):
            track = not track # not entirely accurate, consider this a "wasted" roll 
            tmp_path.append((track_no, f"TRACK SWITCH:{table_unit}"))
        else: 
            tmp_path.append((track_no, table_unit))
        previous = table_unit
    # and now get the guarenteed uber  
    tmp_path.append(("Guaranteed", guarenteed_uber))
    track = not track # switch tracks at end ofc 

    return tmp_path, track, previous 

mx = 0 
metadata = {"best_path": [], "units_collected": [], "previous_unit": "", "last_track_no": ""}
roll_limit = 20 # largest is 107  
# ['Guile', 'Balrog', 'Sagat Cat', 'Cammy', 'Vega Cat', 'Vega', 'Balrog Cat']
#  ['Vega Cat', 'Balrog Cat', 'Jamie Cat', 'Sagat Cat', 'Cammy', 'Balrog']
last_track_no = [""]
moves = 0 

def reset_globals(new_roll_lim):
    global roll_limit
    global mx 
    global metadata
    mx = 0
    roll_limit = new_roll_lim
    metadata = {"best_path": [], "units_collected": [], "previous_unit": "", "last_track_no": ""}

def recurse(roll_num, track, previous, units_collected, current_path):
    global mx 
    global moves
    moves += 1
    if moves % 1000000 == 0: print("Moves", moves)
    if moves > 150000000: return 
    if roll_num >= roll_limit:
        score = len(units_collected)
        if score > mx: 
            mx = score 
            metadata["best_path"] = [i for i in current_path]
            metadata["units_collected"] = [i for i in units_collected]
            track_no = f"{roll_num}{"B" if track else "A"}"
            print("Track ptr:", track_no)
            metadata["last_track_no"] = track_no
            metadata["previous_unit"] = previous

        return 
    if len(units_collected) > mx:
        mx = len(units_collected) 
        metadata["best_path"] = [i for i in current_path]
        metadata["units_collected"] = [i for i in units_collected]
        track_no = f"{roll_num}{"B" if track else "A"}"
        print("Track ptr:", track_no)
        metadata["last_track_no"] = track_no
        metadata["previous_unit"] = previous
    if mx >= 23: return # stop immediately 
    # basically, you can do a 1 pull, or you can do an 11 pull
    # and you can do it on table A or table B
    # globals: roll number, track, previous_unit 
    # total_units
    # current_path 
    # when doing a draw one, you're faced between two choices. If both are collected, pick track A (arb)
    # if one is collected, pick the other one
    # if neither is collected, choose either 
    # for dupes, thats why we have a previous one. 

    # simulate one roll 
    choices, new_track = get_choices(roll_num, track, previous)
    for unit, table_name in choices:
        flg = False 
        if unit in units and unit not in units_collected:
            units_collected.add(unit)
            flg = True 
        track_no = f"{roll_num}{"B" if track else "A"}"

        current_path.append(("1R", track_no, unit, table_name))
        recurse(roll_num + 1, new_track, unit, units_collected, current_path)
        if flg: units_collected.remove(unit)
        current_path.pop()
    
    # simulate guarenteed eleven roll, aka 10 normal rolls and 1 guarenteed uber with track switch. Do this on both tracks 


    uber = peek_guarenteed(roll_num, track, True)
    if uber not in units_collected: 
        tmp_path = []
        units_added = []
        tmp_path, new_track, new_previous = eleven(roll_num, track, previous, True)
        for track_no, unit in tmp_path:
            if unit in units and unit not in units_collected:
                units_collected.add(unit)
                units_added.append(unit)

        track_no = f"{roll_num}{"B" if track else "A"}"

        current_path.append(("Eleven Roll (Cammy)", track_no, tmp_path))

        # I think only + 10 because of how A and B are related. 
        # like its a weird relationship where A -> B means +10, but B to A means +11 
        recurse(roll_num + 10 + (0 if new_track else 1), new_track, new_previous, units_collected, current_path)
        # roll back changes 
        for unit in units_added:
            units_collected.remove(unit)
        current_path.pop() 

    uber = peek_guarenteed(roll_num, track, False)
    if uber not in units_collected: # trimming the kirbs 
        tmp_path = []
        units_added = []
        tmp_path, new_track, new_previous = eleven(roll_num, track, previous, False)
        for track_no, unit in tmp_path:
            if unit in units and unit not in units_collected:
                units_collected.add(unit)
                units_added.append(unit)

        track_no = f"{roll_num}{"B" if track else "A"}"

        current_path.append(("Eleven Roll (Juri)", track_no, tmp_path))

        # I think only + 10 because of how A and B are related. 
        # like its a weird relationship where A -> B means +10, but B to A means +11 
        recurse(roll_num + 10 + (0 if new_track else 1), new_track, new_previous, units_collected, current_path)
        # roll back changes 
        for unit in units_added:
            units_collected.remove(unit)
        current_path.pop() 

def main():
    curr_roll = 1 
    curr_path = []
    already_have = set() 

    previous = "N/A"
    track = False 
    # og call: recurse(curr_roll, False, "N/A", set(), [])

    # ['Sagat Cat', 'Sagat', 'Vega', 'Jamie Cat', 'Vega Cat', 'Balrog', 'Guile', 'Juri', 'Balrog Cat', 'Cammy']
    # get_choices(1, True, "idk")
    for _ in range(3):

        recurse(curr_roll, track, previous, already_have, [])
        print("Best score:", len(metadata["units_collected"]))
        print("Found units:",metadata["units_collected"])
        for unit in metadata["units_collected"]:
            already_have.add(unit)
        # already_have = set(metadata["units_collected"])
        last_roll_num, last_track = metadata["last_track_no"][:-1], metadata["last_track_no"][-1]
        print(last_roll_num, last_track)

        last_track = "B" == last_track
        track = last_track
        last_roll_num = int(last_roll_num)
        curr_roll = last_roll_num

        curr_path.extend(metadata["best_path"])


        reset_globals(curr_roll + roll_limit)

    print("Overall Performance:")
    print("==" * 20)
    print("Best score:", len(already_have))
    print("Found units:", already_have)


    for line in curr_path:
        # ("*" if unit in units else "")
        print(line)
    print("Steps taken:", moves)

if __name__ == "__main__":
    main()