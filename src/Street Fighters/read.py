import pandas as pd
import csv

df = pd.read_csv("data.csv", header=0)
# print(df)

# for i, col in enumerate(df.columns):
#     print(i, repr(col))


left = df.iloc[:, 0:6]
right = df.iloc[:, 7:13]

def clean_data(table, filename):
    cols = ["No", "Result", "Guaranteed", "Alt Result", "Alt Guaranteed", "Alt No"]

    table.columns = cols

    # working with table only for now. 
    table.loc[table["No"].isnull(), "No"] = table["Alt No"]
    table.loc[table["Result"].isnull(), "Result"] = table["Alt Result"]
    table.loc[table["Guaranteed"].isnull(), "Guaranteed"] = table["Alt Guaranteed"]
    table["Guaranteed"] = table["Guaranteed"].str.replace(
        r"^\s*[<>-]+\s*\w+\s*|\s*[<>-]+\s*\w+$",
        "",
        regex=True
    )
    table["Guaranteed"] = table["Guaranteed"].str.strip()


    del table["Alt No"]
    del table["Alt Result"]
    del table["Alt Guaranteed"]

    # shift up by 1 ig  
    table[["Result", "Guaranteed"]] = table[["Result", "Guaranteed"]].shift(-1)
    table["Result"] = table["Result"].str[:-1]
    table["Guaranteed"] = table["Guaranteed"].str[:-1]

    # read "errors" aka, path switches because of duplicates 
    # aka where the No is null 
    table.loc[table["No"].isnull(), "No"] = "DUPLICATE TRACK SWITCH"
    # print(table.loc[table["No"].isnull()])
    print(table.head())

    table.to_csv(filename, index=False)

clean_data(left, "Cammy_Draw.csv")
clean_data(right, "Juri_Draw.csv")