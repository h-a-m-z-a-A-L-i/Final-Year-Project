# Code Test 2 — Fix Error Cell — Full Report

- **Session:** `fyp-CODE_TEST_2_FIX_ERROR_112732919-CODE_TEST_2_FIX_ERROR-9e257fcf`
- **Started:** 2026-06-16T11:27:58.462865+00:00

## Prompt

```
Fix the NameError in cell 31.

Provide:
- Placement guidance (fill cell 31 or insert elsewhere with evidence)
- A single runnable python code block for the corrected cell

Do not dispatch browser tools.
```

## Contract compliance

- **No Browser Tool Dispatch:** True
- **No Notebook Writes:** True
- **Has Placement Guidance:** True
- **Has Python Code Block:** True
- **Single Primary Code Block:** True
- **Acknowledges Empty Cell:** True
- **Defers Code For Empty Cell:** True
- **Requires Code Block:** True

## Model response

```
### Placement
Fill **Cell [31]**. The code is syntactically correct; the `NameError` occurs because `df` is not defined in the kernel memory. You must run **Cell [25]** (which defines `df`) before running Cell [31].

### Run steps
1. Run **Cell [25]** to load the dataset into `df`.
2. Run **Cell [31]** to execute the filtering and display logic.

### Code
```python
# Filter for Lahore properties and get top 10 by price
lahore_properties = df[df['city'] == 'Lahore'].copy()
top_10_lahore = lahore_properties.nlargest(10, 'price')

# Display relevant columns
print(top_10_lahore[['price', 'location', 'property_type', 'bedrooms', 'baths', 'area']])
```
```

_Generated 2026-06-16T11:33:27.291461+00:00_
