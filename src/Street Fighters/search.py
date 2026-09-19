"""
Simple search python script to search for units in street fighter collabs
"""


import pandas as pd 

cammy = pd.read_csv("Cammy_Draw.csv")
juri = pd.read_csv("Juri_Draw.csv")
# print(df.head())

def find(table, unit):
    res = table.loc[table["Result"].str.contains(unit, na=False) | table["Guaranteed"].str.contains(unit, na=False)]
    return res 

def find_natural(table, unit, cat_form):
    if cat_form:
        res = table.loc[table["Result"].str.contains(unit, na=False) & ~ table["Result"].str.contains(f"{unit} Cat", na=False)]
    else: 
        res = table.loc[table["Result"].str.contains(unit, na=False)]
    return res 

find(cammy, "Bath Cat")

units = ['Blanka', 'Juri', 'Luke', 'Ken', 'M. Bison', 'Sagat', 'Dhalsim', 'E. Honda', 'Chun-Li', 'Cammy', 'Sakura', 'Balrog', 'Ryu', 'Vega', 'Guile', 'Zangief', 'Zangief Cat', 'Jamie Cat', 'M. Bison Cat', 'Sagat Cat', 'Vega Cat', 'Balrog Cat', 'Akuma']

for unit in units:
    print("==" * 20, unit.upper(), "==" * 20)
    cat_form = False 
    if f"{unit} Cat" in units: 
        cat_form = True 

    res1 = find_natural(cammy, unit, cat_form)
    res2 = find_natural(juri, unit, cat_form)
    print(res1)
    print(res2)