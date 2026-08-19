---
name: data-analysis
description: Python data analysis patterns and templates
---

# Data Analysis Skill

## Quick Statistics (stdlib only)
```python
import statistics
data = [...]
print(f"Mean: {statistics.mean(data):.2f}")
print(f"Median: {statistics.median(data):.2f}")
```

## CSV Analysis
```python
import csv
from collections import Counter
with open('data.csv') as f:
    rows = list(csv.DictReader(f))
groups = Counter(row['category'] for row in rows)
```

## Output Guidelines
- Present numbers with context
- Use markdown tables for comparisons
- Highlight anomalies and trends
