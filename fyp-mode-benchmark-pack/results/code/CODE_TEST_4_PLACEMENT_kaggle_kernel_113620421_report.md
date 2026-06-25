# Code Test 4 — Placement Guidance — Full Report

- **Session:** `fyp-CODE_TEST_4_PLACEMENT_113620421-CODE_TEST_4_PLACEMENT-724d689f`
- **Started:** 2026-06-16T11:27:58.462865+00:00

## Prompt

```
Where should I insert a new cell to print validation RMSE and MAE after model training?

Provide placement guidance with evidence from the notebook flow.

Include a runnable python code block for the new cell.
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
### Plan
- The current notebook context (Cells 1–5) shows data loading (`df`) and a copy (`df1`), but **no model training or prediction code** is visible yet.
- To print validation RMSE and MAE, you must first have a trained model and predictions (e.g., `y_pred`) in memory.
- I will provide the code to calculate these metrics using `sklearn`.

### Placement
Since the model training cell is not in the provided context (Cells 1–5), I cannot give a specific index like "below Cell [6]".
- **Insert a new Code cell immediately after** the cell where you generate predictions (where `y_pred` is assigned).
- Ensure the training cell and prediction cell have been **Run** so that `y_test` (or `y_val`) and `y_pred` exist in the kernel.

### Code
```python
from sklearn.metrics import mean_squared_error, mean_absolute_error
import numpy as np

# Ensure you have y_true (actual values) and y_pred (predicted values) from the previous cell
# Example: y_true = y_test or y_val
# Example: y_pred = model.predict(X_test) or model.predict(X_val)

rmse = np.sqrt(mean_squared_error(y_true, y_pred))
mae = mean_absolute_error(y_true, y_pred)

print(f"Validation RMSE: {rmse}")
print(f"Validation MAE: {mae}")
```

### Notes
- **Variable Names:** Replace `y_true` with your actual target variable name (e.g., `y_test`, `y_val`) and `y_pred` with your prediction variable name.
- **Dependencies:** This code requires `scikit-learn`. If you haven't imported it yet, the `from sklearn.metrics...` line in the block above handles it.
```

_Generated 2026-06-16T11:33:27.297462+00:00_
