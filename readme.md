This is a tool built as an addition to BC Godfat's Battle Cats Seed Tracker. Currently implemented for Street Fighters collab, but it shoudl work for general gacha sets. 

Essentially, it is a brute force tool for determining an optimal path (among finite checked paths) for completing a gacha collection

We should find a better way to store units to collect, and extract units to collect. 

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
