# Agent Role and Context
You are assisting with a Master's thesis. Consistency in visual presentation is strictly required. 
Whenever you generate, modify, or suggest code for data visualizations in either Python or R, you MUST adhere to the following strict styling rules to ensure all plots look identical and match the thesis text.

# Project Management (Python)
- **Dependency Management:** All Python scripts MUST be executed using `uv run`. 
- **Dependencies:** Do not suggest random `pip install` commands. If a new package is needed, instruct the user to add it to the project root dependencies using `uv add <package_name>`.

# Global Plotting Rules
1. **Style:** All plots must use a clean, white background with subtle grid lines.
2. **Font Size:** Base font size should be 11pt to match the thesis text.
3. **Colors:** Use colorblind-friendly palettes by default unless instructed otherwise.

---

# Python Strict Implementation (Matplotlib / Seaborn)
For Python visualizations, ALWAYS configure Matplotlib to use LaTeX for text rendering and Seaborn for the whitegrid style. Include this boilerplate at the top of your plotting scripts:

```python
import matplotlib.pyplot as plt
import seaborn as sns

# 1. Set the whitegrid style FIRST
sns.set_theme(style="whitegrid")

# 2. Force Matplotlib to use LaTeX for all text rendering
plt.rcParams.update({
    "text.usetex": True,
    "axes.labelsize": 11,
    "font.size": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10
})
```

# R Strict Implementation (ggplot2)
For R visualizations, ALWAYS use `theme_minimal()` for the clean white-background style. Include this boilerplate before generating plots:

```r
library(ggplot2)

# Add this theme to EVERY ggplot
my_thesis_theme <- theme_minimal(base_size = 11) +
  theme(
    plot.title = element_text(size = 12),
    axis.title = element_text(size = 11),
    axis.text = element_text(size = 10),
    legend.text = element_text(size = 11)
  )

# Example usage: ggplot(...) + geom_point() + my_thesis_theme
```

# Exceptions
Do NOT deviate from these settings unless explicitly told by the user to "ignore thesis styling for this plot."
