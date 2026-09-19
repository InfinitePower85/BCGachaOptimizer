This is a tool built as an addition to BC Godfat's Battle Cats Seed Tracker. Currently implemented for Street Fighters collab, but it shoudl work for general gacha sets. 

Essentially, it is a brute force tool for determining an optimal path (among finite checked paths) for completing a gacha collection

We should find a better way to store units to collect, and extract units to collect. 

Also we are working towards the following features:

1. Dataset fetching (from BC Godfat) - Ensure not too many request made at once, as BC Godfat is a small fan website. 
2. Battle Cats Wiki fetching for Gacha units. 
    - note that the gacha should be backend stuff, just to populate an internal data store / data table every so often. Not to be triggered by the user. 
    - We must organize how the data will be stored. 
3. Ability to specify whether a unit is collected or not. 

4. UI for the cli tool, maybe a website or HTML. Will figure out what to do for this
    - ex: select gacha pool to use
    - ex: select seed to use
5. Unit tests to check for correctness of impl / simulation 

Intricacies of how Battle Cats rolls are done: 
1. Two types of rolls:
    - Single Roll (1 rare ticket)
    - Guarenteed 11 Roll (1500 cat food)
        - Guarenteed 11 roll switches the track to whatever other track it is
        - Also gets the uber on the 11th roll instead of whatever unit was already there
        - switch happens AFTER the 11th unit uber rare is gotten, but before the next roll, so the next roll will be on the switched track. 
2. Track switches: When two of the same rare cat are collected in a row, the second rare cat will be replaced AND switch the track. 
    - the replacement rare cat will be collected, and then the track is switched
    - note that it is possible to cancel if another gacha event is going on by switching to the other gacha, and collecting a different rare cat instead
        - ignore this case for the sake of the simulation, since another gacha event is not always guarenteed to have a different rare cat
