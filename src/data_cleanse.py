import re 

pat = r"<- \d+[AB]"
pat2 = r"\d+[AB]"
data = []
with open("tst.txt", 'r', encoding='utf-8') as rdr:
    for line in rdr:
        # print("LINE:", line.strip())
        data.append(line.strip())

data = data[2:] # skip first two lines. Note that the data is not organized well. 
lines = []
for line in data:
    # print(line.split("🐾"))
    lines.append([i.strip() for i in line.split("🐾")][:2])

# for line in lines:
#     print(line)
# odd parity lines need to be dealt with 
print(len(lines))
even_odd = [[-1, -1, -1, -1] for i in range(len(lines) // 2 + 1)]
# newline = [[] for i in range()]
for idx, line in enumerate(lines):
    # print("Line ->", line)
    # if idx % 2:
    #     fix_first = line[0].split('\t')[-1]
    #     print(fix_first)
    #     new_line = []
    cleansed_line = [] 
    for element in line: 
        digits = re.findall(pat, element)
        if digits: 
            cleansed_element = re.split(pat, element)[1]
            cleansed_line.append(re.split(pat, element)[1])
        else:
            digits = re.findall(pat2, element)
            if digits: 
                cleansed_element = re.split(pat2, element)[1]
                cleansed_line.append(re.split(pat2, element)[1])
            else: cleansed_line.append(element) # no pattern matched, good 

    for cleansed_idx, val in enumerate(cleansed_line):
        even_odd[idx // 2 + idx % 2][(idx % 2) * 2 + cleansed_idx] = val
    # even_odd[idx // 2].extend(cleansed_line[::-1])
    # even_odd[idx // 2][idx % 2] = line

for line in even_odd[:10]:
    print(line)