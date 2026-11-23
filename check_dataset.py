import pandas as pd

df = pd.read_csv("D:\\smart food tracker_backup\\smart_eat\\nutrients_clean_final.csv")

print("Total records:", len(df))
print("Total columns:", len(df.columns))

print("\nColumn names:")
print(df.columns.tolist())

print("\nFirst 5 records:")
print(df.head())